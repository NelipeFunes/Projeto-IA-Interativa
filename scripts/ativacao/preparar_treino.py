"""Prepara o treino do modelo "Hey Vision" (openWakeWord) no Google Colab.

Gera, em `data/ativacao/` (fora do git):
- `positivas_ptbr.zip`: "Hey Vision" com sotaque brasileiro, sintetizado pelas vozes Piper pt-BR do Vision. O Colab
  gera milhares de amostras, mas só com vozes em inglês; estas ensinam o modelo a pegar o jeito brasileiro de falar.
- `treino_hey_vision.ipynb`: o notebook openwakeword-colab-2026 (MIT, github.com/alfiedennen/openwakeword-colab-2026,
  numa versão fixa) já com a frase, o nome do modelo e uma célula que recebe o zip acima.

Uso: .venv\\Scripts\\python scripts\\ativacao\\preparar_treino.py [--por-voz 20]
Depois: colab.research.google.com → Arquivo → Fazer upload de notebook → `treino_hey_vision.ipynb`; Ambiente de
execução → Alterar o tipo → GPU T4; Executar tudo; na 1ª célula, envie o `positivas_ptbr.zip`. No fim (~2h30) o
navegador baixa `hey_vision.onnx`: coloque em `modelos/openwakeword/`.

Nenhuma gravação da sua voz vai para o Colab: só estas vozes sintéticas.

O .ipynb é fixo (commit acima), mas o que ele instala no Colab não é: clona o openWakeWord da master e instala os
pacotes sem versão. Roda na máquina do Google, não no PC; o que volta é só o hey_vision.onnx (pesos de uma rede
pequena, sem código), e o gravar_minha_voz.py mostra as notas dele com a sua voz antes de você confiar nele.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
import wave
import zipfile
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "src"))
MODELOS = RAIZ / "modelos"
SAIDA = RAIZ / "data" / "ativacao"

MODELO = "hey_vision"
# Como as vozes em inglês do Colab vão falar. "vizhun" puxa a pronúncia "víjan" de quem fala português.
FRASES = ["hey vision", "hey vizhun"]
# Grafias que fazem as vozes pt-BR dizerem "Hey Vision" como um brasileiro (conferido transcrevendo com o Parakeet
# em 03/10: "Rei víjon" → "Hey Vision"). Sem "ã": o Piper não tem esse fonema.
GRAFIAS = ["Hey Vision", "Rei víjon", "Rêi víjan", "Ei víjon", "Hei víjun", "Hey Víjion", "Ei, víjon"]
# A edresson-low saiu irreconhecível no teste: fica de fora.
VOZES = ["pt_BR-faber-medium", "pt_BR-cadu-medium", "pt_BR-jeff-medium"]
# Parecidas com "Hey Vision" que NÃO devem acordar (somadas às que o openWakeWord gera sozinho).
NEGATIVAS = ["television", "revision", "decision", "division", "provision", "hey visa", "hey vivian", "hey listen",
             "play vision", "hey jason", "hey vinny", "envision", "hey siri", "hey google"]
TAXA = 16000
PARTE_TESTE = 0.15
# Com velocidade e tom sorteados, umas saem estragadas ("Hey", "Hey team"). Só fica o clipe em que o Parakeet ainda
# ouve o começo do nome ("vision", "visjam", "vejam"...): ensinar o modelo com "Hey" sozinho o faria acordar à toa.
TEM_O_NOME = re.compile(r"v[ie][sjzcx]")

# Versão fixa do notebook: o que muda lá não muda aqui sem a gente ver.
NOTEBOOK_URL = ("https://raw.githubusercontent.com/alfiedennen/openwakeword-colab-2026/"
                "da4d92a64fadb803d67866075af870e5161fd9eb/train_wakeword.ipynb")


def _wav(caminho: Path, pcm: np.ndarray) -> None:
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(pcm.tobytes())


def gerar_positivas(por_voz: int, semente: int = 7) -> tuple[int, int, int]:
    """`por_voz` variações de cada grafia em cada voz: velocidade, entonação e um leve desvio de tom (outro "falante").
    Ruído e eco o próprio Colab acrescenta. Devolve (treino, teste, descartadas)."""
    import unicodedata

    from piper import PiperVoice, SynthesisConfig
    from scipy.signal import resample_poly

    from vision.voice.stt import Transcritor

    stt = Transcritor(MODELOS, "parakeet-base-int8")

    def tem_o_nome(pcm: np.ndarray) -> bool:
        texto = unicodedata.normalize("NFKD", stt.transcrever(pcm, TAXA).lower())
        return bool(TEM_O_NOME.search(texto.encode("ascii", "ignore").decode()))

    rnd = random.Random(semente)
    pastas = {p: SAIDA / "positivas_ptbr" / p for p in ("treino", "teste")}
    for pasta in pastas.values():
        pasta.mkdir(parents=True, exist_ok=True)
        for antigo in pasta.glob("*.wav"):
            antigo.unlink()
    contagem = {"treino": 0, "teste": 0, "descartadas": 0}
    for nome in VOZES:
        voz = PiperVoice.load(MODELOS / "piper" / f"{nome}.onnx")
        for grafia in GRAFIAS:
            i = tentativas = 0
            while i < por_voz and tentativas < por_voz * 3:
                tentativas += 1
                cfg = SynthesisConfig(length_scale=rnd.uniform(0.8, 1.35), noise_scale=rnd.uniform(0.3, 0.9),
                                      noise_w_scale=rnd.uniform(0.4, 1.0))
                audio = np.concatenate([c.audio_float_array for c in voz.synthesize(grafia, syn_config=cfg)])
                # Reamostrar para uma taxa "errada" muda tom e ritmo juntos: soa como outra pessoa.
                desvio = rnd.uniform(0.9, 1.1)
                audio = resample_poly(audio, TAXA, round(voz.config.sample_rate * desvio))
                audio = audio / max(float(np.max(np.abs(audio))), 1e-6) * rnd.uniform(0.3, 0.9)
                silencio = np.zeros(int(TAXA * rnd.uniform(0.1, 0.4)), np.float32)
                pcm = (np.concatenate([silencio, audio, silencio]) * 32767).astype(np.int16)
                if not tem_o_nome(pcm):
                    contagem["descartadas"] += 1
                    continue
                parte = "teste" if rnd.random() < PARTE_TESTE else "treino"
                contagem[parte] += 1
                _wav(pastas[parte] / f"ptbr_{nome.split('-')[1]}_{GRAFIAS.index(grafia)}_{i:03d}.wav", pcm)
                i += 1
    with zipfile.ZipFile(SAIDA / "positivas_ptbr.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for parte, pasta in pastas.items():
            for arq in sorted(pasta.glob("*.wav")):
                z.write(arq, f"{parte}/{arq.name}")
    return contagem["treino"], contagem["teste"], contagem["descartadas"]


CELULA_ENVIO = '''# ── Vision: envie o positivas_ptbr.zip (gerado por scripts/ativacao/preparar_treino.py) ──
# Fica no começo de propósito: o "Executar tudo" para aqui esperando o arquivo, e depois segue sozinho até o fim.
import os, zipfile
from google.colab import files
if not os.path.exists('/content/positivas_ptbr.zip'):
    enviado = files.upload()
    nome = next(iter(enviado))
    if nome != 'positivas_ptbr.zip':
        os.replace(nome, '/content/positivas_ptbr.zip')
with zipfile.ZipFile('/content/positivas_ptbr.zip') as z:
    print(f"  ✓ positivas_ptbr.zip: {len(z.namelist())} clipes")
'''

CELULA_JUNTAR = '''# ── Vision: junta as amostras com sotaque brasileiro às geradas em inglês (antes do aumento de dados) ──
import os, glob, zipfile, yaml
with open('/content/my_model.yaml') as f:
    cfg = yaml.safe_load(f)
destinos = {'treino': cfg['positive_clips_train_dir'], 'teste': cfg['positive_clips_test_dir']}
with zipfile.ZipFile('/content/positivas_ptbr.zip') as z:
    for nome in z.namelist():
        parte, _, arquivo = nome.partition('/')
        arquivo = os.path.basename(arquivo)  # só o nome: nada de subpastas ou ".." vindos do zip
        if parte in destinos and arquivo.endswith('.wav'):
            with open(os.path.join(destinos[parte], arquivo), 'wb') as f:
                f.write(z.read(nome))
for parte, pasta in destinos.items():
    print(f"  ✓ {parte}: {len(glob.glob(pasta + '/ptbr_*.wav'))} clipes pt-BR em {pasta}")
for f in glob.glob(f"{cfg['output_dir']}/**/*.npy", recursive=True):
    os.remove(f)  # as features antigas não têm os clipes novos: o aumento refaz
'''


def _celula(fonte: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": fonte.splitlines(keepends=True)}


def _trocar(fonte: str, antes: str, depois: str) -> str:
    if fonte.count(antes) != 1:
        raise SystemExit(f"o notebook mudou: não achei {antes!r}")
    return fonte.replace(antes, depois)


def preparar_notebook() -> Path:
    with urllib.request.urlopen(NOTEBOOK_URL, timeout=60) as r:  # URL fixa acima
        nb = json.loads(r.read().decode("utf-8"))
    celulas = nb["cells"]

    def achar(trecho: str) -> int:
        achados = [i for i, c in enumerate(celulas) if c["cell_type"] == "code" and trecho in "".join(c["source"])]
        if len(achados) != 1:
            raise SystemExit(f"o notebook mudou: {len(achados)} células com {trecho!r}")
        return achados[0]

    i_cfg = achar("TARGET_PHRASE = ")
    fonte = "".join(celulas[i_cfg]["source"])
    fonte = _trocar(fonte, "TARGET_PHRASE = ['mr graves', 'mister graves']", f"TARGET_PHRASE = {FRASES!r}")
    fonte = _trocar(fonte, "MODEL_NAME    = 'mr_graves'", f"MODEL_NAME    = {MODELO!r}")
    fonte = _trocar(fonte, "'custom_negative_phrases': [],", f"'custom_negative_phrases': {NEGATIVAS!r},")
    fonte = _trocar(fonte, "'n_samples':       2000,", "'n_samples':       3000,")
    celulas[i_cfg]["source"] = fonte.splitlines(keepends=True)

    i_aumento = achar("--augment_clips")
    celulas.insert(i_aumento, _celula(CELULA_JUNTAR))
    i_inicio = next(i for i, c in enumerate(celulas) if c["cell_type"] == "code")
    celulas.insert(i_inicio, _celula(CELULA_ENVIO))
    for c in celulas:
        if c["cell_type"] == "code":
            c["outputs"], c["execution_count"] = [], None
    destino = SAIDA / "treino_hey_vision.ipynb"
    destino.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    return destino


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--por-voz", type=int, default=20, help="variações de cada grafia em cada voz (padrão 20)")
    args = ap.parse_args()
    faltando = [v for v in VOZES if not (MODELOS / "piper" / f"{v}.onnx").exists()]
    if faltando:
        print(f"faltam as vozes {faltando}: rode scripts/baixar_modelos.py", file=sys.stderr)
        return 1
    SAIDA.mkdir(parents=True, exist_ok=True)
    treino, teste, fora = gerar_positivas(max(1, args.por_voz))
    print(f"amostras pt-BR: {treino} de treino + {teste} de teste ({fora} estragadas descartadas) → "
          f"{SAIDA / 'positivas_ptbr.zip'}")
    print(f"notebook: {preparar_notebook()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
