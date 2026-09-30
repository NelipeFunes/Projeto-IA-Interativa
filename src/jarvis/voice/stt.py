"""Voz → texto com o Parakeet TDT 0.6B v3 (ONNX, CPU). Zero VRAM."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from jarvis.config import Config

TAXA = 16000

MODELOS = {
    # ajuste fino PT-BR (TAGARELA), fp32
    "parakeet-ptbr": ("nemo-parakeet-tdt-0.6b-v3", "parakeet-ptbr", None),
    # multilíngue original, int8 (mais leve e rápido)
    "parakeet-base-int8": ("nemo-parakeet-tdt-0.6b-v3", "parakeet-base-int8", "int8"),
}


class Transcritor:
    def __init__(self, pasta_modelos: Path, nome: str = "parakeet-ptbr", threads: int = 6):
        import onnx_asr
        import onnxruntime as ort

        tipo, pasta, quant = MODELOS[nome]
        caminho = pasta_modelos / pasta
        if not caminho.exists():
            raise FileNotFoundError(f"modelo de STT não encontrado: {caminho} (rode scripts/baixar_modelos.py)")
        opcoes = ort.SessionOptions()
        opcoes.intra_op_num_threads = threads  # i7-8700: 6 núcleos físicos
        self.nome = nome
        self.modelo = onnx_asr.load_model(
            tipo, caminho, quantization=quant, sess_options=opcoes, providers=["CPUExecutionProvider"]
        )

    def transcrever(self, audio: np.ndarray, taxa: int = TAXA) -> str:
        if audio.size == 0:
            return ""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32) / (32768.0 if np.issubdtype(audio.dtype, np.integer) else 1.0)
        if taxa != TAXA:
            audio = reamostrar(audio, taxa, TAXA)
        return str(self.modelo.recognize(audio, sample_rate=TAXA)).strip()


def reamostrar(audio: np.ndarray, de: int, para: int) -> np.ndarray:
    if de == para:
        return audio
    from scipy.signal import resample_poly

    g = np.gcd(de, para)
    return resample_poly(audio, para // g, de // g).astype(np.float32)


def carregar_transcritor(cfg: Config) -> Transcritor:
    t = time.perf_counter()
    tr = Transcritor(cfg.modelos, cfg.get("voz.stt", "parakeet-ptbr"))
    tr.segundos_carga = time.perf_counter() - t  # type: ignore[attr-defined]
    return tr
