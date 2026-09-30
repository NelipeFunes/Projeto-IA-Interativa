"""Janela nativa da interface (pywebview + WebView2): a tela principal sem bordas e a bolha flutuante.

Fase A: só o modo demonstração, servindo a build de `ui/dist` por um servidor estático local.
Na Fase B o núcleo passa a servir a interface em /app, com token.

Segurança da ponte JS → Python (revisão de 30/09): o pywebview resolve qualquer caminho pontilhado
vindo do JavaScript com getattr, sem filtro (webview/util.py, js_bridge_call). Com um objeto em
`js_api`, `window.pywebview._jsApiCallback("_principal.gui.os.system", ...)` chegava ao sistema, e até
um método público servia de porta (`minimizar.__func__.__globals__`). Por isso:
  - não passamos objeto nenhum em `js_api`; só funções soltas, registradas por nome exato (`expose`);
  - as janelas ficam num dicionário do módulo, que o JavaScript não alcança.
"""

from __future__ import annotations

import ctypes
import functools
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jarvis.config import Config

_JANELAS: dict[str, Any] = {}

LARGURA_BOLHA, ALTURA_BOLHA = 400, 96


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


# ------------------------------------------------------------------ Win32 (bolha sem roubar foco)

def _hwnd(titulo: str) -> int:
    """Janela de nível superior DESTE processo com esse título (0 se não achar).

    Só pelo título, uma segunda instância aberta mostraria a bolha da outra (revisão de 30/09).
    """
    if sys.platform != "win32":
        return 0
    u32 = ctypes.windll.user32
    u32.FindWindowExW.restype = ctypes.c_void_p
    u32.FindWindowExW.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
    u32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
    pid = ctypes.c_ulong()
    hwnd = None
    while hwnd := u32.FindWindowExW(None, hwnd, None, titulo):
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return hwnd
    return 0


def _mostrar_sem_foco(hwnd: int) -> None:
    """Mostra por cima de tudo SEM ativar: não tira o foco do jogo ou do app que você está usando."""
    u32 = ctypes.windll.user32
    SW_SHOWNOACTIVATE, HWND_TOPMOST = 4, -1
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
    u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    u32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)


def _cantos_arredondados(hwnd: int) -> None:
    """Windows 11 arredonda os cantos da janela (a bolha não usa transparência: ver criar_janelas)."""
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
        valor = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(valor), 4)
    except (AttributeError, OSError):
        pass


# ------------------------------------------------------------------ funções expostas ao JavaScript

def minimizar() -> None:
    _JANELAS["principal"].minimize()


def fechar() -> None:
    # Fase A (demonstração): fechar encerra. Na Fase B, fechar só esconde e o núcleo continua.
    for nome in ("bolha", "principal"):
        janela = _JANELAS.pop(nome, None)
        if janela is not None:
            janela.destroy()


def mostrar_bolha() -> None:
    janela = _JANELAS.get("bolha")
    if janela is None:
        return
    hwnd = _hwnd(janela.title)
    if not hwnd:
        # Sem o handle não dá para mostrar sem ativar, e janela.show() rouba o foco (do jogo, inclusive).
        # Melhor não mostrar a bolha do que tirar você do que está fazendo.
        print("interface: janela da bolha não encontrada; bolha não mostrada", file=sys.stderr)
        return
    _cantos_arredondados(hwnd)
    _mostrar_sem_foco(hwnd)
    janela.evaluate_js("window.__reiniciarBolha && window.__reiniciarBolha()")


def esconder_bolha() -> None:
    janela = _JANELAS.get("bolha")
    if janela is not None:
        janela.hide()


FUNCOES_EXPOSTAS = (minimizar, fechar, mostrar_bolha, esconder_bolha)


# ------------------------------------------------------------------ janelas

def _posicao_bolha(webview: Any) -> tuple[int, int]:
    """Canto inferior direito da área útil (sem a barra de tarefas) do monitor PRINCIPAL.

    No Windows o monitor principal é sempre o que começa em (0, 0). No pywebview (winforms), `frame` já
    é o `Screen.WorkingArea`, no mesmo espaço de coordenadas de x/y/width/height (os que ele espera em
    create_window), então não se divide pela escala.
    """
    telas = list(webview.screens)
    tela = next((t for t in telas if t.x == 0 and t.y == 0), telas[0])
    area = tela.frame
    if hasattr(area, "Right") and hasattr(area, "Bottom"):
        direita, baixo = area.Right, area.Bottom
    else:  # outra plataforma ou pywebview diferente: estima a barra de tarefas
        direita, baixo = tela.x + tela.width, tela.y + tela.height - 48
    return int(direita - LARGURA_BOLHA - 16), int(baixo - ALTURA_BOLHA - 16)


def criar_janelas(webview: Any, url: str, nome: str) -> None:
    principal = webview.create_window(
        nome, url, width=1280, height=800, min_size=(1040, 680),
        frameless=True, easy_drag=False, background_color="#05040d",
    )
    x, y = _posicao_bolha(webview)
    # Sem transparent=True de propósito: com ele o pywebview mostra e ativa a janela ao carregar,
    # ignorando hidden=True e focus=False (a bolha abria visível e roubava o foco).
    bolha = webview.create_window(
        f"{nome} (bolha)", url + "&janela=bolha",
        width=LARGURA_BOLHA, height=ALTURA_BOLHA, x=x, y=y,
        frameless=True, easy_drag=False, resizable=False, on_top=True, focus=False, hidden=True,
        shadow=False, background_color="#0a081c",
    )
    for janela in (principal, bolha):
        janela.expose(*FUNCOES_EXPOSTAS)
    # Alt+F4 ou "fechar janela" na barra de tarefas também encerram (senão a bolha escondida prendia o processo).
    principal.events.closed += _ao_fechar_principal
    _JANELAS.update(principal=principal, bolha=bolha)


def _ao_fechar_principal() -> None:
    _JANELAS.pop("principal", None)
    bolha = _JANELAS.pop("bolha", None)  # o ✕ (fechar) já pode ter destruído a bolha
    if bolha is not None:
        bolha.destroy()


def abrir(cfg: Config, demo: bool = True) -> None:
    import webview

    dist = cfg.raiz / "ui" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("A interface ainda não foi construída. Na pasta do projeto rode: npm --prefix ui run build")
    servidor, porta = servir(dist)
    url = f"http://127.0.0.1:{porta}/index.html?demo={'1' if demo else '0'}"
    criar_janelas(webview, url, cfg.get("assistente.nome", "Jarvis"))
    try:
        webview.start(private_mode=True)
    finally:
        servidor.shutdown()
