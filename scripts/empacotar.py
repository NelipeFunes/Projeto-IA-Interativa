"""Gera o pacote independente do Vision: `dist/Vision/Vision.exe`, sem precisar de Python instalado.

    # 1 vez: ambiente à parte, sem o torch do XTTS (uns 0,4 GB em vez de 8 GB)
    UV_PROJECT_ENVIRONMENT=.venv-pacote uv sync --no-default-groups --group empacotar
    # sempre que quiser um pacote novo
    .venv-pacote/Scripts/python scripts/empacotar.py [--modelos nenhum|ligar|copiar]

`--modelos`: o que fazer com `modelos/` e `node/` (grandes demais para o PyInstaller carregar para dentro):
  nenhum  só avisa o que falta (padrão)
  ligar   cria atalhos de pasta (junction) para os do projeto: não gasta disco, só funciona neste PC
  copiar  copia só o que o Vision usa (a voz Piper, o Parakeet, a palavra de ativação e o Node do Google
          Agenda, com o node.exe: o pacote não exige Node na outra máquina)

Nunca entram no pacote: `data/` (logins, memória, conversas), `.env` e os modelos do XTTS (voz.motor: piper).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "dist" / "Vision"
ICONE = RAIZ / "src" / "vision" / "recursos" / "vision.ico"

# Pacotes que carregam arquivos de dentro de si ou importam módulos pelo nome: o PyInstaller não acha sozinho.
COLETAR_TUDO = ["webview", "piper", "onnx_asr", "onnxruntime", "sounddevice", "_sounddevice_data", "pystray", "ddgs",
                "primp", "wyoming", "alexapy", "pycaw", "comtypes", "mcp", "uvicorn", "websockets", "openwakeword"]
IGNORAR = ["torch", "torchaudio", "TTS", "transformers", "matplotlib", "sympy", "tkinter", "IPython"]

# O que o pacote leva de fora do PyInstaller, a partir da pasta do projeto.
COPIAR_PASTAS = [("ui/dist", "ui/dist"), ("mcp_servers", "mcp_servers")]
COPIAR_ARQUIVOS = ["config.yaml", "README.md"]
# Como o config.yaml do projeto manda chamar o Node (no pacote, vira o node.exe da pasta node\).
NODE_NO_CONFIG = r'"%LOCALAPPDATA%\\Programs\\nodejs\\node.exe"'
NODE_NO_PACOTE = r'"node\\node.exe"'  # barra dupla: em YAML entre aspas, "\n" seria uma quebra de linha


def comando_pyinstaller(python: Path) -> list[str]:
    c = [str(python), "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "Vision",
         "--icon", str(ICONE), "--paths", str(RAIZ / "src"), "--distpath", str(RAIZ / "dist"),
         "--workpath", str(RAIZ / "build"), "--specpath", str(RAIZ / "build"),
         "--add-data", f"{RAIZ / 'src' / 'vision' / 'recursos'}{os.pathsep}vision/recursos",
         "--collect-submodules", "vision"]
    for pacote in COLETAR_TUDO:
        c += ["--collect-all", pacote]
    for pacote in IGNORAR:
        c += ["--exclude-module", pacote]
    return c + [str(RAIZ / "scripts" / "vision_exe.py")]


def modelos_necessarios(config_yaml: Path) -> list[str]:
    """Pastas de `modelos/` que o config pede (o STT escolhido, o Piper e a palavra de ativação)."""
    import yaml

    cfg = yaml.safe_load(config_yaml.read_text(encoding="utf-8")) or {}
    stt = str((cfg.get("voz") or {}).get("stt", "parakeet-base-int8"))
    return [stt, "piper", "openwakeword"]


def _ligar(origem: Path, destino: Path) -> None:
    if destino.exists() or destino.is_symlink():
        return
    # `mklink /J` não exige administrador (o link simbólico exigiria).
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(destino), str(origem)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"não consegui ligar {destino} a {origem}: {r.stdout}{r.stderr}")


def _copiar(origem: Path, destino: Path) -> None:
    if destino.exists():
        return
    shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("node_modules/.cache", "*.pyc", "__pycache__"))


def achar_node() -> Path | None:
    """O node.exe instalado (o do Google Agenda): o pacote leva uma cópia, para não exigir Node na outra máquina."""
    for candidato in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe",
                      Path(shutil.which("node") or "")):
        if candidato.is_file():
            return candidato
    return None


def tamanho_mb(pasta: Path) -> float:
    """Tamanho sem entrar em atalhos de pasta (os.walk não segue junctions)."""
    total = 0
    for atual, pastas, arquivos in os.walk(pasta):
        pastas[:] = [d for d in pastas if not (Path(atual) / d).is_junction()]
        total += sum((Path(atual) / a).stat().st_size for a in arquivos if not (Path(atual) / a).is_symlink())
    return total / 1e6


def desfazer_atalhos(saida: Path = SAIDA) -> None:
    """Antes de refazer o pacote: tira os atalhos de pasta (junction) de `modelos/` e `node/` sem seguir para dentro
    deles. Apagar a pasta do pacote por cima de um atalho apagaria os modelos de verdade."""
    for pai in (saida / "modelos", saida):
        if not pai.is_dir():
            continue
        for item in pai.iterdir():
            if item.is_junction() or item.is_symlink():
                item.rmdir() if item.is_junction() else item.unlink()


def montar_pasta(modo: str, saida: Path = SAIDA, raiz: Path = RAIZ, node_exe: Path | None = None) -> list[str]:
    """Põe ao lado do .exe o que ele lê de fora. Devolve avisos do que ficou faltando."""
    avisos: list[str] = []
    for de, para in COPIAR_PASTAS:
        origem = raiz / de
        if not origem.exists():
            avisos.append(f"falta {de} (rode `npm run build` em ui/ para a tela)" if de == "ui/dist" else f"falta {de}")
            continue
        destino = saida / para
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.json"))
    for nome in COPIAR_ARQUIVOS:
        if (raiz / nome).exists():
            shutil.copy2(raiz / nome, saida / nome)
    (saida / "data").mkdir(exist_ok=True)  # vazio: cada instalação cria os seus logins e memória
    quer = [("modelos/" + m, raiz / "modelos" / m, saida / "modelos" / m) for m in modelos_necessarios(raiz / "config.yaml")]
    quer.append(("node", raiz / "node", saida / "node"))
    (saida / "modelos").mkdir(exist_ok=True)
    for rotulo, origem, destino in quer:
        if modo == "nenhum" or not origem.exists():
            avisos.append(f"falta {rotulo} ao lado do Vision.exe" + ("" if origem.exists() else " (e nem no projeto)"))
        elif modo == "ligar":
            _ligar(origem, destino)
        else:
            _copiar(origem, destino)
    if modo == "copiar":
        # Só copiando: o node.exe vai para dentro da cópia (no modo ligar seria dentro do node/ do projeto).
        node = node_exe or achar_node()
        if node is None or not node.is_file():
            avisos.append("não achei o node.exe: o Google Agenda precisa do Node instalado")
        elif (saida / "node").is_dir():
            shutil.copy2(node, saida / "node" / "node.exe")
            config = saida / "config.yaml"
            texto = config.read_text(encoding="utf-8")
            config.write_bytes(texto.replace(NODE_NO_CONFIG, NODE_NO_PACOTE).encode("utf-8"))
    elif modo == "nenhum":
        avisos.append("o Google Agenda usa o node.exe instalado no PC (no modo copiar, o pacote leva um)")
    return avisos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modelos", choices=["nenhum", "ligar", "copiar"], default="nenhum")
    ap.add_argument("--so-pasta", action="store_true", help="não roda o PyInstaller, só monta a pasta ao lado")
    args = ap.parse_args(argv)
    if not args.so_pasta:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print("PyInstaller não está neste ambiente. Use o .venv-pacote (veja o topo deste arquivo).")
            return 1
        desfazer_atalhos()
        r = subprocess.run(comando_pyinstaller(Path(sys.executable)), cwd=RAIZ)
        if r.returncode != 0:
            return r.returncode
    avisos = montar_pasta(args.modelos)
    print(f"\nPacote em {SAIDA} ({tamanho_mb(SAIDA):.0f} MB, sem o que está ligado por atalho de pasta)")
    for a in avisos:
        print("  atenção:", a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
