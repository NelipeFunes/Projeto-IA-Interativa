"""`vision teste voz`: arquivos, dispositivos, nível do microfone e (se você estiver no terminal)
calibração da palavra de ativação e comparação dos dois Parakeet com a sua voz."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import numpy as np

from vision.config import Config


def _gravar(dispositivo, segundos: float) -> np.ndarray:
    import sounddevice as sd

    audio = sd.rec(int(16000 * segundos), samplerate=16000, channels=1, dtype="int16", device=dispositivo)
    sd.wait()
    return audio[:, 0]


def _dbfs(pcm: np.ndarray) -> float:
    rms = np.sqrt(np.mean((pcm.astype(np.float32) / 32768) ** 2)) if pcm.size else 0.0
    return 20 * np.log10(max(rms, 1e-9))


async def rodar(cfg: Config, p: Callable[[str, str], None], OK: str, ERRO: str, AVISO: str) -> bool:
    from vision.voice.audio import achar_dispositivo, escolher_microfone

    ok = True
    voz_nome = cfg.get("voz.voz_piper", "pt_BR-faber-medium")
    arquivos = {
        f"voz {voz_nome}": cfg.modelos / "piper" / f"{voz_nome}.onnx",
        f"STT {cfg.get('voz.stt')}": cfg.modelos / cfg.get("voz.stt", "parakeet-base-int8"),
        "silero VAD": cfg.modelos / "openwakeword" / "silero_vad.onnx",
    }
    for nome, caminho in arquivos.items():
        existe = caminho.exists()
        p(OK if existe else ERRO, f"{nome}: {'ok' if existe else 'FALTANDO ' + str(caminho)}")
        ok &= existe
    if not ok:
        p(AVISO, "rode: .venv\\Scripts\\python scripts\\baixar_modelos.py")
        return False

    if cfg.get("voz.ativacao", "transcricao") in ("modelo", "ambos"):
        from vision.voice.loop import carregar_ativacao

        mensagens: list[str] = []
        ativacao, _ = carregar_ativacao(cfg, mensagens.append)
        p(OK if ativacao is not None else AVISO, mensagens[-1].removeprefix("[aviso] "))
        if ativacao is not None and not ativacao.com_verificador:
            p(AVISO, "sem o verificador da sua voz: rode scripts/ativacao/gravar_minha_voz.py")

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
    from vision.voice.tts import carregar_voz

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
    from vision.voice.audio import BLOCO, Saida, bipe

    saida = Saida(cfg)
    print("\nVou tocar um bipe e uma frase no alto-falante.")
    saida.tocar(bipe(True), 22050)
    saida.tocar(audio, voz.taxa)
    if input("Ouviu o bipe e a frase? Digite s ou n e aperte Enter: ").strip().lower().startswith("n"):
        p(ERRO, f"sem som em '{nome_alto}'. Ajuste voz.alto_falante no config.yaml")
        ok = False

    if cfg.get("voz.ativacao", "transcricao") != "modelo":
        ok = _calibrar_nome(cfg, p, OK, AVISO, ok, mic)
        return _comparar_stt(cfg, p, OK, ERRO, mic, ok)

    from vision.voice.wake import PalavraAtivacao

    palavra = str(cfg.get("voz.palavra_ativacao", "hey_vision"))
    nome = palavra.replace("_", " ").title()  # "hey_vision" → "Hey Vision"
    try:
        ativ = PalavraAtivacao(cfg.modelos / "openwakeword", palavra, limiar=1.0,
                               limiar_verificador=float(cfg.get("voz.limiar_verificador", 0.1)))
    except Exception as e:  # noqa: BLE001 - sem o modelo, o resto do diagnóstico continua
        p(ERRO, f"modelo de ativação '{palavra}' indisponível: {e}")
        return _comparar_stt(cfg, p, OK, ERRO, mic, False)

    def medir() -> float:
        pcm = _gravar(mic, 3.0)
        ativ.zerar()
        pico = 0.0
        for j in range(0, len(pcm) - BLOCO + 1, BLOCO):
            ativ.ouvir(pcm[j : j + BLOCO])
            pico = max(pico, ativ.ultimo_score)
        return pico

    # Uma tentativa perdida (falou fora da janela de 3 s) dá ~0,01 e não diz nada sobre a sua voz:
    # em 30/09 um 0,01 assim puxou a sugestão para baixo e gerou um alarme falso. Repete essas.
    print(f"\nAgora 3 vezes '{nome}'. Depois de apertar Enter você tem 3 segundos.")
    scores = []
    for i in range(3):
        for tentativa in range(2):
            input(f"\n[{i + 1}/3] Aperte Enter e diga '{nome}'...")
            pico = medir()
            print(f"   score: {pico:.2f}")
            if pico >= 0.1 or tentativa == 1:
                break
            print(f"   não ouvi '{nome}' nessa; vamos repetir esta.")
        scores.append(pico)
    validos = [s for s in scores if s >= 0.1]
    atual = float(cfg.get("voz.limiar_ativacao", 0.5))
    if not validos:
        p(AVISO, f"'{nome}' não foi reconhecido (scores {', '.join(f'{s:.2f}' for s in scores)}). "
                 "Tente falar mais perto do microfone, ou use o atalho.")
    else:
        sugerido = max(0.15, min(0.5, round(min(validos) * 0.6, 2)))
        pegou = sum(s >= atual for s in scores)
        p(OK if pegou == len(scores) else AVISO,
          f"'{nome}': scores {', '.join(f'{s:.2f}' for s in scores)}; com o limiar atual ({atual}) "
          f"pegaria {pegou} de {len(scores)}; sugerido: {sugerido}")
    preferido = (cfg.get("voz.microfone") or [None])[0]
    if preferido:
        from vision.voice.audio import achar_dispositivo

        disp_preferido, _ = achar_dispositivo([preferido], entrada=True)
        if disp_preferido is not None and mic != disp_preferido:
            p(AVISO, f"calibrado com o microfone reserva: o '{preferido}' estava mudo. "
                     "Com ele (mais perto da boca) os scores tendem a subir")

    return _comparar_stt(cfg, p, OK, ERRO, mic, ok)


def _calibrar_nome(cfg, p, OK, AVISO, ok, mic) -> bool:
    """Ativação por transcrição: você diz "Hey Vision" 3 vezes e vê o que o Parakeet entendeu."""
    from vision.voice.comandos import achar_ativacao, e_despedida
    from vision.voice.stt import carregar_transcritor

    nome = cfg.get("assistente.nome", "Vision")
    stt = carregar_transcritor(cfg)
    print(f"\nAgora 3 vezes 'Hey {nome}'. Depois de apertar Enter você tem 3 segundos.")
    acertos = 0
    for i in range(3):
        input(f"\n[{i + 1}/3] Aperte Enter e diga 'Hey {nome}'...")
        texto = stt.transcrever(_gravar(mic, 3.0))
        acordou = achar_ativacao(texto) is not None
        acertos += acordou
        print(f"   entendi: \"{texto}\" → {'acordaria' if acordou else 'NÃO acordaria'}")
    p(OK if acertos == 3 else AVISO, f"'Hey {nome}': acordaria em {acertos} de 3"
      + ("" if acertos == 3 else ". Mande o que ele entendeu: dá para ensinar a grafia em voice/comandos.py"))
    input(f"\nAperte Enter e diga '{nome}, standby' (3 s)...")
    texto = stt.transcrever(_gravar(mic, 3.0))
    desligaria = e_despedida(texto)
    p(OK if desligaria else AVISO, f"despedida: \"{texto}\" → {'encerraria' if desligaria else 'NÃO encerraria'}")
    return ok and acertos > 0


def _comparar_stt(cfg, p, OK, ERRO, mic, ok) -> bool:
    from vision.voice.stt import Transcritor

    nome = cfg.get("assistente.nome", "Vision")
    input(f"\nAperte Enter e diga: '{nome}, quanto eu gastei com iFood esse mês?' (5 s)...")
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
