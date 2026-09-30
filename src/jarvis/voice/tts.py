"""Texto → voz com o Piper (ONNX, roda na CPU, vozes pt_BR)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from jarvis.config import Config
from jarvis.voice.falado import para_fala


class Voz:
    def __init__(self, arquivo_modelo: Path, velocidade: float = 1.0, deterministico: bool = False):
        from piper import PiperVoice, SynthesisConfig

        if not arquivo_modelo.exists():
            raise FileNotFoundError(f"voz não encontrada: {arquivo_modelo} (rode scripts/baixar_modelos.py)")
        self.voz = PiperVoice.load(arquivo_modelo)
        self.taxa = self.voz.config.sample_rate
        # O Piper sorteia variações a cada síntese (mais natural); nos testes, o mesmo texto dá o mesmo áudio.
        ruido = {"noise_scale": 0.0, "noise_w_scale": 0.0} if deterministico else {}
        self.cfg = SynthesisConfig(length_scale=velocidade, **ruido)

    def sintetizar(self, texto: str, normalizar: bool = True) -> np.ndarray:
        """Devolve float32 mono em self.taxa Hz."""
        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return np.zeros(0, dtype=np.float32)
        partes = [c.audio_float_array for c in self.voz.synthesize(texto, syn_config=self.cfg)]
        return np.concatenate(partes).astype(np.float32) if partes else np.zeros(0, dtype=np.float32)


def carregar_voz(cfg: Config, nome: str | None = None) -> Voz:
    nome = nome or cfg.get("voz.voz_piper", "pt_BR-faber-medium")
    return Voz(cfg.modelos / "piper" / f"{nome}.onnx", float(cfg.get("voz.velocidade_fala", 1.0)))


def falar_texto(cfg: Config, texto: str) -> None:
    """`jarvis falar ...`: sintetiza e toca no alto-falante configurado."""
    from jarvis.voice.audio import Saida

    t = time.perf_counter()
    voz = carregar_voz(cfg)
    audio = voz.sintetizar(texto)
    print(f"[{time.perf_counter() - t:.2f}s para sintetizar {len(audio) / voz.taxa:.1f}s de fala] {para_fala(texto)}")
    Saida(cfg).tocar(audio, voz.taxa)
