"""Gera o Vision.exe (lançador leve) na pasta do projeto.

    python scripts/gerar_exe.py

Usa o csc.exe que já vem com o Windows (.NET Framework 4): sem Visual Studio, sem pacote novo. O resultado
(`Vision.exe`, uns 170 KB, quase tudo o ícone) fica fora do git e só funciona dentro desta pasta, porque liga o `.venv` daqui.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
FONTE = RAIZ / "scripts" / "lancador" / "Vision.cs"
ICONE = RAIZ / "src" / "vision" / "recursos" / "vision.ico"
SAIDA = RAIZ / "Vision.exe"


def achar_csc() -> Path | None:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for arquitetura in ("Framework64", "Framework"):
        csc = windir / "Microsoft.NET" / arquitetura / "v4.0.30319" / "csc.exe"
        if csc.exists():
            return csc
    return None


def comando(csc: Path, saida: Path = SAIDA) -> list[str]:
    return [str(csc), "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{ICONE}", f"/out:{saida}",
            "/reference:System.Windows.Forms.dll", str(FONTE)]


def gerar(saida: Path = SAIDA) -> Path:
    csc = achar_csc()
    if csc is None:
        raise SystemExit("Não achei o csc.exe do .NET Framework 4 (vem com o Windows 10/11).")
    for arquivo in (FONTE, ICONE):
        if not arquivo.exists():
            raise SystemExit(f"Falta {arquivo}")
    r = subprocess.run(comando(csc, saida), capture_output=True, text=True, cwd=RAIZ)
    if r.returncode != 0:
        raise SystemExit("O compilador falhou:\n" + (r.stdout + r.stderr).strip())
    return saida


if __name__ == "__main__":
    exe = gerar()
    print(f"Gerado: {exe} ({exe.stat().st_size / 1024:.1f} KB)")
    sys.exit(0)
