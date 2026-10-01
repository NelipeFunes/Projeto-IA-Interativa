"""Luzes pela Alexa: só nomes da lista, confirmação antes, nada de texto livre para a Alexa."""

import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from vision import alexa
from vision.brain.agent import Agente
from vision.tools.base import ErroFerramenta, Registro
from vision.tools.casa import Casa

LUZES = [{"nome": "Luz do Quarto", "entity_id": "e1"}, {"nome": "Abajur Sala", "entity_id": "e2"}]


class AlexaFalsa:
    def __init__(self, luzes=LUZES, falha=None):
        self._luzes, self.falha, self.feitas = luzes, falha, []

    def luzes(self):
        return self._luzes

    async def mudar_luz(self, entity_id, ligar, brilho=None):
        if self.falha:
            raise self.falha
        self.feitas.append((entity_id, ligar, brilho))


@pytest.mark.parametrize("pedido,esperado", [
    ("Luz do Quarto", ["e1"]), ("quarto", ["e1"]), ("luz do quarto", ["e1"]), ("abajur", ["e2"]),
    ("todas", ["e1", "e2"]), ("abajur da sala", ["e2"]),
])
def test_escolhe_a_luz_pelo_nome(pedido, esperado):
    assert [x["entity_id"] for x in Casa(AlexaFalsa())._escolher(pedido)] == esperado


@pytest.mark.parametrize("pedido", ["cozinha", "compra papel higiênico", ""])
def test_luz_que_nao_existe_e_recusada(pedido):
    with pytest.raises(ErroFerramenta, match="Luz do Quarto"):  # a mensagem lista as que existem
        Casa(AlexaFalsa())._escolher(pedido)


async def test_acender_com_brilho_e_apagar():
    a = AlexaFalsa()
    c = Casa(a)
    ferr = {f.nome: f for f in c.ferramentas()}
    assert ferr["luz_acender"].escrita and ferr["luz_apagar"].escrita and not ferr["luzes_listar"].escrita
    assert await ferr["luz_acender"].descrever({"luz": "quarto", "brilho": 40}) == "Vou acender Luz do Quarto em 40%."
    assert await ferr["luz_acender"].executar({"luz": "quarto", "brilho": "40%"}) == "Acendi Luz do Quarto em 40%."
    assert await ferr["luz_apagar"].executar({"luz": "todas"}) == "Apaguei Luz do Quarto, Abajur Sala."
    assert a.feitas == [("e1", True, 40), ("e1", False, None), ("e2", False, None)]
    with pytest.raises(ErroFerramenta, match="1 a 100"):
        await ferr["luz_acender"].executar({"luz": "quarto", "brilho": 300})


async def test_sem_login_ou_alexa_fora_do_ar_vira_mensagem():
    c = Casa(AlexaFalsa(luzes=[]))
    with pytest.raises(ErroFerramenta, match="alexa-login"):
        await c._mudar(True)({"luz": "quarto"})
    c = Casa(AlexaFalsa(falha=alexa.SemLogin("a sessão da Alexa venceu: rode `vision alexa-login`")))
    with pytest.raises(ErroFerramenta, match="alexa-login"):
        await c._mudar(False)({"luz": "quarto"})
    c = Casa(AlexaFalsa(falha=RuntimeError("timeout")))
    with pytest.raises(ErroFerramenta, match="não respondeu"):
        await c._mudar(False)({"luz": "quarto"})


async def test_acender_pelo_agente_pede_confirmacao_antes():
    a = AlexaFalsa()
    r = Registro()
    r.adicionar(*Casa(a).ferramentas())
    agente = Agente(LLMFalso([chama("luz_acender", luz="quarto"), fala("Certo.")]), r, None)
    resp = await agente.responder("acende a luz do quarto", "texto", "t")
    assert resp.aguardando_confirmacao and a.feitas == []  # nada aconteceu ainda
    await agente.responder("sim", "texto", "t")
    assert a.feitas == [("e1", True, None)]


def test_so_luzes_entram_na_lista():
    aparelhos = [
        {"applianceTypes": ["LIGHT"], "friendlyName": "Luz do Quarto", "entityId": "e1"},
        {"applianceTypes": ["SMARTPLUG"], "friendlyName": "Tomada", "entityId": "e3"},
        {"applianceTypes": ["LIGHT"], "friendlyName": "Sem id"},
    ]
    assert alexa._luzes_de(aparelhos) == [{"nome": "Luz do Quarto", "entity_id": "e1"}]
    assert alexa._luzes_de(None) == []


def test_sem_login_as_ferramentas_de_luz_nem_aparecem(cfg):
    assert not alexa.tem_login(cfg)
    alexa._gravar(alexa.pasta(cfg) / "conta.json", {"email": "x@y.z", "oauth": {"refresh_token": "r"}})
    assert alexa.tem_login(cfg)


@pytest.mark.parametrize("resposta,falha", [
    ({"controlResponses": [{"entityId": "e1", "code": "SUCCESS"}], "errors": []}, None),
    ({}, None),
    (None, "sem resposta"),
    ({"errors": [{"code": "ENDPOINT_UNREACHABLE"}]}, "ENDPOINT_UNREACHABLE"),
    ({"controlResponses": [{"entityId": "e1", "code": "TARGET_OFFLINE"}]}, "TARGET_OFFLINE"),
    ([1], "resposta inesperada"),
])
def test_resposta_da_alexa(resposta, falha):
    assert alexa.falha_da_resposta(resposta) == falha


class LoginFalso:
    def __init__(self):
        self.fechado = False

    async def close(self):
        self.fechado = True


@pytest.fixture
def com_alexa(cfg, monkeypatch):
    """Alexa de verdade, com o login e a chamada ao AlexaPy trocados por falsos."""
    import alexapy

    a = alexa.Alexa(cfg)
    logins, respostas, chamadas = [], [], []

    async def sessao():
        if a.login is None:
            a.login = LoginFalso()
            logins.append(a.login)
        return a.login

    async def set_light_state(login, entity_id, power_on=True, brightness=None):
        chamadas.append((entity_id, power_on, brightness))
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(a, "_sessao", sessao)
    monkeypatch.setattr(alexa, "_guardar_conta", lambda *_: None)
    monkeypatch.setattr(alexapy.AlexaAPI, "set_light_state", staticmethod(set_light_state))
    return a, logins, respostas, chamadas


async def test_sessao_morta_e_descartada_e_tenta_de_novo(com_alexa):
    from alexapy.errors import AlexapyLoginError

    a, logins, respostas, chamadas = com_alexa
    respostas += [AlexapyLoginError(), {"controlResponses": [{"code": "SUCCESS"}]}]
    await a.mudar_luz("e1", True, 30)
    assert len(logins) == 2 and logins[0].fechado and not logins[1].fechado  # a velha foi fechada
    assert chamadas == [("e1", True, 30)] * 2


async def test_sessao_que_nao_volta_pede_login(com_alexa):
    from alexapy.errors import AlexapyLoginError

    a, _logins, respostas, _ = com_alexa
    respostas += [AlexapyLoginError(), AlexapyLoginError()]
    with pytest.raises(alexa.SemLogin, match="alexa-login"):
        await a.mudar_luz("e1", False)
    assert a.login is None  # nada quebrado fica guardado para a próxima vez


async def test_recusa_no_corpo_nao_vira_acendi(com_alexa):
    a, _l, respostas, _ = com_alexa
    respostas += [{"controlResponses": [{"code": "TARGET_OFFLINE"}]}] * 2
    with pytest.raises(RuntimeError, match="TARGET_OFFLINE"):
        await a.mudar_luz("e1", True)


async def test_todas_com_a_alexa_fora_do_ar_para_na_primeira():
    a = AlexaFalsa(falha=RuntimeError("a Alexa está fora do ar"))
    chamadas = []

    async def mudar(entity_id, ligar, brilho=None):
        chamadas.append(entity_id)
        raise RuntimeError("a Alexa está fora do ar")

    a.mudar_luz = mudar
    with pytest.raises(ErroFerramenta, match="não respondeu"):
        await Casa(a)._mudar(False)({"luz": "todas"})
    assert chamadas == ["e1"]  # não refaz o login luz por luz


def test_brilho_absurdo_e_recusado():
    with pytest.raises(ErroFerramenta, match="1 a 100"):
        Casa._brilho({"brilho": "inf"})
