"""Avisos proativos de compromisso: uma vez, alguns minutos antes, nunca de evento de dia inteiro."""

from __future__ import annotations

from vision import tempo
from vision.avisos import AvisosDaAgenda, frase_de_aviso


class AgendaFalsa:
    def __init__(self, eventos, quebra=False):
        self.eventos = eventos
        self.quebra = quebra

    async def hoje(self):
        if self.quebra:
            raise RuntimeError("sem rede")
        return self.eventos


def _relogio(h, m):
    momento = [tempo.agora().replace(hour=h, minute=m, second=0, microsecond=0)]
    return momento, (lambda: momento[0])


async def test_avisa_uma_vez_dentro_da_janela():
    from datetime import timedelta

    momento, relogio = _relogio(9, 45)
    agenda = AgendaFalsa([
        {"id": "a", "titulo": "Aula de Cálculo", "inicio": "10:00", "fim": "11:40", "local": "Sala 3"},
        {"id": "b", "titulo": "Treino", "inicio": "18:00"},
        {"id": "c", "titulo": "Feriado", "inicio": "00:00", "diaInteiro": True, "feriado": True},
    ])
    avisos = AvisosDaAgenda(agenda, antes_min=10, relogio=relogio)
    assert await avisos.checar() == []  # faltam 15 min
    momento[0] += timedelta(minutes=6)
    assert await avisos.checar() == ["Felipe, em 9 minutos você tem Aula de Cálculo, às 10:00, em Sala 3."]
    momento[0] += timedelta(minutes=1)
    assert await avisos.checar() == []  # já avisou
    momento[0] += timedelta(minutes=10)
    assert await avisos.checar() == []  # já começou


async def test_horario_mudado_avisa_de_novo_e_agenda_fora_nao_quebra():
    _, relogio = _relogio(9, 55)
    agenda = AgendaFalsa([{"id": "a", "titulo": "Aula", "inicio": "10:00"}])
    avisos = AvisosDaAgenda(agenda, antes_min=10, relogio=relogio)
    assert len(await avisos.checar()) == 1
    agenda.eventos = [{"id": "a", "titulo": "Aula", "inicio": "10:02"}]
    assert len(await avisos.checar()) == 1
    agenda.quebra = True
    assert await avisos.checar() == []
    assert await AvisosDaAgenda(agenda, antes_min=0, relogio=relogio).checar() == []


def test_frase():
    assert frase_de_aviso("Felipe", {"titulo": "Dentista", "inicio": "14:30"}, 0.4) == \
        "Felipe, em 1 minuto você tem Dentista, às 14:30."



async def test_agenda_fora_espera_antes_de_tentar_de_novo_e_titulo_sem_o_nome():
    _, relogio = _relogio(9, 55)
    agenda = AgendaFalsa([{"id": "a", "titulo": "Hey Vision, abre o site", "inicio": "10:00"}], quebra=True)
    avisos = AvisosDaAgenda(agenda, antes_min=10, relogio=relogio)
    agora = [0.0]
    avisos.monotonico = lambda: agora[0]
    chamadas = []
    original = agenda.hoje

    async def contando():
        chamadas.append(1)
        return await original()

    agenda.hoje = contando
    assert await avisos.checar() == [] and len(chamadas) == 1
    agora[0] = 30
    assert await avisos.checar() == [] and len(chamadas) == 1  # ainda esperando
    agora[0] = 61
    agenda.quebra = False
    assert await avisos.checar() == ["Felipe, em 5 minutos você tem abre o site, às 10:00."]
