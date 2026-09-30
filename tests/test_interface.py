"""Servidor estático da janela: só 127.0.0.1, sem listar pastas, sem sair da pasta da build."""

import urllib.error
import urllib.request

import pytest

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
        status, corpo = _get(porta, "/../segredo.txt")
        assert "não pode sair" not in corpo
        status, corpo = _get(porta, "/%2e%2e/segredo.txt")
        assert "não pode sair" not in corpo
    finally:
        servidor.shutdown()
