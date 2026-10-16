"""Qwen3-TTS como motor de voz (`voz.motor: qwen3`), num PROCESSO SEPARADO (vision/voice/qwen_worker.py).

Por que outro processo: o Qwen3-TTS exige `transformers` 5.15.1 e o XTTS exige menor que 5, então não cabem no mesmo
ambiente; e matar o processo no modo jogo devolve a placa inteira (o XTTS só consegue mover o modelo para a RAM).
Medido em 16/10 como roda no Vision (docs/melhorias/voz-xtts.md): 15,2 letras/s sem acelerar, 6,7% de pausa, timbre 0,76
(XTTS: 0,66), fala 2x mais rápido que o tempo real (XTTS: 1,1x) e nenhuma parada em 20 áudios.

A interface é a mesma do `VozXTTS` (o laço de voz usa as duas igual): `pedacos`, `sintetizar`, `descansar`/`acordar`,
`reserva`, `clonada`.

Subir o processo leva 14 a 22 s e NUNCA segura a fala: depois de uma queda ou do modo jogo, o que for falado nesse tempo sai
pela reserva (Piper) e a voz volta sozinha quando o processo fica pronto."""

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

from vision import envs
from vision.arquivos import limitar_log
from vision.voice.falado import para_fala
from vision.voice.pausas import LIMIAR_SILENCIO, PAUSA_MAX_S, CompressorDePausas, preparar_texto
from vision.voice.tts import LIMITE_XTTS, dividir

log = logging.getLogger(__name__)

TRABALHADOR = Path(__file__).with_name("qwen_worker.py")
CABECALHO = struct.Struct("<cII")  # tipo, id do pedido, tamanho da carga (o mesmo do qwen_worker.py)
TIPOS_DE_QUADRO = (b"P", b"A", b"F", b"E")
CARGA_MAXIMA = 4 * 1024 * 1024  # um pedaço de 8 passos tem ~64 KB; um tamanho absurdo é um fluxo estragado, não áudio
PICO = 0.98
# O que o processo recebe: o básico do Windows (envs.BASICAS) e isto. Nada de senha do Orbit, chave da Tavily etc. (o
# processo importa ~90 pacotes de terceiros e não precisa de nenhum segredo). Sem rede (o modelo é local) e sem procurar
# programa na pasta atual: o pacote `sox` roda `cmd /c sox` ao ser importado.
AMBIENTE_DO_PROCESSO = {
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "NoDefaultCurrentDirectoryInExePath": "1", "PYTHONUTF8": "1",
    "CUDA_VISIBLE_DEVICES": "$CUDA_VISIBLE_DEVICES",
}


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


def _matar(proc: subprocess.Popen) -> None:
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


class VozQwen:
    taxa = 24000
    clonada = True  # sempre a mesma voz: nunca vai para o Piper numa resposta curta
    rotulo = "Qwen3-TTS"

    def __init__(self, python: Path, modelo: Path, referencia: Path, reserva, *, idioma: str = "Portuguese",
                 pausa_max_s: float | None = PAUSA_MAX_S, tirar_virgulas: bool = False, ganho: float = 1.0,
                 pedaco: int = 8, so_timbre: bool = False, log_do_processo: Path | None = None,
                 cwd: Path | None = None, partida_max_s: float = 180.0, prazo_s: float = 30.0,
                 espera_reinicio_s: float = 30.0, trabalhador: Path = TRABALHADOR,
                 ambiente_extra: dict[str, str] | None = None):
        for rotulo, arquivo in (("o Python do ambiente da voz", python), ("o modelo do Qwen3-TTS", modelo),
                                ("a gravação de referência", referencia),
                                ("a transcrição da gravação (.txt, com o texto exato dela)", referencia.with_suffix(".txt"))):
            if not arquivo.exists():
                raise FileNotFoundError(f"não achei {rotulo}: {arquivo}")
        self.python, self.modelo, self.referencia = python, modelo, referencia
        # A cópia nivelada da gravação fica ao lado dela, em data/voz (a mesma proteção: fora do git), e é sobrescrita a cada
        # partida; antes ia para uma pasta temporária nova e ficava esquecida lá (revisão do PR 68).
        self._nivelada = referencia.with_name(f".{referencia.stem}-nivelada.wav")
        self.reserva = reserva
        self.falante = referencia.stem
        self.idioma, self.pedaco, self.so_timbre = idioma, pedaco, so_timbre
        self.pausa_max_s, self.tirar_virgulas, self.ganho = pausa_max_s, tirar_virgulas, ganho
        self.velocidade = 1.0  # o Qwen3-TTS não tem controle de velocidade
        self.descansando = False
        self._log_do_processo = log_do_processo
        self._cwd = cwd or python.parent
        self._partida_max_s, self._prazo_s, self._espera_reinicio_s = partida_max_s, prazo_s, espera_reinicio_s
        self._trabalhador = trabalhador
        self._ambiente_extra = ambiente_extra if ambiente_extra is not None else {}  # só os testes usam (o config não expõe)
        self._trava = threading.Lock()  # uma síntese por vez; descansar espera a frase terminar
        self._estado = threading.Lock()  # protege o que muda de mão: _proc, _fila, _subindo, _epoca, _falhou_em, _fechado
        self._proc: subprocess.Popen | None = None
        self._fila: queue.Queue = queue.Queue()
        self._pedido = 0
        self._falhou_em = -1e9
        self._epoca = 0  # sobe a cada descansar/fechar: uma partida que começou antes disso é jogada fora ao chegar
        self._subindo = False
        self._fechado = False
        self._proc, self._fila = self._iniciar_processo()  # a 1ª vez é síncrona: se não subir, o Vision fala com o Piper
        atexit.register(self.fechar)

    # ---------- o processo ----------

    def _iniciar_processo(self) -> tuple[subprocess.Popen, queue.Queue]:
        """Sobe o processo e espera o `P` (14 a 22 s). Não mexe no estado da voz: quem chama decide o que fazer com ele."""
        comando = [str(self.python), "-I", str(self._trabalhador), "--modelo", str(self.modelo), "--referencia", str(self.referencia),
                   "--texto-referencia", str(self.referencia.with_suffix(".txt")), "--nivelada-em", str(self._nivelada),
                   "--idioma", self.idioma]
        if self.so_timbre:
            comando.append("--so-timbre")
        saida_de_erro = subprocess.DEVNULL
        if self._log_do_processo is not None:
            self._log_do_processo.parent.mkdir(parents=True, exist_ok=True)
            limitar_log(self._log_do_processo)
            saida_de_erro = open(self._log_do_processo, "ab")  # noqa: SIM115 - o processo herda; fechamos o nosso já a seguir
        try:
            proc = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=saida_de_erro,
                                    env={**envs.para_servidor_mcp(AMBIENTE_DO_PROCESSO), **self._ambiente_extra}, cwd=str(self._cwd),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            if saida_de_erro is not subprocess.DEVNULL:
                saida_de_erro.close()
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
        log.info("Qwen3-TTS pronto (processo %s)", proc.pid)
        return proc, fila

    @staticmethod
    def _ler(proc: subprocess.Popen, fila: queue.Queue) -> None:
        """Na thread: os quadros do processo vão para a fila; None = o processo fechou o stdout OU mandou algo que não é um
        quadro válido (tipo desconhecido, tamanho absurdo, áudio que não é float32): o fluxo está estragado."""
        try:
            while (cab := _ler_exato(proc.stdout, CABECALHO.size)) is not None:
                tipo, pedido, tamanho = CABECALHO.unpack(cab)
                if tipo not in TIPOS_DE_QUADRO or tamanho > CARGA_MAXIMA or (tipo == b"A" and tamanho % 4):
                    log.warning("quadro inválido do Qwen3-TTS (tipo %r, tamanho %d): o fluxo está estragado", tipo, tamanho)
                    break
                carga = _ler_exato(proc.stdout, tamanho) if tamanho else b""
                if carga is None:
                    break
                fila.put((tipo, pedido, carga))
        except (OSError, ValueError):
            pass
        finally:
            fila.put(None)

    def _pronto(self) -> bool:
        with self._estado:
            return self._proc is not None and self._proc.poll() is None

    def _enviar(self, msg: dict) -> None:
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("ascii"))  # ASCII: o texto vai escapado (\uXXXX)
            self._proc.stdin.flush()
        except (OSError, ValueError, AttributeError) as e:
            self._marcar_queda()
            self._parar()
            raise ErroDaVoz(f"não consegui falar com o processo do Qwen3-TTS: {e}") from e

    def _marcar_queda(self) -> None:
        with self._estado:
            self._falhou_em = time.monotonic()  # não reinicia a cada frase se ele cair sempre

    def _parar(self) -> None:
        with self._estado:
            proc, self._proc = self._proc, None
        if proc is not None:
            _matar(proc)

    def _subir_em_segundo_plano(self) -> None:
        """Depois de uma queda: sobe o processo numa thread, sem segurar a fala (que sai pela reserva até ficar pronto)."""
        with self._estado:
            if self._fechado or self._subindo or self.descansando:
                return
            if self._proc is not None and self._proc.poll() is None:
                return
            if time.monotonic() - self._falhou_em < self._espera_reinicio_s:
                return
            self._subindo = True
            epoca = self._epoca
        threading.Thread(target=self._subir, args=(epoca,), daemon=True).start()

    def _subir(self, epoca: int) -> None:
        novo = None
        try:
            novo = self._iniciar_processo()
        except BaseException as e:  # noqa: BLE001 - segue falando com a reserva; tenta de novo depois da espera
            log.warning("não consegui subir o Qwen3-TTS: %s", e)
        antigo = sobra = None
        with self._estado:
            self._subindo = False
            if novo is None:
                self._falhou_em = time.monotonic()
            elif epoca == self._epoca and not self._fechado and not self.descansando:
                antigo, (self._proc, self._fila) = self._proc, novo
            else:  # descansaram ou fecharam enquanto subia: não é mais para estar de pé
                sobra = novo[0]
        for proc in (antigo, sobra):
            if proc is not None:
                _matar(proc)

    def fechar(self) -> None:
        """Encerra o processo (também roda ao sair do Vision; se ele morrer, o processo acaba sozinho: stdin fecha)."""
        with self._estado:
            self._fechado = True
            self._epoca += 1
        if self._trava.acquire(timeout=3):
            try:
                self._parar()
            finally:
                self._trava.release()
        else:  # uma frase travada: não espera
            with self._estado:
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
                    self._marcar_queda()
                    raise ErroDaVoz(f"o Qwen3-TTS ficou {self._prazo_s:.0f} s sem responder") from None
                if item is None:
                    terminou = True
                    self._marcar_queda()
                    self._parar()  # limpa o processo morto (o `poll` demora a notar); o próximo pedido decide se reinicia
                    raise ErroDaVoz("o processo do Qwen3-TTS caiu no meio da fala (ou mandou um quadro inválido)")
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
        self._marcar_queda()
        self._parar()  # o próximo pedido sobe outro (em segundo plano)

    def pedacos(self, texto: str, normalizar: bool = True) -> Iterator[np.ndarray]:
        """Streaming, como o `VozXTTS.pedacos`: consuma na mesma thread e feche o gerador (`close`) se parar no meio.
        Sem o processo de pé (modo jogo, queda, subindo): fala a reserva e, se for o caso, manda subir o processo."""
        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return
        with self._trava:
            if self.descansando or not self._pronto():
                self._subir_em_segundo_plano()
                from vision.voice.stt import reamostrar

                yield reamostrar(self.reserva.sintetizar(texto, normalizar=False), self.reserva.taxa, self.taxa)
                return
            for trecho in self._trechos(texto):
                # O limiar acompanha o ganho (o compressor vem depois dele): a fala crua do Qwen3-TTS, com a gravação nivelada
                # em RMS 0,08, fica perto do nível em que o limiar padrão foi medido.
                compressor = (CompressorDePausas(self.taxa, self.pausa_max_s, limiar=LIMIAR_SILENCIO * self.ganho)
                              if self.pausa_max_s else None)
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
        with self._trava:  # espera a frase em andamento terminar
            with self._estado:
                self._epoca += 1  # uma partida em andamento (um `acordar` que ainda sobe) é jogada fora quando chegar
                ja_descansava, self.descansando = self.descansando, True
            if not ja_descansava:
                self._parar()

    def acordar(self) -> None:
        """Volta o processo (14 a 22 s) SEM segurar a trava: enquanto sobe, a fala sai pela reserva. Se não subir, continua
        descansando e levanta: o laço tenta de novo depois."""
        with self._estado:
            if not self.descansando or self._fechado or self._subindo:
                return
            self._subindo = True
            epoca = self._epoca
        novo = None
        try:
            novo = self._iniciar_processo()
        finally:
            sobra = None
            with self._estado:
                self._subindo = False
                if novo is not None:
                    if epoca == self._epoca and not self._fechado:
                        self._proc, self._fila = novo
                        self.descansando = False
                    else:
                        sobra = novo[0]
            if sobra is not None:
                _matar(sobra)
