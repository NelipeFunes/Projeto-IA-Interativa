"""Texto → voz. Dois motores (`voz.motor`):
- `xtts`: XTTS-v2 na placa de vídeo (~2 GB de VRAM), voz natural, ~1 s por frase curta. Licença do modelo: Coqui
  Public Model License (só uso não comercial). No modo jogo ela sai da VRAM e quem fala é o Piper.
- `piper`: Piper (ONNX, CPU, vozes pt_BR), instantâneo, mais robótico. É também a reserva do XTTS."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from vision.config import Config
from vision.voice.falado import para_fala

log = logging.getLogger(__name__)


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


class VozXTTS:
    """XTTS-v2 com uma das vozes de estúdio que vêm com o modelo (`voz.xtts_falante`).

    `descansar()` (modo jogo) tira o modelo da VRAM e passa a falar com a `reserva` (Piper); `acordar()` volta.
    O áudio sai sempre a 24 kHz, mesmo o da reserva, para a taxa não mudar no meio de uma resposta."""

    taxa = 24000

    def __init__(self, pasta: Path, falante: str, reserva: Voz, velocidade: float = 1.0, idioma: str = "pt"):
        import torch
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        if not (pasta / "model.pth").exists():
            raise FileNotFoundError(f"XTTS-v2 não encontrado em {pasta} (rode scripts/baixar_modelos.py --xtts)")
        if not torch.cuda.is_available():
            raise RuntimeError("sem placa de vídeo com CUDA para o XTTS")
        cfg = XttsConfig()
        cfg.load_json(str(pasta / "config.json"))
        self.modelo = Xtts.init_from_config(cfg)
        self.modelo.load_checkpoint(cfg, checkpoint_dir=str(pasta), use_deepspeed=False)
        falantes = self.modelo.speaker_manager.speakers
        if falante not in falantes:
            raise ValueError(f"voz '{falante}' não existe no XTTS (opções: {', '.join(sorted(falantes))})")
        self.latente, self.timbre = falantes[falante].values()
        self.falante = falante
        self.reserva = reserva
        self.idioma = idioma
        self.velocidade = 1.0 / max(velocidade, 0.1)  # no config, >1 fala mais devagar (como no Piper)
        self._trava = threading.Lock()  # uma síntese por vez; descansar/acordar esperam a frase terminar
        self.descansando = False
        # Tokens por pedaço do streaming. Medido em 01/10 (1ª palavra / resposta de 6 s): 20 → 0,67 s; 12 → 0,40 s;
        # 8 → 0,29 s, mas aí a geração não acompanha a fala e abre buracos (7,0 s no total).
        self.pedaco_tokens = 12
        self.modelo.cuda().eval()
        self.sintetizar("Oi.")  # a primeira síntese compila os kernels: melhor aqui do que na 1ª resposta

    def sintetizar(self, texto: str, normalizar: bool = True) -> np.ndarray:
        import torch

        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return np.zeros(0, dtype=np.float32)
        with self._trava:
            if self.descansando:
                from vision.voice.stt import reamostrar

                return reamostrar(self.reserva.sintetizar(texto, normalizar=False), self.reserva.taxa, self.taxa)
            with torch.inference_mode():
                saida = self.modelo.inference(texto, self.idioma, self.latente, self.timbre, speed=self.velocidade,
                                              enable_text_splitting=len(texto) > 200)
        return np.asarray(saida["wav"], dtype=np.float32)

    def pedacos(self, texto: str, normalizar: bool = True) -> Iterator[np.ndarray]:
        """Streaming: o 1º pedaço sai em ~0,6 s, e o resto vem enquanto ele toca. Consuma na mesma thread e
        feche o gerador (`close`) se parar no meio: ele segura a trava da placa."""
        import torch

        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return
        with self._trava:
            if self.descansando:
                from vision.voice.stt import reamostrar

                yield reamostrar(self.reserva.sintetizar(texto, normalizar=False), self.reserva.taxa, self.taxa)
                return
            with torch.inference_mode():
                for pedaco in self.modelo.inference_stream(texto, self.idioma, self.latente, self.timbre,
                                                           stream_chunk_size=self.pedaco_tokens,
                                                           speed=self.velocidade,
                                                           enable_text_splitting=len(texto) > 200):
                    yield pedaco.cpu().numpy().astype(np.float32)

    def descansar(self) -> None:
        import torch

        with self._trava:
            if self.descansando:
                return
            self.modelo.cpu()
            torch.cuda.empty_cache()
            self.descansando = True

    def acordar(self) -> None:
        """Volta para a placa. Se faltar VRAM no meio (o Qwen voltou antes), desfaz e levanta: o laço tenta de
        novo depois, e até lá fala o Piper (revisão do PR 22)."""
        import torch

        with self._trava:
            if not self.descansando:
                return
            try:
                self.modelo.cuda()
            except BaseException:
                self.modelo.cpu()  # o `.cuda()` move peça por peça: não deixa metade ocupando a placa
                torch.cuda.empty_cache()
                raise
            self.descansando = False


def carregar_piper(cfg: Config, nome: str | None = None) -> Voz:
    nome = nome or cfg.get("voz.voz_piper", "pt_BR-faber-medium")
    return Voz(cfg.modelos / "piper" / f"{nome}.onnx", float(cfg.get("voz.velocidade_fala", 1.0)))


def carregar_voz(cfg: Config, nome: str | None = None) -> Voz | VozXTTS:
    """A voz do `voz.motor`. Se o XTTS não carregar (sem torch, sem placa, sem o modelo), fala com o Piper."""
    piper = carregar_piper(cfg, nome)
    if nome is not None or cfg.get("voz.motor", "piper") != "xtts":
        return piper
    try:
        return VozXTTS(cfg.modelos / "xtts_v2", str(cfg.get("voz.xtts_falante", "Tanja Adelina")), piper,
                       float(cfg.get("voz.velocidade_fala", 1.0)))
    except Exception as e:  # noqa: BLE001 - sem a voz boa, a robótica ainda serve
        log.warning("XTTS indisponível (%s); falando com o Piper", e)
        return piper


def descrever(voz: Voz | VozXTTS, cfg: Config) -> str:
    return f"XTTS ({voz.falante})" if isinstance(voz, VozXTTS) else f"Piper ({cfg.get('voz.voz_piper')})"


def falar_texto(cfg: Config, texto: str) -> None:
    """`vision falar ...`: sintetiza e toca no alto-falante configurado."""
    from vision.voice.audio import Saida

    voz = carregar_voz(cfg)
    t = time.perf_counter()
    audio = voz.sintetizar(texto)
    print(f"[{descrever(voz, cfg)}: {time.perf_counter() - t:.2f}s para sintetizar {len(audio) / voz.taxa:.1f}s de "
          f"fala] {para_fala(texto)}")
    Saida(cfg).tocar(audio, voz.taxa)
