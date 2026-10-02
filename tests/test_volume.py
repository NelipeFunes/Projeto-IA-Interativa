"""Volume: Spotify primeiro, Windows quando faz sentido (pedido de 02/10). A decisão é do código, não do modelo."""

from __future__ import annotations

import pytest

from vision.tools.pc import PC, comando_de_pc
from vision.volume import Plano, planejar

TOCANDO = {"volume": 60, "tocando": True}


@pytest.mark.parametrize("acao,nivel,spotify,windows,mudo,esperado", [
    # Spotify tocando: ele primeiro.
    ("aumentar", None, TOCANDO, 50, False, Plano(spotify=70)),
    ("diminuir", 25, TOCANDO, 50, False, Plano(spotify=35)),
    ("definir", 30, TOCANDO, 50, False, Plano(spotify=30)),
    # No máximo: o Windows sobe; o que passa de 100 vai para o Windows; tudo no máximo: nada a fazer.
    ("aumentar", None, {"volume": 100, "tocando": True}, 40, False,
     Plano(windows=("aumentar", 10), motivo="spotify_no_maximo")),
    ("aumentar", 20, {"volume": 95, "tocando": True}, 40, False, Plano(spotify=100, windows=("aumentar", 15))),
    ("aumentar", None, {"volume": 100, "tocando": True}, 100, False, Plano(motivo="tudo_no_maximo")),
    # Windows mudo e Spotify no máximo: "aumenta" tira o mudo.
    ("aumentar", None, {"volume": 100, "tocando": True}, 100, True,
     Plano(windows=("aumentar", 10), tirar_mudo=True, motivo="spotify_no_maximo")),
    # Spotify no zero: abaixar mexe no Windows.
    ("diminuir", None, {"volume": 0, "tocando": True}, 40, False,
     Plano(windows=("diminuir", 10), motivo="spotify_no_zero")),
    # Spotify parado ou ausente: o Windows, como sempre foi.
    ("aumentar", None, {"volume": 60, "tocando": False}, 50, False, Plano(windows=("aumentar", None))),
    ("aumentar", None, None, 50, False, Plano(windows=("aumentar", None))),
    # Mudo é sempre o Windows.
    ("mudo", None, TOCANDO, 50, False, Plano(windows=("mudo", None))),
])
def test_planejar(acao, nivel, spotify, windows, mudo, esperado):
    assert planejar(acao, nivel, onde="auto", spotify=spotify, windows=windows, windows_mudo=mudo) == esperado


def test_onde_pc_e_onde_spotify():
    assert planejar("aumentar", None, onde="pc", spotify=TOCANDO, windows=10, windows_mudo=False) == \
        Plano(windows=("aumentar", None))
    # "volume do Spotify" com ele pausado: ainda é o Spotify (não o Windows).
    assert planejar("definir", 20, onde="spotify", spotify={"volume": 60, "tocando": False}, windows=10,
                    windows_mudo=False) == Plano(spotify=20)


class SpotifyFalso:
    def __init__(self, estado):
        self.estado, self.mudou = estado, []

    async def volume_atual(self):
        return self.estado

    async def mudar_volume(self, nivel, aparelho=None):
        self.mudou.append((nivel, aparelho))


def _pc(tmp_path, spotify=None, windows=50, mudo=False):
    feitos = []

    def mudar(acao, nivel):
        feitos.append((acao, nivel))
        return {"aumentar": windows + int(nivel or 10), "diminuir": windows - int(nivel or 10)}.get(acao, windows)

    pc = PC(tmp_path, ler_volume=lambda: (windows, mudo), mudar_volume=mudar)
    pc.spotify = spotify
    return pc, feitos


async def test_ferramenta_mexe_no_spotify_e_conta_o_que_fez(tmp_path):
    sp = SpotifyFalso({"volume": 60, "tocando": True, "id": "pc1"})
    pc, feitos = _pc(tmp_path, sp)
    assert await pc.mudar_volume({"acao": "diminuir"}) == "Spotify em 50%."
    assert sp.mudou == [(50, "pc1")] and feitos == []


async def test_spotify_no_maximo_sobe_o_windows(tmp_path):
    sp = SpotifyFalso({"volume": 100, "tocando": True, "id": "pc1"})
    pc, feitos = _pc(tmp_path, sp, windows=40, mudo=True)
    assert await pc.mudar_volume({"acao": "aumentar"}) == "O Spotify já estava no máximo: subi o Windows para 50%."
    assert sp.mudou == [] and feitos == [("som", None), ("aumentar", 10)]


async def test_sem_spotify_tocando_e_o_windows(tmp_path):
    pc, feitos = _pc(tmp_path, SpotifyFalso(None))
    assert await pc.mudar_volume({"acao": "aumentar"}) == "Volume em 60%."
    pc, _ = _pc(tmp_path, None)  # Spotify nem ligado
    assert await pc.mudar_volume({"acao": "definir", "nivel": 30}) == "Volume em 50%."


async def test_volume_do_spotify_sem_spotify_avisa(tmp_path):
    from vision.tools.base import ErroFerramenta

    pc, feitos = _pc(tmp_path, SpotifyFalso(None))
    with pytest.raises(ErroFerramenta, match="não está tocando"):
        await pc.mudar_volume({"acao": "aumentar", "onde": "spotify"})
    assert feitos == []  # não mexeu no Windows no lugar


async def test_spotify_fora_do_ar_nao_quebra_o_volume(tmp_path):
    class Quebrado:
        async def volume_atual(self):
            raise RuntimeError("sem internet")

    pc, feitos = _pc(tmp_path, Quebrado())
    assert await pc.mudar_volume({"acao": "diminuir"}) == "Volume em 40%."


@pytest.mark.parametrize("frase,esperado", [
    ("aumenta o volume", ("volume", {"acao": "aumentar"})),
    ("abaixa a música", ("volume", {"acao": "diminuir"})),
    ("aumenta o som da música", ("volume", {"acao": "aumentar"})),
    ("volume do Spotify em 30", ("volume", {"acao": "definir", "nivel": 30, "onde": "spotify"})),
    ("abaixa o volume do PC", ("volume", {"acao": "diminuir", "onde": "pc"})),
    ("aumenta o volume do computador", ("volume", {"acao": "aumentar", "onde": "pc"})),
    ("volume 40", ("volume", {"acao": "definir", "nivel": 40})),
    ("não abaixa a música", None),
])
def test_atalhos_de_volume(frase, esperado):
    assert comando_de_pc(frase) == esperado
