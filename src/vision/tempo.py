"""Datas em português e no fuso do Felipe. Modelos pequenos erram conta de calendário,
então o prompt recebe uma tabela pronta dos próximos dias."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
DIAS_CURTOS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def agora() -> datetime:
    return datetime.now(FUSO)


def descrever_momento(dt: datetime) -> str:
    return f"{DIAS[dt.weekday()]}, {dt.day} de {MESES[dt.month - 1]} de {dt.year}, {dt:%H:%M}"


def tabela_de_datas(hoje: date, dias: int = 8) -> str:
    """Linhas tipo 'amanhã: quarta-feira 01/10/2026 (2026-10-01)' para o prompt."""
    nomes = {0: "hoje", 1: "amanhã", 2: "depois de amanhã"}
    linhas = []
    for i in range(dias):
        d = hoje + timedelta(days=i)
        rotulo = nomes.get(i, DIAS[d.weekday()])
        linhas.append(f"- {rotulo}: {DIAS[d.weekday()]} {d:%d/%m/%Y} ({d.isoformat()})")
    proxima_segunda = hoje + timedelta(days=(7 - hoje.weekday()) or 7)
    linhas.append(f"- a semana que vem começa na segunda {proxima_segunda:%d/%m} ({proxima_segunda.isoformat()})")
    return "\n".join(linhas)


def rotulo_dia(d: date, hoje: date) -> str:
    delta = (d - hoje).days
    if delta == 0:
        return "hoje"
    if delta == 1:
        return "amanhã"
    if delta == -1:
        return "ontem"
    return f"{DIAS_CURTOS[d.weekday()]} {d:%d/%m}"
