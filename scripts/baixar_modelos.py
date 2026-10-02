"""Baixa os modelos de voz para `modelos/` (idempotente: pula o que já existe).

Uso: uv run python scripts/baixar_modelos.py [--sem-ptbr] [--xtts]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

RAIZ = Path(__file__).resolve().parent.parent
MODELOS = RAIZ / "modelos"

VOZES_PIPER = [
    "pt/pt_BR/faber/medium/pt_BR-faber-medium",
    "pt/pt_BR/cadu/medium/pt_BR-cadu-medium",
    "pt/pt_BR/jeff/medium/pt_BR-jeff-medium",
    "pt/pt_BR/edresson/low/pt_BR-edresson-low",
    # Só para testar o "hey jarvis" sem microfone: o modelo de ativação foi treinado em inglês.
    "en/en_US/lessac/medium/en_US-lessac-medium",
]

PARAKEET_PTBR = "alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx"
PARAKEET_BASE = "istupakov/parakeet-tdt-0.6b-v3-onnx"


def baixar_piper() -> None:
    destino = MODELOS / "piper"
    destino.mkdir(parents=True, exist_ok=True)
    for base in VOZES_PIPER:
        nome = base.rsplit("/", 1)[-1]
        for ext in (".onnx", ".onnx.json"):
            alvo = destino / f"{nome}{ext}"
            if alvo.exists():
                continue
            caminho = hf_hub_download("rhasspy/piper-voices", f"{base}{ext}", local_dir=MODELOS / "_hf_piper")
            Path(caminho).replace(alvo)
        print(f"piper: {nome} ok")


def baixar_parakeet(incluir_ptbr: bool) -> None:
    # Base multilíngue em int8 (~650 MB): rápida na CPU, serve de plano B.
    snapshot_download(
        PARAKEET_BASE,
        local_dir=MODELOS / "parakeet-base-int8",
        allow_patterns=["*.int8.onnx", "nemo128.onnx", "vocab.txt", "config.json"],
    )
    print("parakeet base int8 ok")
    if incluir_ptbr:
        # Ajuste fino em PT-BR (TAGARELA), só existe em fp32 (~2,5 GB).
        snapshot_download(PARAKEET_PTBR, local_dir=MODELOS / "parakeet-ptbr")
        print("parakeet pt-br ok")


def baixar_wakeword() -> None:
    import openwakeword.utils as oww

    destino = MODELOS / "openwakeword"
    destino.mkdir(parents=True, exist_ok=True)
    oww.download_models(model_names=["hey_jarvis"], target_directory=str(destino))
    print("openwakeword ok:", sorted(p.name for p in destino.iterdir()))


def baixar_xtts() -> None:
    # Voz natural (voz.motor: xtts), ~1,9 GB. Licença Coqui Public Model License: só uso não comercial.
    # Revisão fixa (a última, de 12/2023): o repositório da Coqui está parado, mas não é nosso.
    snapshot_download("coqui/XTTS-v2", revision="6c2b0d75eae4b7047358e3b6bd9325f857d43f77",
                      local_dir=MODELOS / "xtts_v2",
                      allow_patterns=["model.pth", "config.json", "vocab.json", "speakers_xtts.pth"])
    print("xtts-v2 ok")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-ptbr", action="store_true", help="não baixa o Parakeet PT-BR de 2,5 GB")
    ap.add_argument("--xtts", action="store_true",
                    help="baixa também o XTTS-v2 (1,9 GB; licença CPML, só uso não comercial)")
    args = ap.parse_args()
    MODELOS.mkdir(exist_ok=True)
    baixar_piper()
    baixar_wakeword()
    baixar_parakeet(incluir_ptbr=not args.sem_ptbr)
    if args.xtts:
        baixar_xtts()
    return 0


if __name__ == "__main__":
    sys.exit(main())
