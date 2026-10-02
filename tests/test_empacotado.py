"""O Vision empacotado (PyInstaller): entrada do .exe, caminhos e a montagem da pasta do pacote."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vision import empacotado, inicializacao

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import empacotar  # noqa: E402


def _empacotado(monkeypatch, exe: Path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))


def test_comando_do_vision_nos_dois_modos(monkeypatch, tmp_path):
    assert empacotado.comando_do_vision("interface", "--nucleo") == [sys.executable, "-m", "vision.cli", "interface",
                                                                      "--nucleo"]
    _empacotado(monkeypatch, tmp_path / "Vision.exe")
    assert empacotado.comando_do_vision("interface", "--nucleo") == [str(tmp_path / "Vision.exe"), "interface",
                                                                      "--nucleo"]
    assert empacotado.pasta_do_programa() == tmp_path.resolve()


def test_atalho_de_inicializacao_aponta_para_o_proprio_exe_no_modo_nucleo(monkeypatch, tmp_path):
    _empacotado(monkeypatch, tmp_path / "Vision.exe")
    assert inicializacao.alvo() == (tmp_path / "Vision.exe", "--nucleo")


def test_entrada_do_exe_escolhe_entre_nucleo_e_comandos(monkeypatch):
    import vision.cli
    import vision.nucleo

    chamado = []
    monkeypatch.setattr(vision.nucleo, "main", lambda argv: chamado.append(("nucleo", argv)) or 0)
    monkeypatch.setattr(vision.cli, "main", lambda argv: chamado.append(("cli", argv)) or 0)
    assert empacotado.principal([]) == 0  # duplo clique: núcleo e janela
    assert empacotado.principal(["--nucleo"]) == 0  # a pasta Inicializar: núcleo, sem janela
    assert empacotado.principal(["--nucleo", "--sem-voz"]) == 0
    assert empacotado.principal(["interface", "--nucleo"]) == 0  # a janela
    assert empacotado.principal(["google-login"]) == 0
    assert chamado == [("nucleo", ["--abrir"]), ("nucleo", []), ("nucleo", ["--sem-voz"]),
                       ("cli", ["interface", "--nucleo"]), ("cli", ["google-login"])]


def test_sem_console_o_stdout_nao_derruba(monkeypatch):
    import vision.cli

    monkeypatch.setattr(vision.cli, "main", lambda argv: print("escrevendo sem console") or 0)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert empacotado.principal(["chat"]) == 0


def test_comando_do_pyinstaller_leva_o_que_precisa_e_deixa_o_torch_de_fora(tmp_path):
    c = empacotar.comando_pyinstaller(Path("python.exe"))
    for pacote in ("webview", "piper", "onnxruntime", "sounddevice", "openwakeword"):
        assert pacote in c[c.index("--collect-all") :] and c[c.index(pacote) - 1] == "--collect-all"
    excluidos = {c[i + 1] for i, a in enumerate(c) if a == "--exclude-module"}
    assert {"torch", "TTS", "transformers"} <= excluidos and "sklearn" not in excluidos  # o openwakeword usa o sklearn
    assert "--windowed" in c and c[-1].endswith("vision_exe.py")


def _projeto_falso(raiz: Path) -> Path:
    (raiz / "ui" / "dist" / "assets").mkdir(parents=True)
    (raiz / "ui" / "dist" / "index.html").write_text("<html>", encoding="utf-8")
    (raiz / "mcp_servers" / "orbit").mkdir(parents=True)
    (raiz / "mcp_servers" / "orbit" / "server.py").write_text("x = 1\n", encoding="utf-8")
    (raiz / "mcp_servers" / "orbit" / "segredo.json").write_text('{"token": "x"}', encoding="utf-8")
    (raiz / "config.yaml").write_text("voz:\n  stt: parakeet-base-int8\n", encoding="utf-8")
    for m in ("parakeet-base-int8", "piper", "openwakeword", "xtts_v2", "parakeet-ptbr"):
        (raiz / "modelos" / m).mkdir(parents=True)
        (raiz / "modelos" / m / "arquivo.bin").write_bytes(b"0")
    (raiz / "node" / "node_modules").mkdir(parents=True)
    (raiz / "data").mkdir()
    (raiz / "data" / "google-oauth.json").write_text("{}", encoding="utf-8")
    (raiz / ".env").write_text("TAVILY_API_KEY=tvly-x\n", encoding="utf-8")
    return raiz


def test_pasta_do_pacote_nunca_leva_dados_nem_segredos_nem_xtts(tmp_path):
    raiz, saida = _projeto_falso(tmp_path / "projeto"), tmp_path / "dist" / "Vision"
    saida.mkdir(parents=True)
    avisos = empacotar.montar_pasta("copiar", saida, raiz)
    assert not avisos
    assert (saida / "ui" / "dist" / "index.html").exists() and (saida / "config.yaml").exists()
    assert (saida / "mcp_servers" / "orbit" / "server.py").exists()
    assert not (saida / "mcp_servers" / "orbit" / "segredo.json").exists()  # nenhum .json de dentro do projeto
    assert sorted(p.name for p in (saida / "modelos").iterdir()) == ["openwakeword", "parakeet-base-int8", "piper"]
    assert list((saida / "data").iterdir()) == [] and not (saida / ".env").exists()
    assert (saida / "node" / "node_modules").exists()


def test_sem_modelos_o_pacote_so_avisa_o_que_falta(tmp_path):
    raiz, saida = _projeto_falso(tmp_path / "projeto"), tmp_path / "dist" / "Vision"
    saida.mkdir(parents=True)
    avisos = empacotar.montar_pasta("nenhum", saida, raiz)
    assert any("modelos/piper" in a for a in avisos) and any("node" in a for a in avisos)
    assert not list((saida / "modelos").iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="junction do Windows")
def test_ligar_cria_atalhos_de_pasta_sem_copiar(tmp_path):
    raiz, saida = _projeto_falso(tmp_path / "projeto"), tmp_path / "dist" / "Vision"
    saida.mkdir(parents=True)
    assert not empacotar.montar_pasta("ligar", saida, raiz)
    assert (saida / "modelos" / "piper" / "arquivo.bin").read_bytes() == b"0"
    assert (saida / "modelos" / "piper").is_junction()


@pytest.mark.skipif(sys.platform != "win32", reason="junction do Windows")
def test_refazer_o_pacote_nao_apaga_o_que_esta_atras_dos_atalhos(tmp_path):
    raiz, saida = _projeto_falso(tmp_path / "projeto"), tmp_path / "dist" / "Vision"
    saida.mkdir(parents=True)
    empacotar.montar_pasta("ligar", saida, raiz)
    empacotar.desfazer_atalhos(saida)
    assert not (saida / "modelos" / "piper").exists() and not (saida / "node").exists()
    assert (raiz / "modelos" / "piper" / "arquivo.bin").exists() and (raiz / "node" / "node_modules").exists()
