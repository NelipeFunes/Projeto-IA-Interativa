"""Cache da agenda: perguntas dentro da janela (ontem até +14 dias) não esperam o Google."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from vision import tempo
from vision.tools.agenda import LIMITE_GOOGLE, Agenda, _no_periodo
from vision.tools.base import ErroFerramenta
from vision.tools.mcp_host import ResultadoMCP


class Contador:
    """Embrulha o HostMCP e conta as consultas de lista ao servidor."""

    def __init__(self, host):
        self.host = host
        self.listas = 0

    async def chamar(self, servidor, ferramenta, args):
        if ferramenta == "list-events":
            self.listas += 1
        return await self.host.chamar(servidor, ferramenta, args)

    async def reconectar(self, servidor):
        await self.host.reconectar(servidor)


@pytest.fixture
def contador(host):
    return Contador(host)


@pytest.fixture
def agenda(cfg, contador):
    return Agenda(cfg, contador)


async def test_dentro_da_janela_responde_do_cache_igual_ao_google(agenda, contador, cfg, host):
    hoje = tempo.agora().date()
    args = {"data_inicio": hoje.isoformat(), "data_fim": (hoje + timedelta(days=agenda.cache_dias)).isoformat()}
    direto = await Agenda(cfg, host).listar(args)  # sem cache: o Google (falso) filtra
    await agenda.atualizar_cache()
    assert contador.listas == 1
    assert await agenda.listar(args) == direto
    amanha = (hoje + timedelta(days=1)).isoformat()
    saida = await agenda.listar({"data_inicio": amanha})
    assert "Dentista" in saida and "Aula de Cálculo I" not in saida
    assert contador.listas == 1  # nenhuma consulta nova


async def test_painel_de_hoje_usa_o_cache(agenda, contador):
    await agenda.atualizar_cache()
    painel = await agenda.hoje()
    assert any(e["titulo"] == "Aula de Cálculo I" for e in painel)
    assert contador.listas == 1


async def test_fora_da_janela_vai_ao_google(agenda, contador):
    await agenda.atualizar_cache()
    longe = (tempo.agora().date() + timedelta(days=40)).isoformat()
    await agenda.listar({"data_inicio": longe})
    assert contador.listas == 2


async def test_cache_velho_vai_ao_google(agenda, contador):
    agora = [1000.0]
    agenda.relogio = lambda: agora[0]
    await agenda.atualizar_cache()
    agora[0] += agenda.cache_s + 1
    await agenda.listar({"data_inicio": tempo.agora().date().isoformat()})
    assert contador.listas == 2


async def test_criar_atualiza_o_cache(agenda, contador):
    await agenda.atualizar_cache()
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    await agenda.criar({"titulo": "Barbeiro", "data": amanha, "hora_inicio": "16:00"})
    await agenda._atualizando  # o cache novo vem em segundo plano
    saida = await agenda.listar({"data_inicio": amanha})
    assert "Barbeiro" in saida
    assert contador.listas == 2  # a foto inicial e a refeita depois de criar; a pergunta veio do cache


async def test_foto_tirada_durante_uma_mudanca_e_descartada(agenda, contador):
    """Uma atualização que começou antes de criar o evento não pode gravar a agenda de antes."""
    liberar = asyncio.Event()
    chamar_original = contador.chamar

    async def lento(servidor, ferramenta, args):
        if ferramenta == "list-events" and not liberar.is_set():
            await liberar.wait()
        return await chamar_original(servidor, ferramenta, args)

    contador.chamar = lento
    velha = asyncio.create_task(agenda.atualizar_cache())
    await asyncio.sleep(0)
    agenda._geracao += 1  # o que o _invalidar faz quando a agenda muda
    liberar.set()
    await velha
    assert agenda._janela is None


def test_periodo_igual_ao_do_google():
    hoje = tempo.agora().date()
    ontem, amanha = hoje - timedelta(days=1), hoje + timedelta(days=1)

    def com_hora(d, h1, h2):
        return {"start": {"dateTime": f"{d}T{h1}:00-03:00"}, "end": {"dateTime": f"{d}T{h2}:00-03:00"}}

    assert _no_periodo(com_hora(hoje, "10:00", "11:00"), hoje, hoje)
    assert not _no_periodo(com_hora(amanha, "10:00", "11:00"), hoje, hoje)
    assert not _no_periodo(com_hora(ontem, "10:00", "11:00"), hoje, hoje)
    dia_inteiro = {"start": {"date": hoje.isoformat()}, "end": {"date": amanha.isoformat()}}
    assert _no_periodo(dia_inteiro, hoje, hoje)
    assert not _no_periodo(dia_inteiro, amanha, amanha)  # o fim do dia inteiro é exclusivo
    virada = {"start": {"dateTime": f"{ontem}T23:00:00-03:00"}, "end": {"dateTime": f"{hoje}T01:00:00-03:00"}}
    assert _no_periodo(virada, hoje, hoje)  # cruza a meia-noite: aparece nos dois dias
    assert not _no_periodo({"start": {}}, hoje, hoje)


class Resposta:
    """Host falso que devolve sempre o mesmo texto para list-events."""

    def __init__(self, texto):
        self.texto = texto
        self.listas = 0

    async def chamar(self, servidor, ferramenta, args):
        self.listas += 1
        return ResultadoMCP(True, self.texto)

    async def reconectar(self, servidor):
        pass


async def test_resposta_sem_eventos_nao_vira_agenda_vazia(cfg):
    ag = Agenda(cfg, Resposta("Serviço instável, tente de novo"))  # texto, não JSON
    with pytest.raises(ErroFerramenta):
        await ag.atualizar_cache()
    assert ag._janela is None


async def test_calendario_no_limite_do_google_nao_entra_no_cache(cfg):
    import json

    hoje = tempo.agora().date().isoformat()
    eventos = [{"id": f"e{i}", "calendarId": "primary", "start": {"date": hoje}, "end": {"date": hoje}}
               for i in range(LIMITE_GOOGLE)]
    host = Resposta(json.dumps({"events": eventos}))
    ag = Agenda(cfg, host)
    await ag.atualizar_cache()
    assert ag._janela is None  # pode ter faltado evento: a pergunta vai direto ao Google
    await ag.listar({"data_inicio": hoje})
    assert host.listas == 2
