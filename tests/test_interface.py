"""Janela da interface: servidor estático fechado e ponte JS → Python sem porta de entrada para o sistema."""

import urllib.error
import urllib.request

import pytest

from jarvis import interface
from jarvis.interface import servir


@pytest.fixture
def pasta(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    (tmp_path / "segredo.txt").write_text("não pode sair", encoding="utf-8")
    return dist


def _get(porta: int, caminho: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}{caminho}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_serve_index_e_so_no_loopback(pasta):
    servidor, porta = servir(pasta)
    try:
        assert servidor.server_address[0] == "127.0.0.1"
        assert _get(porta, "/index.html") == (200, "<h1>ok</h1>")
    finally:
        servidor.shutdown()


def test_nao_lista_pasta_nem_sai_dela(pasta):
    servidor, porta = servir(pasta)
    try:
        assert _get(porta, "/assets/")[0] == 404
        for caminho in ("/../segredo.txt", "/%2e%2e/segredo.txt", "/..%2fsegredo.txt"):
            assert "não pode sair" not in _get(porta, caminho)[1]
    finally:
        servidor.shutdown()


# ---------------------------------------------------------------- ponte JS → Python


class _Eventos:
    def __init__(self):
        self.closed = _Lista()


class _Lista(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class _JanelaFalsa:
    def __init__(self, titulo, kwargs):
        self.title = titulo
        self.kwargs = kwargs
        self.expostas = []
        self.events = _Eventos()
        self.destruida = False

    def expose(self, *funcs):
        self.expostas += funcs

    def destroy(self):
        self.destruida = True


class _WebviewFalso:
    class _Tela:
        x = y = 0
        width, height, scale = 1920, 1080, 1.0
        frame = None

    screens = [_Tela()]

    def __init__(self):
        self.janelas = []

    def create_window(self, titulo, url, **kwargs):
        j = _JanelaFalsa(titulo, kwargs)
        self.janelas.append(j)
        return j


def _resolver_como_o_pywebview(js_api, caminho):
    """Cópia da resolução do pywebview (util.js_bridge_call → get_nested_attribute)."""
    obj = js_api
    for parte in caminho.split("."):
        obj = getattr(obj, parte, None)
        if obj is None:
            return None
    return obj


def test_ponte_so_tem_funcoes_soltas_por_nome_exato():
    wv = _WebviewFalso()
    interface.criar_janelas(wv, "http://127.0.0.1:1/index.html?demo=1", "Teste")
    try:
        for janela in wv.janelas:
            assert janela.kwargs.get("js_api") is None  # nenhum objeto atravessável
            assert {f.__name__ for f in janela.expostas} == {"minimizar", "fechar", "mostrar_bolha", "esconder_bolha"}
        # Os caminhos do ataque que a revisão encontrou não resolvem mais nada.
        for ataque in ("_principal.gui.os.system", "__init__.__globals__", "minimizar.__globals__", "servir"):
            assert _resolver_como_o_pywebview(wv.janelas[0].kwargs.get("js_api"), ataque) is None, ataque
    finally:
        interface._JANELAS.clear()


def test_bolha_nasce_escondida_sem_transparencia_e_fecha_junto():
    wv = _WebviewFalso()
    interface.criar_janelas(wv, "http://127.0.0.1:1/index.html?demo=1", "Teste")
    principal, bolha = wv.janelas
    try:
        assert bolha.kwargs["hidden"] is True and bolha.kwargs["focus"] is False
        assert not bolha.kwargs.get("transparent")  # com transparent o pywebview ignora hidden/focus
        for fn in principal.events.closed:  # Alt+F4 na principal
            fn()
        assert bolha.destruida
    finally:
        interface._JANELAS.clear()
