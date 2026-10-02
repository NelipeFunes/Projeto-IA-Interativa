"""O Vision como programa empacotado (PyInstaller): `Vision.exe` numa pasta, sem Python instalado.

Empacotado, a "pasta do projeto" é a do próprio .exe: ali ficam `config.yaml`, `ui/dist`, `modelos/`, `node/` e a
pasta `data/` (login do Google, memória, logs...). O código e as bibliotecas ficam na subpasta `_internal`.

Um só executável faz todos os papéis (antes eram `visionw`, `vision` e `python -m vision.cli`):

    Vision.exe                  abre a janela (liga o núcleo se ele não estiver rodando)
    Vision.exe --nucleo [...]   o núcleo em segundo plano (é o que a pasta Inicializar executa)
    Vision.exe <comando> [...]  qualquer comando do `vision` (chat, teste, google-login, interface...)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def empacotado() -> bool:
    return bool(getattr(sys, "frozen", False))


def pasta_do_programa() -> Path | None:
    """A pasta do .exe quando empacotado; None rodando do código-fonte."""
    return Path(sys.executable).resolve().parent if empacotado() else None


def comando_do_vision(*args: str) -> list[str]:
    """Como iniciar outro processo do Vision com estes argumentos (a janela, por exemplo)."""
    if empacotado():
        return [sys.executable, *args]
    return [sys.executable, "-m", "vision.cli", *args]


def principal(argv: list[str] | None = None) -> int:
    """Ponto de entrada do .exe: escolhe entre o núcleo e os comandos do `vision`."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # Sem console (janela), stdout e stderr não existem: o que escreve neles não pode derrubar o programa.
    for nome in ("stdout", "stderr"):
        if getattr(sys, nome) is None:
            setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))
    if not argv:
        argv = ["--nucleo", "--abrir"]
    if argv[0] == "--nucleo":
        from vision.nucleo import main as nucleo

        return nucleo(argv[1:])
    from vision.cli import main as cli

    return cli(argv)
