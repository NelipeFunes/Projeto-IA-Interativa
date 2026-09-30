from datetime import timedelta

import httpx
import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from jarvis import tempo
from jarvis.montagem import montar
from jarvis.server import criar_app


@pytest.fixture
async def cliente_e_agenda(cfg, host, memorias, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        fala("Oi, Felipe!"),
        chama("agenda_criar", titulo="Academia", data=amanha, hora_inicio="07:00"),
        fala("Sim o quê?"),
    ])
    async with montar(cfg, llm=llm, host=host, memorias=memorias) as j:
        app = criar_app(cfg, jarvis=j)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://jarvis") as c:
            yield c, servidor_agenda


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
