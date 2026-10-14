"""Palavra de ativação por modelo (openWakeWord), detector de fala (Silero VAD) e o atalho de teclado."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# O verificador da sua voz (scripts/ativacao/gravar_minha_voz.py): `<modelo>_verificador.pkl` ao lado do .onnx.
SUFIXO_VERIFICADOR = "_verificador.pkl"
# Nota do modelo a partir da qual o verificador é consultado (e passa a dar a nota no lugar dele). Baixa de propósito:
# com o seu sotaque o modelo (treinado com vozes sintéticas) pode dar 0,2 para um "Hey Vision" seu, e o verificador,
# que conhece a sua voz, decide se foi você chamando.
LIMIAR_VERIFICADOR = 0.1


def abrir_modelo(pasta: Path, modelo: Path, verificador: Path | None = None, limiar_verificador: float = LIMIAR_VERIFICADOR):
    """O openWakeWord com os arquivos locais de `pasta` (sem baixar nada)."""
    from openwakeword.model import Model

    extra = {}
    if verificador is not None:
        # O .pkl é um pickle (scikit-learn): só o que foi gerado aqui, em modelos/, que fica fora do git.
        extra = {"custom_verifier_models": {modelo.stem: str(verificador)},
                 "custom_verifier_threshold": limiar_verificador}
    return Model(
        wakeword_models=[str(modelo)],
        inference_framework="onnx",
        melspec_model_path=str(pasta / "melspectrogram.onnx"),
        embedding_model_path=str(pasta / "embedding_model.onnx"),
        **extra,
    )


class PalavraAtivacao:
    def __init__(self, pasta: Path, nome: str = "hey_jarvis", limiar: float = 0.5,
                 limiar_verificador: float = LIMIAR_VERIFICADOR):
        modelo = next(pasta.glob(f"{nome}*.onnx"), None)
        if modelo is None:
            raise FileNotFoundError(f"modelo de ativação '{nome}' não encontrado em {pasta}")
        verificador = pasta / f"{modelo.stem}{SUFIXO_VERIFICADOR}"
        self.com_verificador = verificador.is_file()
        self.modelo = abrir_modelo(pasta, modelo, verificador if self.com_verificador else None, limiar_verificador)
        self.nome = modelo.stem
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
    """Tecla de atalho global (ex.: ctrl+alt+j). Funciona como dizer 'Hey Vision'."""

    def __init__(self, combinacao: str, ao_apertar: Callable[[], None]):
        self.combinacao = combinacao
        self.ativo = False
        self._gancho = None
        try:
            import keyboard

            self._gancho = keyboard.add_hotkey(combinacao, ao_apertar)
            self.ativo = True
        except Exception as e:  # noqa: BLE001 - sem atalho o resto continua funcionando
            log.warning("atalho %s indisponível: %s", combinacao, e)

    def fechar(self) -> None:
        if self.ativo:
            import keyboard

            # Só o deste atalho: o núcleo tem mais de um (ouvir e abrir a janela).
            keyboard.remove_hotkey(self._gancho)
            self.ativo = False
