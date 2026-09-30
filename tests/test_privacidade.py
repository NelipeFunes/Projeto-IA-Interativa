"""Nenhum arquivo versionado pode conter termos privados do dono (o repositório é público).

Os termos ficam em data/termos-privados.txt, que é ignorado pelo git: assim a proteção funciona sem que
os próprios termos entrem no código (erro que a revisão de 30/09 pegou num teste antigo).
Sem o arquivo (outra máquina, CI), o teste é pulado.
"""

import subprocess

import pytest

from jarvis.config import RAIZ

TERMOS = RAIZ / "data" / "termos-privados.txt"


def _termos() -> list[str]:
    linhas = TERMOS.read_text(encoding="utf-8").splitlines()
    return [t.strip().lower() for t in linhas if t.strip() and not t.startswith("#")]


@pytest.mark.skipif(not TERMOS.exists(), reason="sem data/termos-privados.txt nesta máquina")
def test_nenhum_arquivo_versionado_tem_termo_privado():
    arquivos = subprocess.run(
        ["git", "-C", str(RAIZ), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    termos = _termos()
    achados = []
    for rel in arquivos:
        caminho = RAIZ / rel
        if not caminho.is_file() or caminho.stat().st_size > 2_000_000:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue  # binário
        achados += [f"{rel} contém o termo nº {i + 1}" for i, t in enumerate(termos) if t in texto]
    # A mensagem cita só o número do termo, nunca o termo.
    assert not achados, "termos privados em arquivos versionados:\n" + "\n".join(achados)
