"""Janela da interface: servidor estático fechado e ponte JS → Python sem porta de entrada para o sistema."""

import sys
import urllib.error
import urllib.request

import pytest

from vision import interface
from vision.interface import servir


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
        self.closing = _Lista()
        self.loaded = _Lista()


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

    def show(self):
        self.visivel = True

    def hide(self):
        self.visivel = False

    def restore(self):
        pass


class _WebviewFalso:
    class _Tela:
        x = y = 0
        width, height, scale = 1920, 1080, 1.0
        frame = None

    screens = [_Tela()]

    def __init__(self):
        self.janelas = []
        self.urls = []

    def create_window(self, titulo, url, **kwargs):
        j = _JanelaFalsa(titulo, kwargs)
        self.janelas.append(j)
        self.urls.append(url)
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


class _Area:  # o System.Drawing.Rectangle do Screen.WorkingArea
    def __init__(self, direita, baixo):
        self.Right, self.Bottom = direita, baixo


class _TelaFalsa:
    def __init__(self, x, y, largura, altura, frame):
        self.x, self.y, self.width, self.height, self.frame, self.scale = x, y, largura, altura, frame, 1.25


def test_bolha_vai_para_a_area_util_do_monitor_principal():
    class Wv:
        screens = [
            _TelaFalsa(-1920, 0, 1920, 1080, _Area(0, 1040)),  # secundário à esquerda
            _TelaFalsa(0, 0, 2560, 1440, _Area(2560, 1380)),  # principal, barra de tarefas de 60 px
        ]

    # Sem dividir pela escala: o WorkingArea já está no espaço de x/y que o create_window espera.
    assert interface._posicao_bolha(Wv) == (2560 - interface.LARGURA_BOLHA - 16, 1380 - interface.ALTURA_BOLHA - 16)


def test_bolha_sem_area_util_estima_a_barra_de_tarefas():
    class Wv:
        screens = [_TelaFalsa(0, 0, 1920, 1080, None)]

    assert interface._posicao_bolha(Wv) == (1920 - interface.LARGURA_BOLHA - 16, 1080 - 48 - interface.ALTURA_BOLHA - 16)


def test_sem_o_handle_a_bolha_nao_aparece_em_vez_de_roubar_o_foco(monkeypatch):
    class Bolha:
        title = "Teste (bolha)"
        mostrada = False

        def show(self):
            self.mostrada = True

        def evaluate_js(self, _js):
            raise AssertionError("não deveria animar uma bolha que não apareceu")

    bolha = Bolha()
    monkeypatch.setattr(interface, "_hwnd", lambda _titulo: 0)
    monkeypatch.setitem(interface._JANELAS, "bolha", bolha)
    interface.mostrar_bolha()
    assert not bolha.mostrada


@pytest.mark.skipif(sys.platform != "win32", reason="Win32")
def test_hwnd_so_procura_janelas_deste_processo():
    assert interface._hwnd("janela que não existe 7f3a") == 0


@pytest.fixture
def no_nucleo(monkeypatch):
    wv = _WebviewFalso()
    interface.criar_janelas(wv, "http://127.0.0.1:1/app/index.html?nucleo=1", "Teste", escondida=True, sufixo="#t=x")
    monkeypatch.setitem(interface._MODO, "nucleo", True)
    mostradas = []
    monkeypatch.setattr(interface, "mostrar_bolha", lambda: mostradas.append("bolha"))
    yield wv, mostradas
    interface._JANELAS.clear()
    interface._MODO.update(nucleo=False, principal_visivel=True)


def test_no_nucleo_a_janela_nasce_escondida_e_o_token_fica_no_fragmento(no_nucleo):
    wv, _ = no_nucleo
    principal, bolha = wv.janelas
    assert principal.kwargs["hidden"] is True
    assert "#t=x" in wv.urls[0] and wv.urls[1].endswith("&janela=bolha#t=x")  # o fragmento vem por último


def test_no_nucleo_fechar_so_esconde(no_nucleo):
    wv, _ = no_nucleo
    principal, bolha = wv.janelas
    interface.mostrar_principal()
    assert all(fn() is False for fn in principal.events.closing)  # Alt+F4: cancelado
    interface.fechar()  # o ✕ da tela
    assert principal.visivel is False and not principal.destruida and not bolha.destruida


def test_comandos_do_nucleo(no_nucleo):
    wv, mostradas = no_nucleo
    principal, bolha = wv.janelas
    assert interface.executar_comando("bolha\n") and mostradas == ["bolha"]  # janela escondida: bolha aparece
    assert interface.executar_comando("mostrar\n") and principal.visivel
    assert interface.executar_comando("bolha") and mostradas == ["bolha"]  # janela aberta: sem bolha
    assert interface.executar_comando("rm -rf /") is True  # desconhecido: ignorado
    assert interface.executar_comando("sair") is False
    assert principal.destruida and bolha.destruida


def test_se_o_nucleo_morrer_a_janela_fecha(no_nucleo):
    wv, _ = no_nucleo
    interface._ouvir_nucleo(iter(["mostrar\n"]))  # a entrada acaba sem "sair"
    assert all(j.destruida for j in wv.janelas)
