"""Nenhum termo privado do dono pode ir para o GitHub (o repositório é público).

Os termos ficam em data/termos-privados.txt, que é ignorado pelo git: assim a proteção funciona sem que
os próprios termos entrem no código (erro que a revisão de 30/09 pegou num teste antigo).
Duas camadas:
  - este teste varre os arquivos versionados de agora (sem o arquivo de termos, é pulado);
  - o hook .githooks/pre-push varre o que vai subir, commit a commit (conteúdo, mensagens e nomes),
    que é por onde o vazamento da 1ª rodada teria passado. Os testes do hook usam um termo falso.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.config import RAIZ

TERMOS = RAIZ / "data" / "termos-privados.txt"
HOOK = RAIZ / ".githooks" / "pre-push"
ZERO = "0" * 40


def _achados_nos_arquivos_versionados() -> list[str]:
    """Os termos só existem aqui dentro: com `pytest -l`, uma falha não os imprime como locais do teste."""
    linhas = TERMOS.read_text(encoding="utf-8").splitlines()
    termos = [t.strip().lower() for t in linhas if t.strip() and not t.strip().startswith("#")]
    arquivos = subprocess.run(
        ["git", "-C", str(RAIZ), "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    achados = []
    for rel in arquivos:
        caminho = RAIZ / rel
        if not caminho.is_file() or caminho.stat().st_size > 2_000_000:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue  # binário
        achados += [f"{rel} contém o termo nº {i + 1}" for i, t in enumerate(termos) if t in texto or t in rel.lower()]
    return achados


@pytest.mark.skipif(not TERMOS.exists(), reason="sem data/termos-privados.txt nesta máquina")
def test_nenhum_arquivo_versionado_tem_termo_privado():
    achados = _achados_nos_arquivos_versionados()
    # A mensagem cita só o número do termo, nunca o termo.
    assert not achados, "termos privados em arquivos versionados:\n" + "\n".join(achados)


# ------------------------------------------------------------------ hook pre-push

def _sh() -> str | None:
    sh = shutil.which("sh")
    git = shutil.which("git")
    if not sh and git:  # Git for Windows: ...\Git\cmd\git.exe → ...\Git\bin\sh.exe
        candidato = Path(git).parents[1] / "bin" / "sh.exe"
        sh = str(candidato) if candidato.exists() else None
    return sh


SH = _sh()
TERMO_FALSO = "Termo-Falso-7f3a"
TERMO_ACENTUADO = "são-falsópolis-7f3a"
precisa_de_sh = pytest.mark.skipif(SH is None, reason="sem sh (Git for Windows) nesta máquina")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=teste", "-c", "user.email=teste@example.com", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _commit(repo: Path, arquivo: str, conteudo: str, mensagem: str = "commit") -> str:
    (repo / arquivo).write_text(conteudo, encoding="utf-8")
    _git(repo, "add", arquivo)
    _git(repo, "commit", "-q", "-m", mensagem)
    return _git(repo, "rev-parse", "HEAD")


def _push(repo: Path, local: str, remoto: str = ZERO, nome_remoto: str = "origin") -> subprocess.CompletedProcess:
    """Roda o hook como o git roda: uma linha por ref no stdin (ref local, sha local, ref remota, sha remoto)."""
    r = subprocess.run(  # em bytes: no modo texto o Windows trocaria o \n por \r\n, e o git manda \n
        [SH, str(HOOK), nome_remoto, "https://example.com/repo.git"], cwd=repo, input=f"refs/heads/x {local} refs/heads/x {remoto}\n".encode(),
        capture_output=True,
    )
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode(), r.stderr.decode())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "termos-privados.txt").write_text(
        f"# comentário\n\n{TERMO_FALSO.lower()}\n{TERMO_ACENTUADO}\n", encoding="utf-8"
    )
    return tmp_path


@precisa_de_sh
def test_hook_barra_termo_no_conteudo_sem_citar_o_termo(repo):
    r = _push(repo, _commit(repo, "a.txt", f"oi {TERMO_FALSO.upper()}\n"))
    assert r.returncode == 1
    assert "nº 1" in r.stderr and TERMO_FALSO.lower() not in r.stderr.lower()


@precisa_de_sh
def test_hook_barra_termo_na_mensagem_e_no_nome_do_arquivo(repo):
    assert _push(repo, _commit(repo, "a.txt", "limpo\n", f"fala do {TERMO_FALSO}")).returncode == 1
    assert _push(repo, _commit(repo, f"{TERMO_FALSO}.txt", "limpo\n")).returncode == 1


@precisa_de_sh
def test_hook_barra_termo_que_entrou_e_saiu_no_mesmo_push(repo):
    _commit(repo, "a.txt", f"{TERMO_FALSO}\n")
    sha = _commit(repo, "a.txt", "limpo\n")  # o arquivo final está limpo, mas o commit anterior sobe junto
    assert _push(repo, sha).returncode == 1


@precisa_de_sh
def test_hook_deixa_passar_o_limpo_e_a_remocao_de_termo_ja_publicado(repo):
    publicado = _commit(repo, "a.txt", f"{TERMO_FALSO}\n")
    assert _push(repo, _commit(repo, "a.txt", "limpo\n"), remoto=publicado).returncode == 0
    ultimo = _commit(repo, "b.txt", "nada demais\n")
    assert _push(repo, ultimo, remoto=_git(repo, "rev-parse", "HEAD~1")).returncode == 0


@precisa_de_sh
def test_hook_barra_quando_nao_consegue_ler_os_commits(repo):
    _commit(repo, "a.txt", "limpo\n")
    assert _push(repo, "1" * 40).returncode == 1  # commit que não existe: o git log falha


@precisa_de_sh
def test_hook_sem_arquivo_de_termos_nao_barra(repo):
    (repo / "data" / "termos-privados.txt").unlink()
    assert _push(repo, _commit(repo, "a.txt", f"{TERMO_FALSO}\n")).returncode == 0


@precisa_de_sh
def test_hook_ve_o_que_foi_digitado_ao_resolver_conflito_de_merge(repo):
    publicado = _commit(repo, "f.txt", "base\n")
    _git(repo, "checkout", "-q", "-b", "outro")
    _commit(repo, "f.txt", "do outro\n")
    _git(repo, "checkout", "-q", "-")
    _commit(repo, "f.txt", "da principal\n")
    with pytest.raises(subprocess.CalledProcessError):
        _git(repo, "merge", "-q", "outro")  # dá conflito
    assert (repo / ".git" / "MERGE_HEAD").exists()
    (repo / "f.txt").write_text(f"resolvido com {TERMO_FALSO}\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "--no-edit")
    assert _push(repo, _git(repo, "rev-parse", "HEAD"), remoto=publicado).returncode == 1


@precisa_de_sh
def test_hook_le_a_mensagem_de_tag_anotada(repo):
    _commit(repo, "a.txt", "limpo\n")
    _git(repo, "tag", "-a", "v0.1", "-m", f"versão do {TERMO_FALSO}")
    assert _push(repo, _git(repo, "rev-parse", "v0.1")).returncode == 1


@precisa_de_sh
def test_hook_nao_confia_em_outro_remoto(repo):
    sha = _commit(repo, "a.txt", f"{TERMO_FALSO}\n")
    _git(repo, "update-ref", "refs/remotes/backup/main", sha)  # já está num remoto privado, não no origin
    assert _push(repo, sha, nome_remoto="origin").returncode == 1


@precisa_de_sh
def test_hook_le_nome_de_arquivo_acentuado(repo):
    assert _push(repo, _commit(repo, f"doc-{TERMO_ACENTUADO}.txt", "limpo\n")).returncode == 1
