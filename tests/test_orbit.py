"""MCP do Orbit contra HTTP simulado, no formato real da API (conferido no front do Orbit em 02/10)."""

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
        m.post("/auth/login").respond(json={"accessToken": "jwt-123", "user": {"id": "u1"}})
        m.get("/finances/transactions").respond(json=TRANSACOES)
        m.get("/finances/budgets").respond(json=[{"category": "Alimentação", "planned": 800}])
        m.get("/todos/tasks").respond(json=[
            {"id": "t1", "title": "Pagar IPVA", "due": "2026-10-10", "priority": "high", "done": False},
            {"id": "t2", "title": "Comprar whey", "priority": "medium", "done": True},
            {"id": "t3", "title": "Ligar para o banco", "priority": "low", "done": False},
            {"id": "t4", "title": "Ligar para a escola", "priority": "low", "done": False},
        ])
        m.patch(url__regex=r"/todos/tasks/[^/]+$").respond(json={"ok": True})
        m.delete(url__regex=r"/todos/tasks/[^/]+$").respond(204)
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
    assert "Pagar IPVA (vence 2026-10-10, prioridade alta)" in t and "whey" not in t
    todas = texto(await chamar("tarefas_listar", {"incluir_concluidas": True}))
    assert "Comprar whey (prioridade média, feita)" in todas


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


async def test_sem_credenciais_aponta_para_conexoes():
    api = OrbitAPI(url=URL, token="", email="", senha="", cliente=httpx.AsyncClient(base_url=URL))
    async with Client(criar_servidor(api)) as c:
        r = await c.call_tool("tarefas_listar", {})
    assert r.is_error and "Conexões" in texto(r)


def _corpo(mock, metodo, caminho):
    import json

    chamada = [c for c in mock.calls if c.request.method == metodo and c.request.url.path == caminho][-1]
    return json.loads(chamada.request.content) if chamada.request.content else None


async def test_criar_tarefa_manda_due_e_prioridade_em_ingles(api_mock):
    """O Orbit usa `due`: com `dueDate` (até 02/10) a data das tarefas criadas pelo Vision se perdia."""
    await chamar("tarefas_criar", {"titulo": "Trocar o óleo", "vencimento": "2026-10-15", "prioridade": "alta"})
    assert _corpo(api_mock, "POST", "/todos/tasks") == {"title": "Trocar o óleo", "priority": "high", "done": False,
                                                        "due": "2026-10-15"}


async def test_concluir_acha_pelo_titulo_e_reabre(api_mock):
    t = texto(await chamar("tarefas_concluir", {"tarefa": "pagar o ipva"}))
    assert t == "Marquei como feita: Pagar IPVA" and _corpo(api_mock, "PATCH", "/todos/tasks/t1") == {"done": True}
    t = texto(await chamar("tarefas_concluir", {"tarefa": "comprar whey", "desfazer": True}))
    assert t == "Reabri a tarefa: Comprar whey" and _corpo(api_mock, "PATCH", "/todos/tasks/t2") == {"done": False}


async def test_tarefa_ambigua_ou_ausente_nao_mexe_em_nada(api_mock):
    r = await chamar_bruto("tarefas_concluir", {"tarefa": "ligar"})
    assert r.is_error and "Ligar para o banco" in texto(r) and "Ligar para a escola" in texto(r)
    r = await chamar_bruto("tarefas_apagar", {"tarefa": "lavar o carro"})
    assert r.is_error and "Não achei" in texto(r)
    assert not [c for c in api_mock.calls if c.request.method in ("PATCH", "DELETE")]


async def test_editar_e_apagar(api_mock):
    await chamar("tarefas_editar", {"tarefa": "IPVA", "vencimento": "2026-10-20", "prioridade": "baixa"})
    assert _corpo(api_mock, "PATCH", "/todos/tasks/t1") == {"due": "2026-10-20", "priority": "low"}
    await chamar("tarefas_editar", {"tarefa": "IPVA", "sem_vencimento": True})
    assert _corpo(api_mock, "PATCH", "/todos/tasks/t1") == {"due": None}
    r = await chamar_bruto("tarefas_editar", {"tarefa": "IPVA"})
    assert r.is_error and "Mudar o quê" in texto(r)
    assert texto(await chamar("tarefas_apagar", {"tarefa": "ligar para o banco"})) == "Apaguei a tarefa: Ligar para o banco"
    assert [c.request.url.path for c in api_mock.calls if c.request.method == "DELETE"] == ["/todos/tasks/t3"]


async def chamar_bruto(ferramenta: str, args: dict):
    async with Client(servidor()) as c:
        return await c.call_tool(ferramenta, args)


async def test_login_com_codigo_por_email():
    """O Orbit pode responder ao login com um desafio: o código vai para o e-mail e entra por /auth/code/verify."""
    from orbit_api import PrecisaCodigo

    with respx.mock(base_url=URL) as m:
        m.post("/auth/login").respond(json={"challengeToken": "desafio-1", "maskedEmail": "f***@exemplo.com"})
        verificar = m.post("/auth/code/verify").respond(json={"accessToken": "jwt-novo"})
        api = OrbitAPI(url=URL, token="", email="a@b.com", senha="s", cliente=httpx.AsyncClient(base_url=URL))
        with pytest.raises(PrecisaCodigo) as e:
            await api.login()
        assert e.value.desafio == "desafio-1" and e.value.email_mascarado == "f***@exemplo.com"
        assert await api.confirmar_codigo("desafio-1", "123456") == "jwt-novo"
        import json

        assert json.loads(verificar.calls[0].request.content) == {"challengeToken": "desafio-1", "code": "123456"}


async def test_codigo_errado_vira_mensagem():
    from orbit_api import ErroOrbit

    with respx.mock(base_url=URL) as m:
        m.post("/auth/code/verify").respond(400, json={"message": "invalid"})
        api = OrbitAPI(url=URL, token="", email="a@b.com", senha="s", cliente=httpx.AsyncClient(base_url=URL))
        with pytest.raises(ErroOrbit, match="não aceitou o código"):
            await api.confirmar_codigo("d", "000000")


async def test_id_da_tarefa_nao_vira_caminho():
    """O id vem do servidor, mas vai escapado na URL: um id com "/" não muda a rota."""
    with respx.mock(base_url=URL) as m:
        rota = m.patch("/todos/tasks/..%2Fusers%2Fme").respond(json={})
        api = OrbitAPI(url=URL, token="jwt", email="", senha="", cliente=httpx.AsyncClient(base_url=URL))
        await api.mudar_tarefa("../users/me", {"done": True})
        assert rota.called
