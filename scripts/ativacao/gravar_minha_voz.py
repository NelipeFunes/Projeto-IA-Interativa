"""Grava a sua voz para o "Hey Vision" pelo modelo, treina o verificador e mostra as notas.

O modelo `hey_vision.onnx` (treinado no Colab com vozes sintéticas) nunca ouviu você. O verificador é um 2º filtro,
treinado aqui em segundos com as suas gravações: quando o modelo acha que pode ser o nome (nota a partir de
`voz.limiar_verificador`), é ele quem decide se foi você chamando. Corrige o sotaque e corta a TV.

Uso (.venv\\Scripts\\python scripts\\ativacao\\gravar_minha_voz.py ...):
  gravar    grava 20 "Hey Vision" e 4 frases normais (pode rodar de novo: soma às anteriores)
  treinar   treina o verificador (precisa de modelos/openwakeword/hey_vision.onnx) e mostra as notas
  medir     só mostra as notas do modelo (e do verificador, se houver) nas suas gravações
Sem nada: gravar e, se o modelo existir, treinar.

As gravações ficam em data/ativacao/minha_voz/ (fora do git) e não vão para lugar nenhum.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))

from vision import config
from vision.voice.wake import LIMIAR_VERIFICADOR, SUFIXO_VERIFICADOR, abrir_modelo

TAXA = 16000
BLOCO = 1280
PASTA = RAIZ / "data" / "ativacao" / "minha_voz"
CHAMADAS = PASTA / "chamadas"
OUTRAS = PASTA / "outras"
VEZES = 20
SEGUNDOS_CHAMADA = 2.5
# Frases para ler em voz normal: o verificador aprende como é a sua voz quando NÃO está chamando. Têm palavras
# parecidas com o nome de propósito (visão, televisão, revisão), que não devem acordar.
FRASES = [
    "A televisão da sala está ligada, mas ninguém está assistindo nada.",
    "Fiz a revisão do texto inteiro e mandei a versão final por e-mail.",
    "Tenho uma visão diferente sobre isso, mas a decisão é sua, pode escolher.",
    "Amanhã cedo eu passo no mercado e compro pão, café e algumas frutas.",
]
SEGUNDOS_FRASE = 7.0
DICAS = ["normal, perto do microfone", "um pouco mais baixo", "mais rápido, emendado", "mais devagar",
         "um pouco mais longe do microfone", "do jeito que você chamaria de verdade"]


def _salvar(pasta: Path, pcm: np.ndarray) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    numero = max((int(p.stem) for p in pasta.glob("*.wav") if p.stem.isdigit()), default=0) + 1
    caminho = pasta / f"{numero:03d}.wav"
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return caminho


def _ler(caminho: Path) -> np.ndarray:
    with wave.open(str(caminho)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


def gravar(cfg: config.Config) -> None:
    import sounddevice as sd

    from vision.voice.audio import bipe, escolher_microfone

    mic, nome, notas = escolher_microfone(cfg.get("voz.microfone", []))
    for nota in notas:
        print(f"  microfone {nota}")
    print(f"Microfone: {nome}. Grave no lugar e no microfone em que você usa o Vision.\n")

    def ouvir(segundos: float) -> np.ndarray:
        sd.play(bipe(subindo=True), 22050)
        sd.wait()
        audio = sd.rec(int(TAXA * segundos), samplerate=TAXA, channels=1, dtype="int16", device=mic)
        sd.wait()
        return audio[:, 0]

    print(f'Parte 1: {VEZES} vezes "Hey Vision". Depois do bipe você tem {SEGUNDOS_CHAMADA:.1f} s.')
    feitas = 0
    while feitas < VEZES:
        dica = DICAS[feitas % len(DICAS)]
        input(f'\n[{feitas + 1}/{VEZES}] Enter e diga "Hey Vision" ({dica})...')
        pcm = ouvir(SEGUNDOS_CHAMADA)
        if np.abs(pcm).max() < 300:  # nada além de ruído: o microfone está mudo ou você falou fora da janela
            print("   não ouvi nada; vamos repetir esta.")
            continue
        _salvar(CHAMADAS, pcm)
        feitas += 1
    print(f"\nParte 2: leia {len(FRASES)} frases em voz normal ({SEGUNDOS_FRASE:.0f} s cada, depois do bipe).")
    for i, frase in enumerate(FRASES):
        input(f'\n[{i + 1}/{len(FRASES)}] Enter e leia: "{frase}"')
        _salvar(OUTRAS, ouvir(SEGUNDOS_FRASE))
        time.sleep(0.2)
    print(f"\nPronto: {len(list(CHAMADAS.glob('*.wav')))} chamadas e {len(list(OUTRAS.glob('*.wav')))} frases em {PASTA}")


def _modelo(cfg: config.Config) -> tuple[Path, Path]:
    pasta = cfg.modelos / "openwakeword"
    nome = cfg.get("voz.palavra_ativacao", "hey_vision")
    arquivo = next(pasta.glob(f"{nome}*.onnx"), None)
    if arquivo is None:
        raise SystemExit(f"falta o modelo {pasta / (nome + '.onnx')}: treine no Colab (scripts/ativacao/preparar_treino.py)")
    return pasta, arquivo


def _picos(oww, clipes: list[Path]) -> list[float]:
    """A maior nota que cada clipe tirou, bloco a bloco, como o Vision ouve."""
    nome = next(iter(oww.models))
    picos = []
    for clipe in clipes:
        oww.reset()
        pcm = np.concatenate([np.zeros(TAXA, np.int16), _ler(clipe), np.zeros(TAXA // 2, np.int16)])
        pico = 0.0
        for j in range(0, len(pcm) - BLOCO + 1, BLOCO):
            pico = max(pico, float(oww.predict(pcm[j : j + BLOCO])[nome]))
        picos.append(pico)
    return picos


def _resumo(rotulo: str, chamadas: list[float], outras: list[float]) -> None:
    print(f"\n{rotulo}")
    print("  chamadas: " + " ".join(f"{p:.2f}" for p in chamadas))
    print("  frases:   " + " ".join(f"{p:.2f}" for p in outras))
    for limiar in (0.3, 0.5, 0.7):
        pegou = sum(p >= limiar for p in chamadas)
        falsos = sum(p >= limiar for p in outras)
        print(f"  limiar {limiar}: acordaria em {pegou} de {len(chamadas)} chamadas; {falsos} das frases acordariam")


def _clipes() -> tuple[list[Path], list[Path]]:
    chamadas, outras = sorted(CHAMADAS.glob("*.wav")), sorted(OUTRAS.glob("*.wav"))
    if len(chamadas) < 3 or not outras:
        raise SystemExit("poucas gravações: rode primeiro com 'gravar'")
    return chamadas, outras


def medir(cfg: config.Config) -> None:
    pasta, arquivo = _modelo(cfg)
    chamadas, outras = _clipes()
    oww = abrir_modelo(pasta, arquivo)
    _resumo(f"Só o modelo {arquivo.stem}:", _picos(oww, chamadas), _picos(oww, outras))
    verificador = pasta / f"{arquivo.stem}{SUFIXO_VERIFICADOR}"
    if verificador.is_file():
        limiar = float(cfg.get("voz.limiar_verificador", LIMIAR_VERIFICADOR))
        oww = abrir_modelo(pasta, arquivo, verificador, limiar)
        _resumo("Com o verificador (treinado com estas mesmas gravações: a nota real fica um pouco abaixo):",
                _picos(oww, chamadas), _picos(oww, outras))


def _treinar_verificador(pasta: Path, arquivo: Path, chamadas: list[Path], outras: list[Path], destino: Path,
                         limiar: float) -> None:
    """Como o openwakeword.train_custom_verifier, mas pegando os trechos das chamadas a partir de `limiar` (lá é fixo
    em 0,5): com o seu sotaque o modelo pode dar menos que isso, e é justamente aí que o verificador ajuda."""
    import pickle

    from openwakeword.custom_verifier_model import get_reference_clip_features, train_verifier_model

    oww = abrir_modelo(pasta, arquivo)
    nome = next(iter(oww.models))
    positivos = np.vstack([get_reference_clip_features(_ler(c), oww, nome, threshold=limiar, N=5) for c in chamadas])
    if positivos.shape[0] == 0:
        raise SystemExit(f"o modelo não passou de {limiar} em nenhuma chamada sua: o verificador não tem o que aprender. "
                         "Retreine o modelo no Colab (ou grave mais perto do microfone).")
    negativos = np.vstack([get_reference_clip_features(_ler(c), oww, nome, threshold=0.0, N=1) for c in outras])
    rotulos = np.array([1] * positivos.shape[0] + [0] * negativos.shape[0])
    modelo = train_verifier_model(np.vstack((positivos, negativos)), rotulos)
    tmp = destino.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(modelo, f)
    tmp.replace(destino)


def treinar(cfg: config.Config) -> None:
    pasta, arquivo = _modelo(cfg)
    chamadas, outras = _clipes()
    limiar = float(cfg.get("voz.limiar_verificador", LIMIAR_VERIFICADOR))
    destino = pasta / f"{arquivo.stem}{SUFIXO_VERIFICADOR}"
    base = abrir_modelo(pasta, arquivo)
    _resumo(f"Só o modelo {arquivo.stem}:", _picos(base, chamadas), _picos(base, outras))
    # Prova honesta: treina sem 1 de cada 4 chamadas e mede nelas (o verificador nunca as ouviu).
    fora = chamadas[3::4]
    if fora:
        dentro = [c for c in chamadas if c not in fora]
        _treinar_verificador(pasta, arquivo, dentro, outras, destino, limiar)
        oww = abrir_modelo(pasta, arquivo, destino, limiar)
        _resumo(f"Com o verificador, nas {len(fora)} chamadas que ele não ouviu no treino:",
                _picos(oww, fora), _picos(oww, outras))
    _treinar_verificador(pasta, arquivo, chamadas, outras, destino, limiar)  # o final usa tudo
    print(f"\nVerificador salvo em {destino}. Reinicie o Vision para valer.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("acao", nargs="?", choices=["gravar", "treinar", "medir"])
    args = ap.parse_args()
    cfg = config.carregar()
    if args.acao in (None, "gravar"):
        gravar(cfg)
    if args.acao == "treinar" or (args.acao is None and any((cfg.modelos / "openwakeword").glob(
            f"{cfg.get('voz.palavra_ativacao', 'hey_vision')}*.onnx"))):
        treinar(cfg)
    if args.acao == "medir":
        medir(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
