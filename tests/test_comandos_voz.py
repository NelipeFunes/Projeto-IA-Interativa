"""As frases que abrem e fecham a conversa, no texto que o Parakeet devolve (sem precisar de modelo)."""

import pytest

from jarvis.voice.comandos import achar_ativacao, e_despedida


@pytest.mark.parametrize("ouvido,resto", [
    # o que o Parakeet devolveu na calibração de 30/09 (vozes sintéticas)
    ("Hey Vision", ""),
    ("E vision.", ""),
    ("Eye vision", ""),
    ("E Visium, o que eu tenho hoje?", "o que eu tenho hoje?"),
    ("Hey Vision, O Kway U Ten Ho Hoach?", "O Kway U Ten Ho Hoach?"),
    ("Vision, marca barbeiro amanhã", "marca barbeiro amanhã"),
    ("Ei, visão, tudo bem?", "tudo bem?"),  # "visão" só vale depois de um chamado
    ("Rei Vision.", ""),
    ("ok vizion me lembra", "me lembra"),
])
def test_chamados_que_acordam(ouvido, resto):
    assert achar_ativacao(ouvido) == resto


@pytest.mark.parametrize("ouvido", [
    "Qual é a minha agenda de hoje?",
    "Visita amanhã às três.",
    "Visão computacional é legal",  # "visão" sem chamado antes
    "Eu assisti Vision ontem",  # o nome no meio da frase
    "O filme tem uma visual incrível",
    "",
    "Fiz um marca babiro amanhã",
    # falsos positivos achados na revisão do PR 3
    "É visão de futuro isso aí",
    "E visão, né, cara",
    "Ok, visão geral do projeto",
    "Aí, visão turva hoje",
    "Visionário demais esse cara",
    "Revision do código",
])
def test_falas_que_nao_acordam(ouvido):
    assert achar_ativacao(ouvido) is None


@pytest.mark.parametrize("ouvido", [
    "Beleza, Visium pode desligar.",
    "Beleza, Vision, pode desligar.",
    "Valeu, Visium, pode desligar.",
    "Beleza Vision Poe Desliger.",
    "Pode dormir.",
    "pode desligar agora, valeu",
    "Tchau, Vision.",
    "pode encerrar por favor",
    "Beleza, Vision pode dizer I.",  # "desligar" mal ouvido (30/09)
])
def test_despedidas(ouvido):
    assert e_despedida(ouvido)


@pytest.mark.parametrize("ouvido", [
    "Pode desligar o alarme de amanhã",  # é um pedido
    "Desliga a luz da sala",
    "Marca o dentista amanhã às nove",
    "Beleza, Vision, pode marcar o dentista",
    "Beleza Vision pode me falar",
    "Beleza, pode ser amanhã",  # sem o nome: é conversa normal
    # falsos positivos achados na revisão do PR 3
    "Me lembra de desligar",
    "Que horas eu preciso dormir?",
    "A que horas devo encerrar",
    "Não desliga",
    "Não, não desliga agora",
    "Tchau Vision, marca dentista amanhã",
    "Beleza Vision pode continuar",
    "Certo, Vision, pode repetir",
    "Ok Vision, pode excluir",
    "Tchau pra você também, a reunião acabou cedo",  # tchau sem o nome, frase longa
    "Me explica como a gente vai desligar o servidor antigo sem perder os dados de ninguém",
    "",
])
def test_nao_despedidas(ouvido):
    assert not e_despedida(ouvido)
