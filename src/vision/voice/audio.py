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


class SaidaArquivo:
    """Guarda tudo o que seria tocado (para testes)."""

    def __init__(self, taxa: int = 22050, tempo_real: bool = False):
        self.taxa = taxa
        self.trechos: list[np.ndarray] = []
        self.interromper = threading.Event()
        self.tempo_real = tempo_real  # demora o tempo do áudio (e pode ser interrompida), como o alto-falante

    def tocar(self, audio: np.ndarray, taxa: int) -> bool:
        from vision.voice.stt import reamostrar

        self.trechos.append(reamostrar(audio, taxa, self.taxa))
        if self.tempo_real:
            self.interromper.clear()
            fim = time.monotonic() + audio.size / taxa
            while time.monotonic() < fim:
                if self.interromper.wait(0.01):
                    return False
        return True

    def audio(self) -> np.ndarray:
        return np.concatenate(self.trechos) if self.trechos else np.zeros(0, dtype=np.float32)


def escolher_microfone(nomes: list[str]) -> tuple[int | None, str, list[str]]:
    """Primeiro microfone da lista que não esteja em silêncio digital (headset desligado/mudo dá zero puro)."""
    import sounddevice as sd

    notas = []
    for nome in nomes or []:
        disp, nome_real = achar_dispositivo([nome], entrada=True)
        if disp is None:
            notas.append(f"{nome}: não encontrado")
            continue
        try:
            amostra = sd.rec(int(TAXA * 0.4), samplerate=TAXA, channels=1, dtype="int16", device=disp)
            sd.wait()
        except Exception as e:  # noqa: BLE001
            notas.append(f"{nome_real}: não abriu ({e})")
            continue
        # Headset sem fio desligado/mudo entrega ~-97 dBFS (zeros com ±1 de ruído); sala silenciosa fica em ~-60.
        rms = float(np.sqrt(np.mean((amostra.astype(np.float32) / 32768) ** 2)))
        if 20 * np.log10(max(rms, 1e-9)) < -85:
            notas.append(f"{nome_real}: silêncio digital (desligado ou mudo?)")
            continue
        return disp, nome_real, notas
    disp, nome_real = achar_dispositivo([], entrada=True)
    return disp, nome_real, notas


class Microfone:
    """Blocos int16 de 80 ms a 16 kHz, entregues numa fila assíncrona."""

    def __init__(self, cfg: Config):
        self.dispositivo, self.nome, self.notas = escolher_microfone(cfg.get("voz.microfone", []))
        self._fila: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream = None

    def __enter__(self) -> Microfone:
        import sounddevice as sd

        def cb(indata, _frames, _tempo, _status):
            try:
                self._fila.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        self._stream = sd.InputStream(
            samplerate=TAXA, channels=1, dtype="int16", blocksize=BLOCO, device=self.dispositivo, callback=cb
        )
        self._stream.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

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
                yield self._fila.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)


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
