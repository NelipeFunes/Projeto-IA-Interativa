"""Iniciar com o Windows: um atalho "Vision.lnk" na pasta Inicializar do usuário.

O atalho é criado pelo próprio núcleo quando ele roda no seu usuário. Criado de dentro do app do Claude
(MSIX), cairia na pasta virtualizada do app e o Windows nunca o veria.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NOME_ATALHO = "Vision.lnk"
ICONE = Path(__file__).parent / "recursos" / "vision.ico"

# Os caminhos chegam por variáveis de ambiente: nada de montar comando do PowerShell com texto.
_SCRIPT = (
    "$a = (New-Object -ComObject WScript.Shell).CreateShortcut($env:VISION_LNK);"
    "$a.TargetPath = $env:VISION_ALVO; $a.Arguments = $env:VISION_ARGS;"
    "$a.WorkingDirectory = $env:VISION_PASTA; $a.WindowStyle = 7; $a.Description = 'Vision';"
    "if ($env:VISION_ICONE) { $a.IconLocation = $env:VISION_ICONE + ',0' };"
    "$a.Save()"
)


def pasta_inicializar() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def atalho() -> Path:
    return pasta_inicializar() / NOME_ATALHO


def alvo() -> tuple[Path, str]:
    """O que o atalho executa: o visionw.exe do ambiente (sem console) ou, na falta, pythonw -m vision.nucleo."""
    scripts = Path(sys.executable).parent
    visionw = scripts / "visionw.exe"
    if visionw.exists():
        return visionw, ""
    pythonw = scripts / "pythonw.exe"
    return (pythonw if pythonw.exists() else Path(sys.executable)), "-m vision.nucleo"


def ativo() -> bool:
    return atalho().exists()


def ligar(pasta_projeto: Path) -> Path:
    destino = atalho()
    destino.parent.mkdir(parents=True, exist_ok=True)
    exe, args = alvo()
    env = {**os.environ, "VISION_LNK": str(destino), "VISION_ALVO": str(exe), "VISION_ARGS": args,
           "VISION_PASTA": str(pasta_projeto), "VISION_ICONE": str(ICONE) if ICONE.exists() else ""}
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
        env=env, check=True, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return destino


def desligar() -> None:
    atalho().unlink(missing_ok=True)


# Sobe quando o atalho muda (2: ganhou o ícone do orbe). Um atalho de versão anterior é refeito UMA vez,
# não a cada boot: o PowerShell custa tempo, e o atalho não pode seguir o Python de cada teste (revisão do PR 19).
VERSAO_ATALHO = "2"
_TEXTO_MARCADOR = "o início com o Windows já foi configurado uma vez; mude pela bandeja\nversao={}\n"


def primeira_vez(marcador: Path, pasta_projeto: Path) -> bool:
    """Na primeira execução do núcleo, liga o início com o Windows (o pedido original). Depois, vale a bandeja;
    um atalho de versão anterior é refeito uma vez (pega o ícone novo)."""
    if marcador.exists():
        if f"versao={VERSAO_ATALHO}" not in marcador.read_text(encoding="utf-8"):
            if ativo():
                ligar(pasta_projeto)
            marcador.write_text(_TEXTO_MARCADOR.format(VERSAO_ATALHO), encoding="utf-8")
        return False
    ligar(pasta_projeto)
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.write_text(_TEXTO_MARCADOR.format(VERSAO_ATALHO), encoding="utf-8")
    return True
