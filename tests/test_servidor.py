from datetime import timedelta

import httpx
import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from vision import tempo
from vision.montagem import montar
from vision.server import criar_app


@pytest.fixture
async def cliente_e_agenda(cfg, host, memorias, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        fala("Oi, Felipe!"),
        chama("agenda_criar", titulo="Academia", data=amanha, hora_inicio="07:00"),
        fala("Sim o quê?"),
    ])
    cfg.bruto.setdefault("assistente", {})["confirmacao"] = "todas"  # este teste é do modo com confirmação
    async with montar(cfg, llm=llm, host=host, memorias=memorias) as j:
        app = criar_app(cfg, vision=j, token="segredo-de-teste")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vision",
                                     headers={"Authorization": "Bearer segredo-de-teste"}) as c:
            yield c, servidor_agenda


async def test_criar_nao_e_sensivel_e_e_feito_na_hora(cfg, host, memorias, servidor_agenda):
    """`assistente.confirmacao: sensiveis` (pedido de 01/10): marcar já marca."""
    cfg.bruto.setdefault("assistente", {})["confirmacao"] = "sensiveis"
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        chama("agenda_criar", titulo="Academia", data=amanha, hora_inicio="07:00"),
        fala("Marquei academia amanhã às 7h."),
    ])
    async with montar(cfg, llm=llm, host=host, memorias=memorias) as j:
        app = criar_app(cfg, vision=j, token="segredo-de-teste")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://vision",
                                     headers={"Authorization": "Bearer segredo-de-teste"}) as c:
            r = await c.post("/conversa", json={"texto": "marca academia amanhã 7h", "sessao": "a"})
    assert r.json()["aguardando_confirmacao"] is False
    assert "Confirma?" not in r.json()["resposta"]
    assert any(e["summary"] == "Academia" for e in servidor_agenda.eventos)


async def test_conversa_e_confirmacao_entre_requisicoes(cliente_e_agenda):
    c, agenda = cliente_e_agenda
    r = await c.post("/conversa", json={"texto": "oi", "sessao": "a"})
    assert r.status_code == 200 and r.json()["resposta"] == "Oi, Felipe!"

    r = await c.post("/conversa", json={"texto": "marca academia amanhã 7h", "sessao": "a"})
    assert r.json()["aguardando_confirmacao"] is True
    assert "Confirma?" in r.json()["resposta"]

    # outra sessão não herda a confirmação pendente
    r = await c.post("/conversa", json={"texto": "sim", "sessao": "b"})
    assert r.status_code == 200
    assert not any(e["summary"] == "Academia" for e in agenda.eventos)

    r = await c.post("/conversa", json={"texto": "sim", "sessao": "a"})
    assert r.json()["resposta"].startswith("Feito. Criei 'Academia'")
    assert any(e["summary"] == "Academia" for e in agenda.eventos)


async def test_validacao_e_status(cliente_e_agenda):
    c, _ = cliente_e_agenda
    assert (await c.post("/conversa", json={"texto": ""})).status_code == 400
    assert (await c.post("/conversa", content=b"nao-json")).status_code == 400
    st = (await c.get("/status")).json()
    assert st["mcp"] == {"google-calendar": "ok"}
    assert "agenda_listar" in st["ferramentas"]
