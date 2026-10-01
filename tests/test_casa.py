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
