"""Texto → voz. Dois motores (`voz.motor`):
- `xtts`: XTTS-v2 na placa de vídeo (~2 GB de VRAM), voz natural, ~1 s por frase curta. Licença do modelo: Coqui
  Public Model License (só uso não comercial). No modo jogo ela sai da VRAM e quem fala é o Piper.
- `piper`: Piper (ONNX, CPU, vozes pt_BR), instantâneo, mais robótico. É também a reserva do XTTS."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from vision.config import Config
from vision.voice.falado import para_fala

log = logging.getLogger(__name__)

# O XTTS aceita até ~203 caracteres de português por vez; acima disso, a divisão dele exige o spaCy (não
# instalado). Em 01/10 uma resposta de busca na web passou disso e derrubou o laço de voz: a divisão é nossa.
LIMITE_XTTS = 180


def dividir(texto: str, limite: int = LIMITE_XTTS) -> list[str]:
    """Pedaços de até `limite` caracteres, cortando de preferência em fim de frase, depois em vírgula ou
    ponto e vírgula, depois em espaço."""
    texto = " ".join(texto.split())
    if len(texto) <= limite:
        return [texto] if texto else []
    for separador in (r"(?<=[.!?…])\s+", r"(?<=[,;:])\s+", r"\s+"):
        partes = [p for p in re.split(separador, texto) if p]
        if len(partes) > 1:
            pedacos: list[str] = []
            for parte in partes:
                if pedacos and len(pedacos[-1]) + 1 + len(parte) <= limite:
                    pedacos[-1] += " " + parte
                else:
                    pedacos.append(parte)
            return [q for p in pedacos for q in dividir(p, limite)]
    return [texto[i : i + limite] for i in range(0, len(texto), limite)]  # uma "palavra" gigante (um link)


PAUSA_ENTRE_FRASES_S = 0.14  # o Piper emendava uma frase na outra: soava apressado
VOLUME_ALVO_RMS = 0.08  # ~-22 dBFS: o mesmo volume em toda frase (o pico sozinho deixava umas mais baixas)
LIMITE_PICO = 0.95
SILENCIO = 0.008  # abaixo disso, nas pontas, é silêncio


def acabamento(audio: np.ndarray, taxa: int) -> np.ndarray:
    """Tira o silêncio das pontas (a 1ª palavra sai antes), suaviza começo e fim (sem estalo entre pedaços) e
    deixa todas as frases no mesmo volume, sem estourar."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    alto = np.flatnonzero(np.abs(audio) > SILENCIO)
    if alto.size == 0:
        return np.zeros(0, dtype=np.float32)
    margem = int(taxa * 0.03)
    audio = audio[max(0, alto[0] - margem) : min(audio.size, alto[-1] + margem)].copy()
    rms = float(np.sqrt(np.mean(audio**2)))
    if rms > 1e-6:
        ganho = VOLUME_ALVO_RMS / rms
        pico = float(np.max(np.abs(audio))) * ganho
        if pico > LIMITE_PICO:
            ganho *= LIMITE_PICO / pico
        audio *= min(ganho, 8.0)  # frase quase muda não vira chiado amplificado
    borda = min(int(taxa * 0.008), audio.size // 2)
    if borda > 0:
        rampa = np.linspace(0.0, 1.0, borda, dtype=np.float32)
        audio[:borda] *= rampa
        audio[-borda:] *= rampa[::-1]
    return audio


class Voz:
    def __init__(self, arquivo_modelo: Path, velocidade: float = 1.0, deterministico: bool = False,
                 variacao: float | None = None, variacao_ritmo: float | None = None, pausa_s: float | None = None):
        """`variacao` (noise_scale) e `variacao_ritmo` (noise_w): quanto a entonação e o ritmo mudam de uma síntese
        para outra. None = o padrão do modelo (0,667 / 0,8). Menor = mais estável, maior = mais solto."""
        from piper import PiperVoice, SynthesisConfig

        if not arquivo_modelo.exists():
            raise FileNotFoundError(f"voz não encontrada: {arquivo_modelo} (rode scripts/baixar_modelos.py)")
        self.voz = PiperVoice.load(arquivo_modelo)
        self.taxa = self.voz.config.sample_rate
        # O Piper sorteia variações a cada síntese (mais natural); nos testes, o mesmo texto dá o mesmo áudio.
        if deterministico:
            ruido = {"noise_scale": 0.0, "noise_w_scale": 0.0}
        else:
            ruido = {k: v for k, v in (("noise_scale", variacao), ("noise_w_scale", variacao_ritmo)) if v is not None}
        self.cfg = SynthesisConfig(length_scale=velocidade, **ruido)
        self.pausa = np.zeros(int(self.taxa * (PAUSA_ENTRE_FRASES_S if pausa_s is None else pausa_s)), np.float32)

    def sintetizar(self, texto: str, normalizar: bool = True) -> np.ndarray:
        """Devolve float32 mono em self.taxa Hz."""
        texto = para_fala(texto) if normalizar else texto
        if not texto:
            return np.zeros(0, dtype=np.float32)
        partes = [acabamento(c.audio_float_array, self.taxa) for c in self.voz.synthesize(texto, syn_config=self.cfg)]
        partes = [p for p in partes if p.size]
        if not partes:
            return np.zeros(0, dtype=np.float32)
        com_pausas = [x for p in partes for x in (p, self.pausa)][:-1]  # pausa entre as frases, não no fim
        return np.concatenate(com_pausas).astype(np.float32)


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
            partes = []
            with torch.inference_mode():
                for pedaco in dividir(texto):
                    saida = self.modelo.inference(pedaco, self.idioma, self.latente, self.timbre,
                                                  speed=self.velocidade, enable_text_splitting=False)
                    partes.append(np.asarray(saida["wav"], dtype=np.float32))
        return np.concatenate(partes) if partes else np.zeros(0, dtype=np.float32)

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
                for trecho in dividir(texto):
                    for pedaco in self.modelo.inference_stream(trecho, self.idioma, self.latente, self.timbre,
                                                               stream_chunk_size=self.pedaco_tokens,
                                                               speed=self.velocidade,
                                                               enable_text_splitting=False):
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

    def numero(chave: str) -> float | None:
        valor = cfg.get(chave)
        return None if valor is None else float(valor)

    return Voz(cfg.modelos / "piper" / f"{nome}.onnx", float(cfg.get("voz.velocidade_fala", 1.0)),
               variacao=numero("voz.piper_variacao"), variacao_ritmo=numero("voz.piper_variacao_ritmo"),
               pausa_s=numero("voz.pausa_entre_frases_s"))


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
