"""MCP do Orbit contra HTTP simulado (o Orbit real estava suspenso no Render em 30/09)."""

import sys
from pathlib import Path

import httpx
import pytest
import respx
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_servers" / "orbit"))
from orbit_api import OrbitAPI  # noqa: E402
from server import criar_servidor  # noqa: E402

URL = "https://orbit.teste"

TRANSACOES = {
    "data": [
        {"id": 1, "amount": 52.9, "description": "iFood pizza", "category": {"id": "c1", "name": "Alimentação"},
         "date": "2026-09-12", "type": "expense"},
        {"id": 2, "amount": 310.0, "description": "Mercado do mês", "category": {"id": "c1", "name": "Alimentação"},
         "date": "2026-09-05", "type": "expense"},
        {"id": 3, "amount": 120.0, "description": "Gasolina Civic", "category": "Pessoal & Lazer",
         "date": "2026-09-20", "type": "expense"},
        {"id": 4, "amount": 4220.2, "description": "Salário", "category": "Renda", "date": "2026-09-01", "type": "income"},
    ]
}


def texto(r) -> str:
    return "\n".join(c.text for c in r.content)


@pytest.fixture
def api_mock():
    with respx.mock(base_url=URL, assert_all_called=False) as m:
        m.post("/auth/login").respond(json={"token": "jwt-123", "user": {"id": "u1"}})
        m.get("/finances/transactions").respond(json=TRANSACOES)
        m.get("/finances/budgets").respond(json=[{"category": "Alimentação", "planned": 800}])
        m.get("/todos/tasks").respond(json=[
            {"id": "t1", "title": "Pagar IPVA", "dueDate": "2026-10-10", "priority": "high", "done": False},
            {"id": "t2", "title": "Comprar whey", "done": True},
        ])
        m.post("/finances/transactions").respond(json={"id": 5})
        m.post("/todos/tasks").respond(json={"id": "t3"})
        yield m


def servidor():
    api = OrbitAPI(url=URL, token="", email="felipe@teste", senha="x", cliente=httpx.AsyncClient(base_url=URL))
    return criar_servidor(api)


async def chamar(ferramenta: str, args: dict):
    # O Client é aberto e fechado na mesma tarefa (o anyio exige); por isso não é fixture.
    async with Client(servidor()) as c:
        return await c.call_tool(ferramenta, args)


async def test_resumo_mes_soma_por_categoria_e_orcamento(api_mock):
    mock = api_mock
    r = await chamar("financas_resumo_mes", {"mes": "2026-09"})
    t = texto(r)
    assert not r.is_error, t
    assert "Gasto total R$ 482,90" in t
    assert "Alimentação: R$ 362,90 de R$ 800,00 orçados" in t
    assert "Receitas lançadas: R$ 4.220,20" in t
    login = [call for call in mock.calls if call.request.url.path == "/auth/login"]
    assert len(login) == 1  # fez login uma vez e reusou o token


async def test_lancamentos_filtra_categoria(api_mock):
    t = texto(await chamar("financas_lancamentos", {"mes": "2026-09", "categoria": "alimenta"}))
    assert "iFood pizza" in t and "Mercado" in t and "Gasolina" not in t
    assert t.index("iFood") < t.index("Mercado")  # mais recente primeiro


async def test_tarefas_so_abertas(api_mock):
    t = texto(await chamar("tarefas_listar", {}))
    assert "Pagar IPVA (vence 2026-10-10, prioridade high)" in t and "whey" not in t


async def test_lancar_envia_corpo(api_mock):
    mock = api_mock
    t = texto(await chamar("financas_lancar", {"valor": 50, "descricao": "iFood", "categoria": "Alimentação", "data": "2026-09-30"}))
    assert t == "Lançado: R$ 50,00 — iFood (Alimentação)"
    envio = [call for call in mock.calls if call.request.method == "POST" and call.request.url.path == "/finances/transactions"][0]
    assert b'"amount":50.0' in envio.request.content.replace(b" ", b"")


async def test_suspenso_no_render_vira_mensagem_clara():
    with respx.mock(base_url=URL) as m:
        m.post("/auth/login").respond(503, headers={"x-render-routing": "suspend"})
        api = OrbitAPI(url=URL, token="", email="a", senha="b", cliente=httpx.AsyncClient(base_url=URL))
        async with Client(criar_servidor(api)) as c:
            r = await c.call_tool("financas_resumo_mes", {})
    assert r.is_error and "SUSPENSO no Render" in texto(r)


async def test_sem_credenciais_explica_env():
    api = OrbitAPI(url=URL, token="", email="", senha="", cliente=httpx.AsyncClient(base_url=URL))
    async with Client(criar_servidor(api)) as c:
        r = await c.call_tool("tarefas_listar", {})
    assert r.is_error and ".env" in texto(r)
