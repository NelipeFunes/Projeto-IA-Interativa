"""Janela nativa da interface (pywebview + WebView2): a tela principal sem bordas e a bolha flutuante.

Fase A: só o modo demonstração, servindo a build de `ui/dist` por um servidor estático local.
Na Fase B o núcleo passa a servir a interface em /app, com token.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jarvis.config import Config


class _Silencioso(SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # sem poluir o console
        pass

    def list_directory(self, _path: Any):  # sem listagem de pastas
        self.send_error(404)
        return None


def servir(pasta: Path) -> tuple[ThreadingHTTPServer, int]:
    """Servidor estático só em 127.0.0.1, porta aleatória, restrito à pasta da build."""
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Silencioso, directory=str(pasta)))
    threading.Thread(target=servidor.serve_forever, name="interface-http", daemon=True).start()
    return servidor, servidor.server_address[1]


class Api:
    """Métodos que o JavaScript chama por window.pywebview.api.*"""

    def __init__(self) -> None:
        self.principal: Any = None
        self.bolha: Any = None

    def minimizar(self) -> None:
        self.principal.minimize()

    def fechar(self) -> None:
        # Fase A (demonstração): fechar encerra. Na Fase B, fechar só esconde e o núcleo continua.
        for janela in (self.bolha, self.principal):
            if janela is not None:
                janela.destroy()

    def mostrar_bolha(self) -> None:
        self.bolha.show()
        self.bolha.evaluate_js("window.__reiniciarBolha && window.__reiniciarBolha()")

    def esconder_bolha(self) -> None:
        self.bolha.hide()


def abrir(cfg: Config, demo: bool = True) -> None:
    import webview

    dist = cfg.raiz / "ui" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("A interface ainda não foi construída. Na pasta do projeto rode: npm --prefix ui run build")
    servidor, porta = servir(dist)
    base = f"http://127.0.0.1:{porta}/index.html?demo={'1' if demo else '0'}"
    nome = cfg.get("assistente.nome", "Jarvis")

    api = Api()
    api.principal = webview.create_window(
        nome, base, js_api=api, width=1280, height=800, min_size=(1040, 680),
        frameless=True, easy_drag=False, background_color="#05040d",
    )
    tela = webview.screens[0]
    largura, altura = 400, 104
    api.bolha = webview.create_window(
        f"{nome} (bolha)", base + "&janela=bolha", js_api=api,
        width=largura, height=altura, x=tela.width - largura - 20, y=tela.height - altura - 64,
        frameless=True, easy_drag=False, resizable=False, on_top=True, focus=False, hidden=True,
        transparent=True, shadow=False, background_color="#05040d",
    )
    try:
        webview.start(private_mode=True)
    finally:
        servidor.shutdown()
