"""`jarvis teste voz`: arquivos, dispositivos, nível do microfone e (se você estiver no terminal)
calibração do 'Hey Jarvis' e comparação dos dois Parakeet com a sua voz."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import numpy as np

from jarvis.config import Config


def _gravar(dispositivo, segundos: float) -> np.ndarray:
    import sounddevice as sd

    audio = sd.rec(int(16000 * segundos), samplerate=16000, channels=1, dtype="int16", device=dispositivo)
    sd.wait()
    return audio[:, 0]


def _dbfs(pcm: np.ndarray) -> float:
    rms = np.sqrt(np.mean((pcm.astype(np.float32) / 32768) ** 2)) if pcm.size else 0.0
    return 20 * np.log10(max(rms, 1e-9))


async def rodar(cfg: Config, p: Callable[[str, str], None], OK: str, ERRO: str, AVISO: str) -> bool:
    from jarvis.voice.audio import achar_dispositivo, escolher_microfone

    ok = True
    voz_nome = cfg.get("voz.voz_piper", "pt_BR-faber-medium")
    arquivos = {
        f"voz {voz_nome}": cfg.modelos / "piper" / f"{voz_nome}.onnx",
        f"STT {cfg.get('voz.stt')}": cfg.modelos / cfg.get("voz.stt", "parakeet-base-int8"),
        "hey_jarvis": cfg.modelos / "openwakeword" / "hey_jarvis_v0.1.onnx",
        "silero VAD": cfg.modelos / "openwakeword" / "silero_vad.onnx",
    }
    for nome, caminho in arquivos.items():
        existe = caminho.exists()
        p(OK if existe else ERRO, f"{nome}: {'ok' if existe else 'FALTANDO ' + str(caminho)}")
        ok &= existe
    if not ok:
        p(AVISO, "rode: uv run python scripts/baixar_modelos.py")
        return False

    mic, nome_mic, notas = escolher_microfone(cfg.get("voz.microfone", []))
    alto, nome_alto = achar_dispositivo(cfg.get("voz.alto_falante", []), entrada=False)
    for nota in notas:
        p(AVISO, f"microfone {nota}")
    p(OK if mic is not None else AVISO, f"microfone escolhido: {nome_mic}" + ("" if mic is not None else " (nenhum da lista; usando o padrão)"))
    p(OK if alto is not None else AVISO, f"alto-falante: {nome_alto}")

    try:
        nivel = _dbfs(_gravar(mic, 1.0))
        if nivel < -90:
            p(ERRO, f"microfone mudo ({nivel:.0f} dBFS). Confira: Configurações > Privacidade > Microfone, e o mute do headset.")
            ok = False
        else:
            p(OK, f"microfone captando (ruído de fundo {nivel:.0f} dBFS)")
    except Exception as e:  # noqa: BLE001
        p(ERRO, f"não abriu o microfone: {e}")
        return False

    t = time.perf_counter()
    from jarvis.voice.tts import carregar_voz

    voz = carregar_voz(cfg)
    frase = "Oi, Felipe. Amanhã você tem dentista às 14:00."
    audio = voz.sintetizar(frase)
    p(OK, f"TTS: {len(audio) / voz.taxa:.1f}s de fala em {time.perf_counter() - t:.2f}s (com carga)")

    if not sys.stdin.isatty():
        p(AVISO, "sem terminal interativo: pulei a calibração com a sua voz")
        return ok
    try:
        return await _calibrar(cfg, p, OK, ERRO, AVISO, ok, mic, nome_alto, voz, audio)
    except EOFError:
        p(AVISO, "entrada fechada: pulei a calibração com a sua voz")
        return ok


async def _calibrar(cfg, p, OK, ERRO, AVISO, ok, mic, nome_alto, voz, audio) -> bool:
    from jarvis.voice.audio import BLOCO, Saida, bipe

    saida = Saida(cfg)
    print("\nVou tocar um bipe e uma frase no alto-falante.")
    saida.tocar(bipe(True), 22050)
    saida.tocar(audio, voz.taxa)
    if input("Ouviu? [s/n] ").strip().lower().startswith("n"):
        p(ERRO, f"sem som em '{nome_alto}'. Ajuste voz.alto_falante no config.yaml")
        ok = False

    from jarvis.voice.wake import PalavraAtivacao

    ativ = PalavraAtivacao(cfg.modelos / "openwakeword", limiar=1.0)
    scores = []
    for i in range(3):
        input(f"\n[{i + 1}/3] Aperte Enter e diga 'Hey Jarvis' do seu jeito normal (3 s)...")
        pcm = _gravar(mic, 3.0)
        ativ.zerar()
        pico = 0.0
        for j in range(0, len(pcm) - BLOCO + 1, BLOCO):
            ativ.ouvir(pcm[j : j + BLOCO])
            pico = max(pico, ativ.ultimo_score)
        scores.append(pico)
        print(f"   score: {pico:.2f}")
    sugerido = max(0.15, min(0.6, round(min(scores) * 0.7, 2)))
    atual = float(cfg.get("voz.limiar_ativacao", 0.3))
    p(OK if min(scores) >= atual else AVISO,
      f"'Hey Jarvis': scores {', '.join(f'{s:.2f}' for s in scores)}; limiar atual {atual}; sugerido {sugerido}")
    if min(scores) < 0.15:
        p(AVISO, "scores muito baixos: tente 'Hei Djárvis' mais em inglês, ou use o atalho (ou treine uma palavra própria depois)")

    from jarvis.voice.stt import Transcritor

    input("\nAperte Enter e diga: 'Jarvis, quanto eu gastei com iFood esse mês?' (5 s)...")
    pcm = _gravar(mic, 5.0)
    for nome in ("parakeet-base-int8", "parakeet-ptbr"):
        try:
            t = time.perf_counter()
            tr = Transcritor(cfg.modelos, nome)
            carga = time.perf_counter() - t
            t = time.perf_counter()
            texto = tr.transcrever(pcm)
            p(OK, f"{nome}: \"{texto}\" (carga {carga:.1f}s, transcrição {time.perf_counter() - t:.2f}s)")
        except Exception as e:  # noqa: BLE001
            p(ERRO, f"{nome}: {e}")
    print("   → escolha o que acertou mais e ponha em voz.stt no config.yaml")
    return ok
