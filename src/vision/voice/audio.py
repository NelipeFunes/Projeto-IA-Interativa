"""Microfone e alto-falante (sounddevice/MME), mais versões de arquivo para testar sem hardware."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np

from vision.config import Config

TAXA = 16000
BLOCO = 1280  # 80 ms: o tamanho que o openWakeWord espera


def _mme() -> int | None:
    import sounddevice as sd

    for i, h in enumerate(sd.query_hostapis()):
        if h["name"] == "MME":  # aceita 16 kHz direto; WASAPI compartilhado exige a taxa nativa
            return i
    return None


def achar_dispositivo(nomes: list[str], entrada: bool) -> tuple[int | None, str]:
    import sounddevice as sd

    api = _mme()
    canais = "max_input_channels" if entrada else "max_output_channels"
    for nome in nomes or []:
        for i, d in enumerate(sd.query_devices()):
            if (api is None or d["hostapi"] == api) and d[canais] > 0 and nome.lower() in d["name"].lower():
                return i, d["name"]
    padrao = sd.default.device[0 if entrada else 1]
    return None, sd.query_devices(padrao)["name"] if padrao is not None and padrao >= 0 else "padrão"


def bipe(subindo: bool = True, taxa: int = 22050) -> np.ndarray:
    """Sinal curto de 'estou ouvindo' (sobe) ou 'entendi' (desce)."""
    notas = (660, 880) if subindo else (880, 660)
    t = np.linspace(0, 0.07, int(taxa * 0.07), endpoint=False)
    partes = [0.25 * np.sin(2 * np.pi * f * t) * np.hanning(t.size) for f in notas]
    return np.concatenate(partes).astype(np.float32)


def bipe_desligar(taxa: int = 22050) -> np.ndarray:
    """Três notas descendo: 'conversa encerrada' (no lugar de falar uma despedida)."""
    t = np.linspace(0, 0.09, int(taxa * 0.09), endpoint=False)
    partes = [0.25 * np.sin(2 * np.pi * f * t) * np.hanning(t.size) for f in (880, 660, 440)]
    return np.concatenate(partes).astype(np.float32)


def alarme(taxa: int = 22050, vezes: int = 4) -> np.ndarray:
    """Fim de timer: quatro bipes duplos, agudos, com pausa (uns 3 s)."""
    t = np.linspace(0, 0.12, int(taxa * 0.12), endpoint=False)
    bipe_ = 0.3 * np.sin(2 * np.pi * 1320 * t) * np.hanning(t.size)
    pausa = np.zeros(int(taxa * 0.08))
    grupo = np.concatenate([bipe_, pausa, bipe_, np.zeros(int(taxa * 0.5))])
    return np.tile(grupo, vezes).astype(np.float32)


class Saida:
    def __init__(self, cfg: Config):
        self.dispositivo, self.nome = achar_dispositivo(cfg.get("voz.alto_falante", []), entrada=False)
        self.interromper = threading.Event()

    def tocar(self, audio: np.ndarray, taxa: int) -> bool:
        """Toca e espera terminar. Devolve False se foi interrompido."""
        import sounddevice as sd

        if audio.size == 0:
            return True
        self.interromper.clear()
        sd.play(audio, taxa, device=self.dispositivo)
        duracao = audio.size / taxa
        fim = time.monotonic() + duracao + 0.3
        while time.monotonic() < fim:
            if self.interromper.is_set():
                sd.stop()
                return False
            time.sleep(0.02)
        sd.wait()
        return True


    def abrir(self, taxa: int) -> FluxoSaida:
        """Uma resposta inteira num fluxo só: os pedaços do XTTS emendam sem buraco entre eles."""
        return FluxoSaida(self.dispositivo, taxa, self.interromper)


class FluxoSaida:
    PASSO_S = 0.05  # escreve em fatias de 50 ms para o corte (por voz ou atalho) valer na hora

    def __init__(self, dispositivo: int | None, taxa: int, interromper: threading.Event):
        import sounddevice as sd

        self.taxa = taxa
        self.interromper = interromper
        self.stream = sd.OutputStream(samplerate=taxa, channels=1, dtype="float32", device=dispositivo)
        self.fechado = False
        try:
            self.stream.start()
        except BaseException:  # dispositivo ocupado: não deixa o stream aberto (revisão do PR 22)
            self.stream.close()
            raise

    def escrever(self, audio: np.ndarray, taxa: int) -> bool:
        """Espera o pedaço entrar no buffer (o ritmo da fala). False se foi interrompido."""
        from vision.voice.stt import reamostrar

        if taxa != self.taxa:
            audio = reamostrar(audio, taxa, self.taxa)
        passo = max(1, int(self.taxa * self.PASSO_S))
        for i in range(0, audio.size, passo):
            if self.interromper.is_set():
                return False
            self.stream.write(np.ascontiguousarray(audio[i : i + passo], dtype=np.float32).reshape(-1, 1))
        return not self.interromper.is_set()

    def fechar(self, drenar: bool = True) -> None:
        """`drenar`: deixa o fim do buffer tocar (o `stop` do PortAudio espera); senão corta na hora. Pode ser
        chamado mais de uma vez (o corte fecha na hora, e o fim da resposta fecha de novo)."""
        if self.fechado:
            return
        self.fechado = True
        try:
            if drenar and not self.interromper.is_set():
                self.stream.stop()
            else:
                self.stream.abort()
        finally:
            self.stream.close()


class SaidaArquivo:
    """Guarda tudo o que seria tocado (para testes)."""

    def __init__(self, taxa: int = 22050, tempo_real: bool = False):
        self.taxa = taxa
        self.trechos: list[np.ndarray] = []
        self.interromper = threading.Event()
        self.tempo_real = tempo_real  # demora o tempo do áudio (e pode ser interrompida), como o alto-falante

    def tocar(self, audio: np.ndarray, taxa: int, _limpar: bool = True) -> bool:
        from vision.voice.stt import reamostrar

        if not _limpar and self.interromper.is_set():
            return False  # no fluxo, um corte anterior vale (o `tocar` sozinho começa do zero)
        self.trechos.append(reamostrar(audio, taxa, self.taxa))
        if self.tempo_real:
            if _limpar:
                self.interromper.clear()
            fim = time.monotonic() + audio.size / taxa
            while time.monotonic() < fim:
                if self.interromper.wait(0.01):
                    return False
        return True

    def abrir(self, _taxa: int) -> SaidaArquivo:
        """O mesmo papel do `FluxoSaida`: cada pedaço escrito vira um trecho."""
        return self

    def escrever(self, audio: np.ndarray, taxa: int) -> bool:
        return self.tocar(audio, taxa, _limpar=False) and not self.interromper.is_set()

    def fechar(self, drenar: bool = True) -> None:
        pass

    def audio(self) -> np.ndarray:
        return np.concatenate(self.trechos) if self.trechos else np.zeros(0, dtype=np.float32)


SILENCIO_DIGITAL_DB = -85  # headset sem fio desligado/mudo: ~-97 dBFS (zeros com ±1); sala silenciosa: ~-60 a -75
# O microfone se vigia enquanto o Vision roda (02/10: o HyperX ficou mudo por um minuto e, antes, a escuta só
# escolhia o microfone ao iniciar; com o headset mudo ou desligado, o "Hey Vision" nunca mais era ouvido).
TROCAR_APOS_SILENCIO_S = 60.0  # o microfone em uso só deu silêncio digital por 1 min: tenta o próximo da lista
CONFERIR_PREFERIDO_S = 30.0  # usando um reserva: a cada 30 s confere se o primeiro da lista voltou
SEM_AUDIO_S = 3.0  # o fluxo parou de entregar blocos (o aparelho sumiu do Windows): reabre
PRAZO_VIGIA_S = 5.0  # amostrar/abrir um microfone que não responde não prende a escuta mais que isso


def silencio_digital(pcm: np.ndarray) -> bool:
    rms = float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768) ** 2))) if pcm.size else 0.0
    return 20 * np.log10(max(rms, 1e-9)) < SILENCIO_DIGITAL_DB


def _amostra(disp: int | None, segundos: float = 0.4) -> np.ndarray:
    """0,4 s de um microfone, num fluxo próprio: sd.rec/sd.wait usam o estado global do sounddevice, o mesmo do
    sd.play dos bipes (um cortaria o outro; revisão do PR 39)."""
    import sounddevice as sd

    with sd.InputStream(samplerate=TAXA, channels=1, dtype="int16", device=disp) as fluxo:
        dados, _ = fluxo.read(int(TAXA * segundos))
    return dados[:, 0].copy()


def escolher_microfone(nomes: list[str]) -> tuple[int | None, str, list[str]]:
    """Primeiro microfone da lista que não esteja em silêncio digital (headset desligado/mudo dá zero puro)."""
    notas = []
    for nome in nomes or []:
        disp, nome_real = achar_dispositivo([nome], entrada=True)
        if disp is None:
            notas.append(f"{nome}: não encontrado")
            continue
        try:
            amostra = _amostra(disp)
        except Exception as e:  # noqa: BLE001
            notas.append(f"{nome_real}: não abriu ({e})")
            continue
        if silencio_digital(amostra):
            notas.append(f"{nome_real}: silêncio digital (desligado ou mudo?)")
            continue
        return disp, nome_real, notas
    disp, nome_real = achar_dispositivo([], entrada=True)
    return disp, nome_real, notas


class VigiaMicrofone:
    """Decide, bloco a bloco, quando o microfone em uso precisa ser trocado ou reaberto. Sem áudio de verdade
    (o relógio vem de fora): o Microfone faz o que ela pede.

    - "trocar": só silêncio digital há TROCAR_APOS_SILENCIO_S (mudo/desligado): escolher de novo na lista.
    - "conferir": num microfone reserva, de tempos em tempos: ver se o preferido (o 1º da lista) voltou.
    O fluxo que morre (nenhum bloco do driver há SEM_AUDIO_S) é visto pelo Microfone, pela hora do último bloco
    que o driver entregou: a do último bloco *lido* não serve, porque enquanto o Vision fala ninguém lê.
    """

    def __init__(self, agora: float, varios: bool, no_preferido: bool):
        self.varios = varios  # com um microfone só, não há para onde trocar (reabrir ainda vale)
        self.no_preferido = no_preferido
        self.zerar(agora, no_preferido)

    def zerar(self, agora: float, no_preferido: bool) -> None:
        self.no_preferido = no_preferido
        self.silencio_desde: float | None = None
        self.conferido_em = agora

    def bloco(self, pcm: np.ndarray, agora: float) -> str | None:
        if not silencio_digital(pcm):
            self.silencio_desde = None
        elif self.silencio_desde is None:
            self.silencio_desde = agora
        if not self.varios:
            return None
        if self.silencio_desde is not None and agora - self.silencio_desde >= TROCAR_APOS_SILENCIO_S:
            self.silencio_desde = agora  # se nenhum outro tiver som, só tenta de novo daqui a 1 min
            return "trocar"
        if not self.no_preferido and agora - self.conferido_em >= CONFERIR_PREFERIDO_S:
            self.conferido_em = agora
            return "conferir"
        return None


class Microfone:
    """Blocos int16 de 80 ms a 16 kHz, entregues numa fila assíncrona. Se vigia (VigiaMicrofone): mudo ou
    desligado por 1 min, passa para o próximo da lista; o preferido voltando, volta para ele; o fluxo morrendo,
    reabre. `ao_trocar` recebe a frase de cada troca (o laço escreve no log)."""

    def __init__(self, cfg: Config, *, relogio=time.monotonic):
        self.nomes = list(cfg.get("voz.microfone", []) or [])
        self.dispositivo, self.nome, self.notas = escolher_microfone(self.nomes)
        self._fila: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream = None
        self._relogio = relogio
        self.ao_trocar = None
        self._recebido_em = relogio()  # último bloco que o driver entregou (o callback roda na thread de áudio)
        self._vigia = VigiaMicrofone(relogio(), len(self.nomes) > 1, self._e_o_preferido())
        self._trava = threading.Lock()  # troca e fechamento nunca ao mesmo tempo (a vigia roda numa thread)
        self._encerrado = False
        self._em_andamento: asyncio.Future | None = None
        self._espera_reabrir = SEM_AUDIO_S  # dobra a cada reabertura sem áudio (até 60 s); volta com o 1º bloco

    def _e_o_preferido(self) -> bool:
        return not self.nomes or str(self.nomes[0]).lower() in self.nome.lower()

    def __enter__(self) -> Microfone:
        self._stream = self._criar(self.dispositivo)
        return self

    def _criar(self, disp: int | None):
        import sounddevice as sd

        def cb(indata, _frames, _tempo, _status):
            self._recebido_em = self._relogio()
            try:
                self._fila.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        fluxo = sd.InputStream(samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO, device=disp, callback=cb)
        fluxo.start()
        self._recebido_em = self._relogio()  # o novo tem um tempo para começar a entregar
        return fluxo

    @staticmethod
    def _fechar_fluxo(fluxo) -> None:
        if fluxo is not None:
            try:
                fluxo.stop()
                fluxo.close()
            except Exception:  # noqa: BLE001 - aparelho que sumiu pode falhar ao fechar
                pass

    def __exit__(self, *exc: object) -> None:
        with self._trava:
            self._encerrado = True  # uma vigia que termine depois não abre fluxo que ninguém fecha
            fluxo, self._stream = self._stream, None
        self._fechar_fluxo(fluxo)

    def _trocar_para(self, disp: int | None, nome: str, motivo: str, *, avisar: bool = True) -> bool:
        """Abre o novo ANTES de fechar o atual: se ele não abrir, continua tudo como estava (revisão do PR 39)."""
        with self._trava:
            if self._encerrado:
                return False
            try:
                novo = self._criar(disp)
            except Exception as e:  # noqa: BLE001 - tenta de novo na próxima vigia
                if avisar:
                    self._avisar(f"não consegui abrir {nome} ({e}); continua {self.nome}")
                return False
            antigo, antes = self._stream, self.nome
            self._stream, self.dispositivo, self.nome = novo, disp, nome
        if antigo is not novo:
            self._fechar_fluxo(antigo)
        if avisar:
            self._avisar(f"{motivo}: {antes} → {nome}" if nome != antes else f"{motivo}: {nome} reaberto")
        return True

    def _avisar(self, texto: str) -> None:
        if self.ao_trocar is not None:
            self.ao_trocar(f"[microfone] {texto}")

    def _vigiar(self, acao: str) -> None:
        """Roda numa thread: amostrar um microfone leva 0,4 s e o fluxo atual continua enchendo a fila."""
        if acao == "reabrir":
            primeira = self._espera_reabrir <= SEM_AUDIO_S  # numa sequência de falhas, só a 1ª vai ao log
            disp, nome, _ = escolher_microfone(self.nomes)
            self._trocar_para(disp, nome, "o microfone parou de mandar áudio", avisar=primeira)
            self._espera_reabrir = min(self._espera_reabrir * 2, TROCAR_APOS_SILENCIO_S)
        elif acao == "trocar":
            disp, nome, _ = escolher_microfone(self.nomes)
            if disp != self.dispositivo:
                self._trocar_para(disp, nome, f"{self.nome} mudo ou desligado há 1 min")
        elif acao == "conferir" and self.nomes:
            disp, nome = achar_dispositivo([str(self.nomes[0])], entrada=True)
            if disp is None or disp == self.dispositivo:
                return
            try:
                voltou = not silencio_digital(_amostra(disp))
            except Exception:  # noqa: BLE001
                return
            if voltou:
                self._trocar_para(disp, nome, f"{nome} voltou")

    def _vigiar_seguro(self, acao: str) -> None:
        try:
            self._vigiar(acao)
        except Exception:  # noqa: BLE001 - a vigia nunca derruba a escuta
            pass

    async def _cuidar(self, acao: str) -> None:
        """Uma ação por vez e com prazo: um driver que trave ao abrir não congela a escuta (revisão do PR 39).
        Passado o prazo, a escuta segue no fluxo atual e a thread termina sozinha quando o driver soltar."""
        if self._em_andamento is not None and not self._em_andamento.done():
            return
        self._em_andamento = asyncio.ensure_future(asyncio.to_thread(self._vigiar_seguro, acao))
        try:
            await asyncio.wait_for(asyncio.shield(self._em_andamento), PRAZO_VIGIA_S)
        except TimeoutError:
            self._avisar(f"a troca de microfone passou de {PRAZO_VIGIA_S:g} s (driver travado?); a escuta continua")
        self._vigia.zerar(self._relogio(), self._e_o_preferido())

    def descartar(self) -> None:
        """Joga fora o áudio acumulado enquanto o Vision pensava/falava."""
        while not self._fila.empty():
            try:
                self._fila.get_nowait()
            except queue.Empty:
                break

    async def blocos(self) -> AsyncIterator[np.ndarray]:
        while True:
            try:
                bloco = self._fila.get_nowait()
            except queue.Empty:
                if self._relogio() - self._recebido_em >= self._espera_reabrir:
                    await self._cuidar("reabrir")
                await asyncio.sleep(0.01)
                continue
            self._espera_reabrir = SEM_AUDIO_S
            if (acao := self._vigia.bloco(bloco, self._relogio())) is not None:
                await self._cuidar(acao)
            yield bloco


class ArquivoComoMicrofone:
    """Toca WAVs como se fossem o microfone (em tempo acelerado), para testar o loop sem hardware."""

    def __init__(self, audios: list[np.ndarray | Path], silencio_final_s: float = 2.0):
        import soundfile as sf

        from vision.voice.stt import reamostrar

        partes = []
        for a in audios:
            if isinstance(a, Path):
                dados, taxa = sf.read(a, dtype="float32")
                a = reamostrar(dados if dados.ndim == 1 else dados[:, 0], taxa, TAXA)
            partes.append(a)
        partes.append(np.zeros(int(TAXA * silencio_final_s), dtype=np.float32))
        total = np.concatenate(partes)
        self.pcm = (np.clip(total, -1, 1) * 32767).astype(np.int16)
        self.nome = "arquivo"
        self._pos = 0  # compartilhada: quem escuta durante a fala continua de onde o laço parou, como no microfone

    def __enter__(self) -> ArquivoComoMicrofone:
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def descartar(self) -> None:
        pass  # no arquivo, nada chega enquanto o Vision fala

    async def blocos(self) -> AsyncIterator[np.ndarray]:
        while self._pos + BLOCO <= len(self.pcm):
            bloco = self.pcm[self._pos : self._pos + BLOCO]
            self._pos += BLOCO
            yield bloco
            await asyncio.sleep(0)
