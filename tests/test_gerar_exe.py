"""O lançador Vision.exe: o script de geração e o código-fonte dele."""

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))

import gerar_exe  # noqa: E402


def test_comando_do_compilador_tem_icone_saida_e_fonte(tmp_path):
    csc = gerar_exe.Path("csc.exe")
    saida = tmp_path / "Vision.exe"
    c = gerar_exe.comando(csc, saida)
    assert "/target:winexe" in c and f"/out:{saida}" in c and str(gerar_exe.FONTE) in c
    assert any(a.startswith("/win32icon:") and a.endswith("vision.ico") for a in c)


def test_fonte_liga_o_visionw_do_projeto_sem_console():
    fonte = gerar_exe.FONTE.read_text(encoding="utf-8")
    assert r".venv\Scripts\visionw.exe" in fonte and "CreateNoWindow = true" in fonte
    assert '"--abrir"' in fonte  # sem argumentos, abre a janela


def test_gera_um_executavel_de_verdade(tmp_path):
    if gerar_exe.achar_csc() is None:
        pytest.skip("sem csc.exe (só Windows)")
    exe = gerar_exe.gerar(tmp_path / "Vision.exe")
    dados = exe.read_bytes()
    assert dados[:2] == b"MZ" and 50_000 < len(dados) < 1_000_000


def test_binarios_gerados_ficam_fora_do_git():
    import subprocess

    raiz = gerar_exe.RAIZ
    r = subprocess.run(["git", "-C", str(raiz), "check-ignore", "Vision.exe", "dist/Vision/Vision.exe", "build/x"],
                       capture_output=True, text=True)
    assert set(r.stdout.split()) == {"Vision.exe", "dist/Vision/Vision.exe", "build/x"}


def test_lancador_escapa_argumentos_e_exige_o_marcador_do_projeto():
    fonte = gerar_exe.FONTE.read_text(encoding="utf-8")
    assert "Escapar" in fonte and "string.Join(\" \", args)" not in fonte
    assert 'pyproject.toml' in fonte and "NativeErrorCode" in fonte
