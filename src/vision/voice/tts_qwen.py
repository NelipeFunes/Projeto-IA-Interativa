"""Qwen3-TTS como motor de voz (`voz.motor: qwen3`), num PROCESSO SEPARADO (vision/voice/qwen_worker.py).

Por que outro processo: o Qwen3-TTS exige `transformers` 5.15.1 e o XTTS exige menor que 5, então não cabem no mesmo
ambiente; e matar o processo no modo jogo devolve a placa inteira (o XTTS só consegue mover o modelo para a RAM).
Medido em 16/10 (docs/melhorias/voz-xtts.md): com a mesma referência e o mesmo tratamento do XTTS, 16 letras/s sem
acelerar, ~1% de pausa, timbre 0,73 (XTTS: 0,68) e fala 1,8x mais rápido que o tempo real (XTTS: 1,2x).

A interface é a mesma do `VozXTTS` (o laço de voz usa as duas igual): `pedacos`, `sintetizar`, `descansar`/`acordar`,
`reserva`, `clonada`."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import struct
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from vision.voice.falado import para_fala
from vision.voice.pausas import PAUSA_MAX_S, CompressorDePausas, preparar_texto
from vision.voice.tts import LIMITE_XTTS, dividir

log = logging.getLogger(__name__)

TRABALHADOR = Path(__file__).with_name("qwen_worker.py")
CABECALHO = struct.Struct("<cII")  # tipo, id do pedido, tamanho da carga (o mesmo do qwen_worker.py)
PICO = 0.98


class ErroDaVoz(RuntimeError):
    """O processo do Qwen3-TTS não subiu, caiu ou travou. O laço de voz fala o resto com a reserva (Piper)."""


def _ler_exato(arquivo, n: int) -> bytes | None:
    dados = b""
    while len(dados) < n:
        parte = arquivo.read(n - len(dados))
        if not parte:
            return None
        dados += parte
    return dados


class VozQwen:
    taxa = 24000
    clonada = True  # sempre a mesma voz: nunca vai para o Piper numa resposta curta
    rotulo = "Qwen3-TTS"

    def __init__(self, python: Path, modelo: Path, referencia: Path, reserva, *, idioma: str = "Portuguese",
                 pausa_max_s: float | None = PAUSA_MAX_S, tirar_virgulas: bool = True, ganho: float = 1.0,
                 pedaco: int = 8, so_timbre: bool = False, log_do_processo: Path | None = None,
                 partida_max_s: float = 180.0, prazo_s: float = 30.0, espera_reinicio_s: float = 30.0,
                 trabalhador: Path = TRABALHADOR):
        for rotulo, arquivo in (("o Python do ambiente da voz", python), ("o modelo do Qwen3-TTS", modelo),
                                ("a gravação de referência", referencia),
                                ("a transcrição da gravação (.txt, com o texto exato dela)", referencia.with_suffix(".txt"))):
            if not arquivo.exists():
                raise FileNotFoundError(f"não achei {rotulo}: {arquivo}")
        self.python, self.modelo, self.referencia = python, modelo, referencia
        self.reserva = reserva
        self.falante = referencia.stem
        self.idioma, self.pedaco, self.so_timbre = idioma, pedaco, so_timbre
        self.pausa_max_s, self.tirar_virgulas, self.ganho = pausa_max_s, tirar_virgulas, ganho
        self.velocidade = 1.0  # o Qwen3-TTS não tem controle de velocidade
        self.descansando = False
        self._log_do_processo = log_do_processo
        self._partida_max_s, self._prazo_s, self._espera_reinicio_s = partida_max_s, prazo_s, espera_reinicio_s
        self._trabalhador = trabalhador
        self._trava = threading.Lock()  # uma síntese por vez; descansar/acordar esperam a frase terminar
        self._proc: subprocess.Popen | None = None
        self._fila: queue.Queue = queue.Queue()
        self._pedido = 0
        self._falhou_em = -1e9
        self._iniciar()
        atexit.register(self.fechar)

    # ---------- o processo ----------

    def _iniciar(self) -> None:
        comando = [str(self.python), "-I", str(self._trabalhador), "--modelo", str(self.modelo), "--referencia", str(self.referencia),
                   "--texto-referencia", str(self.referencia.with_suffix(".txt")), "--idioma", self.idioma]
        if self.so_timbre:
            comando.append("--so-timbre")
        saida_de_erro = subprocess.DEVNULL
        if self._log_do_processo is not None:
            self._log_do_processo.parent.mkdir(parents=True, exist_ok=True)
            saida_de_erro = open(self._log_do_processo, "ab")  # noqa: SIM115 - fica aberto enquanto o processo vive
        proc = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=saida_de_erro,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if saida_de_erro is not subprocess.DEVNULL:
            saida_de_erro.close()  # o processo já herdou o arquivo
        fila: queue.Queue = queue.Queue()
        threading.Thread(target=self._ler, args=(proc, fila), daemon=True).start()
        try:
            item = fila.get(timeout=self._partida_max_s)
        except queue.Empty:
            proc.kill()
            raise ErroDaVoz(f"o Qwen3-TTS não ficou pronto em {self._partida_max_s:.0f} s") from None
        if item is None or item[0] == b"E":
            motivo = item[2].decode("utf-8", "replace") if item else "o processo saiu sem dizer por quê"
            proc.kill()
            raise ErroDaVoz(f"o Qwen3-TTS não subiu: {motivo}")
        if item[0] != b"P":
            proc.kill()
            raise ErroDaVoz(f"resposta inesperada do Qwen3-TTS ao subir: {item[0]!r}")
        self._proc, self._fila = proc, fila
        log.info("Qwen3-TTS pronto (processo %s)", proc.pid)

    @staticmethod
    def _ler(proc: subprocess.Popen, fila: queue.Queue) -> None:
        """Na thread: os quadros do processo vão para a fila; None = o processo fechou o stdout."""
        try:
            while (cab := _ler_exato(proc.stdout, CABECALHO.size)) is not None:
                tipo, pedido, tamanho = CABECALHO.unpack(cab)
                carga = _ler_exato(proc.stdout, tamanho) if tamanho else b""
                if carga is None:
                    break
                fila.put((tipo, pedido, carga))
        except (OSError, ValueError):
            pass
        finally:
            fila.put(None)

    def _enviar(self, msg: dict) -> None:
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("ascii"))  # ASCII: o texto vai escapado (\uXXXX)
            self._proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            self._falhou_em = time.monotonic()
            self._parar()
            raise ErroDaVoz(f"não consegui falar com o processo do Qwen3-TTS: {e}") from e

    def _parar(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.write(b'{"op": "sair"}\n')
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _garantir_processo(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if time.monotonic() - self._falhou_em < self._espera_reinicio_s:
            raise ErroDaVoz("o processo do Qwen3-TTS caiu há pouco; falando com a reserva por enquanto")
        self._parar()
        try:
            self._iniciar()
        except BaseException:
            self._falhou_em = time.monotonic()
            raise

    def fechar(self) -> None:
        """Encerra o processo (também roda ao sair do Vision; se ele morrer, o processo acaba sozinho: stdin fecha)."""
        if self._trava.acquire(timeout=3):
            try:
                self._parar()
            finally:
                self._trava.release()
        else:  # uma frase travada: não espera
            proc, self._proc = self._proc, None
            if proc is not None:
                proc.kill()

    # ---------- a voz ----------

    def definir_velocidade(self, _velocidade_fala: float) -> None:
        """A velocidade da tela de Ajustes não vale aqui (o Qwen3-TTS não tem controle de velocidade)."""

    def _trechos(self, texto: str) -> list[str]:
        return [t for t in (preparar_texto(p, self.tirar_virgulas) for p in dividir(texto, LIMITE_XTTS)) if t]

    def _volume(self, audio) -> np.ndarray:
        return np.clip(np.asarray(audio, dtype=np.float32) * self.ganho, -PICO, PICO)

    def _pedir(self, trecho: str) -> Iterator[np.ndarray]:
        self._pedido += 1
        pedido = self._pedido
        self._enviar({"op": "falar", "id": pedido, "texto": trecho, "pedaco": self.pedaco})
        terminou = False
        try:
            while True:
                try:
                    item = self._fila.get(timeout=self._prazo_s)
                except queue.Empty:
                    raise ErroDaVoz(f"o Qwen3-TTS ficou {self._prazo_s:.0f} s sem responder") from None
                if item is None:
                    terminou = True
                    self._falhou_em = time.monotonic()  # não reinicia a cada frase se ele cair sempre
                    self._parar()  # limpa o processo morto (o `poll` demora a notar); o próximo pedido decide se reinicia
                    raise ErroDaVoz("o processo do Qwen3-TTS caiu no meio da fala")
                tipo, id_, carga = item
                if id_ != pedido:
                    continue  # sobra de um pedido cancelado
                if tipo == b"A":
                    yield np.frombuffer(carga, dtype="<f4")
                elif tipo == b"F":
                    terminou = True
                    return
                elif tipo == b"E":
                    terminou = True
                    raise ErroDaVoz(carga.decode("utf-8", "replace"))
        finally:
            if not terminou:
                self._cancelar(pedido)

    def _cancelar(self, pedido: int) -> None:
        """A frase foi cortada (falou por cima, standby): manda parar e limpa a fila até o fim do pedido."""
        try:
            self._enviar({"op": "cancelar", "id": pedido})
            limite = time.monotonic() + 5
            while time.monotonic() < limite:
                item = self._fila.get(timeout=max(limite - time.monotonic(), 0.01))
                if item is None or (item[1] == pedido and item[0] in (b"F", b"E")):
                    return
        except (ErroDaVoz, queue.Empty):
            pass
        log.warning("o Qwen3-TTS não parou ao cancelar; reiniciando o processo")
        self._parar()  # o próximo pedido sobe outro

    def pedacos(self, texto: str, normalizar: bool = True) -> Iterator[np.ndarray]:
        """Streaming, como o `VozXTTS.pedacos`: consuma na mesma thread e feche o gerador (`close`) se parar no meio."""
        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return
        with self._trava:
            if self.descansando:
                from vision.voice.stt import reamostrar

                yield reamostrar(self.reserva.sintetizar(texto, normalizar=False), self.reserva.taxa, self.taxa)
                return
            self._garantir_processo()
            for trecho in self._trechos(texto):
                compressor = CompressorDePausas(self.taxa, self.pausa_max_s) if self.pausa_max_s else None
                for pedaco in self._pedir(trecho):
                    audio = self._volume(pedaco)
                    if compressor:
                        audio = compressor.processar(audio)
                    if audio.size:
                        yield audio
                if compressor:
                    resto = compressor.terminar()
                    if resto.size:
                        yield resto

    def sintetizar(self, texto: str, normalizar: bool = True) -> np.ndarray:
        partes = list(self.pedacos(texto, normalizar))
        return np.concatenate(partes) if partes else np.zeros(0, dtype=np.float32)

    # ---------- modo jogo ----------

    def descansar(self) -> None:
        """Modo jogo: encerra o processo (a placa volta inteira) e passa a falar com a `reserva` (Piper)."""
        with self._trava:
            if not self.descansando:
                self._parar()
                self.descansando = True

    def acordar(self) -> None:
        """Volta o processo (~10 s). Se não subir, continua descansando e levanta: o laço tenta de novo depois."""
        with self._trava:
            if not self.descansando:
                return
            self._iniciar()
            self.descansando = False
