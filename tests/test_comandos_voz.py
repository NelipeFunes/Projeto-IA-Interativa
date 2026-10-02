"""As frases que abrem e fecham a conversa, no texto que o Parakeet devolve (sem precisar de modelo)."""

import pytest

from vision.voice.comandos import achar_ativacao, e_despedida


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
    "Vision, standby.",
    "Vision standby",
    "Stand by.",
    "Beleza, Vision Standry.",  # voz sintética, mal ouvido
    "Vision, a standby",
    "Pode ficar em stand-by.",
    # o que ele disse de verdade em 01/10
    "Tudo certinho. Agora pode ficar em standby e que se eu precisar, eu te chamo de novo.",
    "Não, não precisa de mais nada, pode ficar em standby.",
    "Não, Vision, standby.",  # revisão do PR 16: o "não" responde a outra coisa
    "Não, obrigado. Standby.",
    "Não precisa, Vision, standby",
    # 2ª revisão do PR 16: jeitos naturais de mandar parar
    "Vision. Stand. By.",
    "Stand, by.",
    "Vision por favor standby",
    "Esta tudo certo pode ficar em standby",
    "Pode deixar em standby",
    "Coloca em standby, Vision",
    "Pode dormir.",
    "Tchau, Vision.",
    "pode encerrar por favor",
    # 02/10: "modo" não barra mais, e o Parakeet com sotaque
    "Não, não, não, pode reparar, pode entrar em modo standby.",
    "Pode entrar em modo standby",
    "Stand you by.",
    "Entra em standby",
])
def test_despedidas(ouvido):
    assert e_despedida(ouvido)


@pytest.mark.parametrize("ouvido", [
    "Pode desligar o alarme de amanhã",  # é um pedido
    "Desliga a luz da sala",
    # 01/10: "desligar" agora é só pedido (luz); quem fecha a conversa é "standby"
    "Pode desligar.",
    "Não, não precisa de mais nada, não. Pode desligar.",
    "Beleza, Vision, pode desligar.",
    "Mas não entrou em stand-by perfeito, né?",  # pergunta
    "Não entra em standby agora",
    "Coloca o PC em standby",
    "Põe o computador em standby",
    "Deixa a TV em standby",
    "A TV ficou em standby a noite toda",
    "Me explica o que é o modo standby da TV",
    "Deixa em standby o PC",  # aparelho depois do standby
    # revisão do PR 42: pergunta sobre o modo standby, a música, e "stand X by" que não é o Parakeet
    "Me explica o modo standby",
    "Quanto gasta o modo standby",
    "Como ativo o modo standby no Windows",
    "Toca Stand by Me do Ben E. King",
    "Stand up by 5pm",
    "stand 3 by 4",
    "Me explica o que é o modo standby da TV e quanto ele gasta de energia por mês aqui em casa, por favor, rapidinho",
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
    # 2ª revisão do PR 3: verbo fora da lista não é "desligar" mal ouvido
    "Beleza Vision pode agendar",
    "Beleza Vision pode adicionar",
    "Beleza Vision pode remarcar",
    "Beleza Vision pode checar",
    "Valeu Vision pode olhar",
    "Certo, Vision, pode ser",
    "Tchau pra você também, a reunião acabou cedo",  # tchau sem o nome, frase longa
    "Me explica como a gente vai desligar o servidor antigo sem perder os dados de ninguém",
    "",
])
def test_nao_despedidas(ouvido):
    assert not e_despedida(ouvido)


@pytest.mark.parametrize("ouvido,motivo", [
    ("Para de falar.", "parar"),
    ("Vision, para.", "parar"),
    ("Chega, já entendi.", "parar"),
    ("Pera aí.", "parar"),
    ("Cala a boca.", "parar"),
    ("Para, para, para.", "parar"),
    ("Vision, standby.", "standby"),
    ("Pode ficar em standby.", "standby"),
    # a própria voz vazando no microfone, ou conversa: não corta
    ("Vou marcar para amanhã às dez.", None),
    ("Para quando é a prova?", None),
    ("Hoje você tem aula para fazer", None),
    ("Espera que eu vou ver a agenda de amanhã e já te falo tudo", None),
    # revisão do PR 17: frases que ele mesmo diz não cortam a própria fala
    ("Espere um pouco.", None),
    ("Pode descansar.", None),
    ("Encerrar.", None),
    ("", None),
])
def test_interrupcao_por_voz(ouvido, motivo):
    from vision.voice.comandos import e_interrupcao

    assert e_interrupcao(ouvido) == motivo



# ------------------------------------------------------------------ calibração do "Hey Vision" (02/10)


@pytest.fixture
def sem_apelidos():
    from vision.voice import comandos

    comandos.definir_apelidos([])
    yield comandos
    comandos.definir_apelidos([])


def test_aprende_a_grafia_que_se_repete_e_nao_acordou(sem_apelidos):
    c = sem_apelidos
    ouvidos = ["Deliving", "Hey Vision", "Deliving.", "Hey, deliving!", "Divisão"]
    assert c.aprender_apelidos(ouvidos) == ["deliving"]  # 3 vezes; "Divisão" saiu 1 vez só (e é palavra comum)
    assert c.achar_ativacao("Deliving, que horas são?") is None
    c.definir_apelidos(["deliving"])
    assert c.achar_ativacao("Deliving, que horas são?") == "que horas são?"
    assert c.achar_ativacao("Hey deliving") == ""


@pytest.mark.parametrize("ouvidos", [
    ["Yeah.", "Yeah", "yeah!"],  # palavra comum, mesmo repetida
    ["Do not", "Do not", "Do not"],  # curta demais
    ["Visita", "Visita", "Visita"],  # palavra do dia a dia: a visita acordaria o Vision
    ["Deliving", "Bijon", "Vixon"],  # cada vez uma: nada seguro para aprender
    ["", "", ""],
])
def test_nao_aprende_o_que_acordaria_a_toa(sem_apelidos, ouvidos):
    assert sem_apelidos.aprender_apelidos(ouvidos) == []


def test_apelidos_gravados_voltam_e_os_invalidos_ficam_de_fora(sem_apelidos, tmp_path):
    c = sem_apelidos
    c.gravar_apelidos(tmp_path, ["deliving", "bijon"])
    assert c.ler_apelidos(tmp_path) == ["bijon", "deliving"]
    (tmp_path / c.ARQUIVO_APELIDOS).write_text('{"apelidos": ["ok", "visita", "../x", 3, "deliving"]}', encoding="utf-8")
    assert c.ler_apelidos(tmp_path) == ["deliving"]
    (tmp_path / c.ARQUIVO_APELIDOS).write_text("{{{", encoding="utf-8")
    assert c.ler_apelidos(tmp_path) == []

def test_stand_you_by_tambem_interrompe():
    from vision.voice.comandos import e_interrupcao

    assert e_interrupcao("Stand you by.") == "standby"
    assert e_interrupcao("Toca Stand by Me") is None



# ------------------------------------------------------------------ revisão do PR 43


@pytest.mark.parametrize("ouvidos", [
    ["E o Brasil venceu ontem", "E o Brasil perdeu", "Hey Vision", "x", "y"],  # frase longa da TV
    ["E o Brasil", "E o Brasil", "E o Brasil", "a", "b"],  # palavra comum, mesmo curta e repetida
    ["Como está o tempo hoje, gente", "Como fazer isso", "Como", "", ""],
    ["Deliving", "Deliving", "Hey Vision", "Hey Vision", ""],  # 2 de 5: não é maioria
])
def test_tv_e_minoria_nao_ensinam(sem_apelidos, ouvidos):
    assert sem_apelidos.aprender_apelidos(ouvidos) == []


def test_no_maximo_cinco_grafias_minusculas(sem_apelidos, tmp_path):
    c = sem_apelidos
    c.definir_apelidos(["Deliving", "bijon", "vixon", "vijom", "bision", "vezion", "vishion"])
    assert len(c.APELIDOS) == 5 and "deliving" in c.APELIDOS
    c.gravar_apelidos(tmp_path, ["Deliving", "bijon", "vixon", "vijom", "bision", "vezion"])
    assert len(c.ler_apelidos(tmp_path)) == 5
    (tmp_path / c.ARQUIVO_APELIDOS).write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    assert c.ler_apelidos(tmp_path) == []  # grande demais (ou aninhado demais): não derruba a voz
