from datetime import timedelta

import pytest

from vision import tempo
from vision.tools.agenda import Agenda
from vision.tools.base import ErroFerramenta


@pytest.fixture
def agenda(cfg, host):
    return Agenda(cfg, host)


async def test_listar_hoje_inclui_eventos_e_ids(agenda):
    hoje = tempo.agora().date()
    saida = await agenda.listar({"data_inicio": hoje.isoformat()})
    assert "Aula de Cálculo I" in saida
    assert "Treino C" in saida
    assert "id: ev_aula_hoje" in saida
    assert "Dentista" not in saida  # é amanhã


async def test_listar_periodo_marca_feriado(agenda):
    hoje = tempo.agora().date()
    saida = await agenda.listar({"data_inicio": hoje.isoformat(), "data_fim": (hoje + timedelta(days=14)).isoformat()})
    assert "Nossa Senhora Aparecida" in saida and "FERIADO" in saida
    assert "Prova de Física" in saida


async def test_buscar(agenda):
    saida = await agenda.buscar({"texto": "prova"})
    assert "Prova de Física" in saida


async def test_criar_descreve_e_cria(agenda, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    args = {"titulo": "Barbeiro", "data": amanha, "hora_inicio": "16:00"}
    assert await agenda.descrever_criar(args) == "Vou criar 'Barbeiro' amanhã das 16:00 às 17:00."
    saida = await agenda.criar(args)
    assert "Evento criado: Barbeiro" in saida
    assert any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)


async def test_criar_com_conflito_avisa(agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    saida = await agenda.criar({"titulo": "Call", "data": amanha, "hora_inicio": "14:30"})
    assert "conflita" in saida


async def test_dia_inteiro(agenda):
    amanha = tempo.agora().date() + timedelta(days=1)
    desc = await agenda.descrever_criar({"titulo": "Folga", "data": amanha.isoformat()})
    assert "dia inteiro" in desc


async def test_apagar_descreve_titulo(agenda, servidor_agenda):
    assert (await agenda.descrever_apagar({"evento_id": "ev_dentista"})).startswith("Vou apagar 'Dentista' de amanhã às 14:00")
    await agenda.apagar({"evento_id": "ev_dentista"})
    assert not any(e["id"] == "ev_dentista" for e in servidor_agenda.eventos)


async def test_alterar_so_hora_mantem_data(agenda, servidor_agenda):
    desc = await agenda.descrever_alterar({"evento_id": "ev_dentista", "hora_inicio": "15:00"})
    assert "amanhã das 15:00 às 16:00" in desc
    await agenda.alterar({"evento_id": "ev_dentista", "hora_inicio": "15:00"})
    ev = next(e for e in servidor_agenda.eventos if e["id"] == "ev_dentista")
    assert ev["start"]["dateTime"][11:16] == "15:00"


async def test_data_invalida_vira_erro_legivel(agenda):
    with pytest.raises(ErroFerramenta, match="AAAA-MM-DD"):
        await agenda.listar({"data_inicio": "semana que vem"})


async def test_servidor_fora_explica_login(cfg):
    from vision.tools.mcp_host import ConexaoMCP, HostMCP

    h = HostMCP({"google-calendar": ConexaoMCP("google-calendar", None, 1)})  # nunca iniciado
    ag = Agenda(cfg, h)
    with pytest.raises(ErroFerramenta, match="Conexões"):
        await ag.listar({"data_inicio": tempo.agora().date().isoformat()})
