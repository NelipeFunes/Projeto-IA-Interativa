"""'Hey Jarvis' (openWakeWord), detector de fala (Silero VAD) e o atalho de teclado."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class PalavraAtivacao:
    def __init__(self, pasta: Path, nome: str = "hey_jarvis", limiar: float = 0.5):
        from openwakeword.model import Model

        modelo = next(pasta.glob(f"{nome}*.onnx"), None)
        if modelo is None:
            raise FileNotFoundError(f"modelo de ativação '{nome}' não encontrado em {pasta}")
        self.modelo = Model(
            wakeword_models=[str(modelo)],
            inference_framework="onnx",
            melspec_model_path=str(pasta / "melspectrogram.onnx"),
            embedding_model_path=str(pasta / "embedding_model.onnx"),
        )
        self.limiar = limiar
        self.ultimo_score = 0.0

    def ouvir(self, bloco: np.ndarray) -> bool:
        """bloco: int16, 1280 amostras a 16 kHz."""
        scores = self.modelo.predict(bloco)
        self.ultimo_score = max(scores.values()) if scores else 0.0
        return self.ultimo_score >= self.limiar

    def zerar(self) -> None:
        self.modelo.reset()


class DetectorFala:
    """Diz se há voz num bloco de 80 ms e decide quando você terminou de falar."""

    def __init__(self, modelo: Path, silencio_fim_ms: int = 800, maximo_s: float = 20, espera_inicio_s: float = 5):
        from openwakeword.vad import VAD

        self.vad = VAD(model_path=str(modelo))
        self.silencio_fim_ms = silencio_fim_ms
        self.maximo_s = maximo_s
        self.espera_inicio_s = espera_inicio_s
        self.zerar()

    def zerar(self) -> None:
        self.vad.reset_states()
        self.falou = False
        self.ms_total = 0
        self.ms_silencio = 0

    def tem_voz(self, pcm: np.ndarray) -> bool:
        """Só pergunta 'tem voz nestes 80 ms?' (usado na janela de conversa, antes de gravar)."""
        return float(self.vad.predict(pcm, frame_size=640)) >= 0.5

    def bloco(self, pcm: np.ndarray) -> bool:
        """Processa 80 ms. Devolve True quando a fala acabou (ou estourou o tempo)."""
        prob = float(self.vad.predict(pcm, frame_size=640))
        self.ms_total += 80
        if prob >= 0.5:
            self.falou = True
            self.ms_silencio = 0
        else:
            self.ms_silencio += 80
        if self.falou and self.ms_silencio >= self.silencio_fim_ms:
            return True
        if not self.falou and self.ms_total >= self.espera_inicio_s * 1000:
            return True
        return self.ms_total >= self.maximo_s * 1000


class Atalho:
    """Tecla de atalho global (ex.: ctrl+alt+j). Funciona como dizer 'Hey Jarvis'."""

    def __init__(self, combinacao: str, ao_apertar: Callable[[], None]):
        self.combinacao = combinacao
        self.ativo = False
        try:
            import keyboard

            keyboard.add_hotkey(combinacao, ao_apertar)
            self.ativo = True
        except Exception as e:  # noqa: BLE001 - sem atalho o resto continua funcionando
            log.warning("atalho %s indisponível: %s", combinacao, e)

    def fechar(self) -> None:
        if self.ativo:
            import keyboard

            keyboard.unhook_all_hotkeys()
