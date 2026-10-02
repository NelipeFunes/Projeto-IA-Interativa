"""'O que você sabe fazer?' lista só o que está ligado."""

import pytest

from vision.brain import prompt
from vision.tools.ajuda import Ajuda, pede_ajuda
from vision.tools.base import Ferramenta, Registro, esquema


async def _nada(_args):
    return ""


async def test_lista_so_os_grupos_ligados():
    r = Registro()
    assert await Ajuda(r).capacidades() == "Por enquanto, só conversar."
    r.adicionar(Ferramenta("agenda_listar", "", esquema([]), _nada, grupo="agenda"),
                Ferramenta("timer_criar", "", esquema([]), _nada, grupo="timer"),
                Ferramenta("web_buscar", "", esquema([]), _nada, grupo="web"))
    assert await Ajuda(r).capacidades() == ("Posso ver e mudar a sua agenda, pôr timers e alarmes e pesquisar na "
                                            "internet. É só pedir.")


@pytest.mark.parametrize("frase,sim", [("O que você sabe fazer?", True), ("vision, quais são suas funções", True),
                                       ("me ajuda", True), ("me ajuda a escolher um presente", False),
                                       ("o que você faz amanhã?", False)])
def test_frases(frase, sim):
    assert pede_ajuda(frase) is sim


def test_persona_jarvis_no_prompt():
    from datetime import datetime

    from vision import tempo

    texto = prompt.montar("Felipe", "voz", datetime.now(tempo.FUSO), "", "")
    assert "JARVIS" in texto and "próximo passo" in texto
