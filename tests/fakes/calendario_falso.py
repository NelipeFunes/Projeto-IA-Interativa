"""Servidor MCP falso com os mesmos nomes e o mesmo formato de resposta do @cocal/google-calendar-mcp.

Usado nos testes e nas avaliações, enquanto o login do Google não existe. Roda em memória
(`mcp.Client(servidor)`) ou por stdio (`python calendario_falso.py`).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

FERIADOS = "pt-br.brazilian#holiday@group.v.calendar.google.com"


def _evento(id_: str, titulo: str, inicio: str, fim: str, cal: str = "primary", local: str | None = None) -> dict:
    tem_hora = "T" in inicio
    chave = "dateTime" if tem_hora else "date"
    ev = {
        "id": id_,
        "summary": titulo,
        "start": {chave: inicio + ("-03:00" if tem_hora else "")},
        "end": {chave: fim + ("-03:00" if tem_hora else "")},
        "status": "confirmed",
        "calendarId": cal,
    }
    if local:
        ev["location"] = local
    return ev


def eventos_exemplo(hoje: date) -> list[dict]:
    def d(n: int) -> str:
        return (hoje + timedelta(days=n)).isoformat()

    return [
        _evento("ev_aula_hoje", "Aula de Cálculo I", f"{d(0)}T19:30:00", f"{d(0)}T21:10:00", local="Sala P1"),
        _evento("ev_trab_hoje", "Trabalho — bloco 1", f"{d(0)}T08:00:00", f"{d(0)}T12:00:00"),
        _evento("ev_treino_hoje", "Treino C", f"{d(0)}T17:30:00", f"{d(0)}T18:30:00"),
        _evento("ev_dentista", "Dentista", f"{d(1)}T14:00:00", f"{d(1)}T15:00:00", local="Clínica Sorriso"),
        _evento("ev_prova_fisica", "Prova de Física (P1)", f"{d(8)}T19:30:00", f"{d(8)}T21:30:00"),
        _evento("ev_feriado", "Dia de Nossa Senhora Aparecida", d(12), d(13), cal=FERIADOS),
    ]


def criar_servidor(hoje: date | None = None) -> MCPServer:
    hoje = hoje or date.today()
    srv = MCPServer("calendario-falso")
    eventos: list[dict] = eventos_exemplo(hoje)
    srv.eventos = eventos  # type: ignore[attr-defined] - para os testes inspecionarem

    def _no_periodo(ev: dict, t0: str, t1: str) -> bool:
        ini = ev["start"].get("dateTime") or ev["start"]["date"]
        return t0[:19] <= ini[:19] <= t1[:19]

    def _cals(calendar_id: Any) -> list[str]:
        return calendar_id if isinstance(calendar_id, list) else [calendar_id]

    @srv.tool(name="list-events")
    def list_events(calendarId: Any, timeMin: str = "", timeMax: str = "", timeZone: str = "") -> str:
        cals = _cals(calendarId)
        achados = [e for e in eventos if e["calendarId"] in cals and _no_periodo(e, timeMin, timeMax)]
        achados.sort(key=lambda e: e["start"].get("dateTime") or e["start"]["date"])
        return json.dumps({"events": achados, "totalCount": len(achados)})

    @srv.tool(name="search-events")
    def search_events(calendarId: Any, query: str, timeMin: str, timeMax: str, timeZone: str = "") -> str:
        cals = _cals(calendarId)
        q = query.lower()
        achados = [
            e for e in eventos
            if e["calendarId"] in cals and q in e["summary"].lower() and _no_periodo(e, timeMin, timeMax)
        ]
        return json.dumps({"events": achados, "totalCount": len(achados), "query": query})

    @srv.tool(name="get-event")
    def get_event(calendarId: str, eventId: str) -> str:
        for e in eventos:
            if e["id"] == eventId:
                return json.dumps({"event": e})
        raise ToolError(f"Event not found: {eventId}")

    @srv.tool(name="create-event")
    def create_event(
        calendarId: str, summary: str, start: str, end: str, timeZone: str = "",
        location: str | None = None, description: str | None = None,
    ) -> str:
        ev = _evento("ev_" + uuid.uuid4().hex[:8], summary, start, end, calendarId, location)
        conflitos = [
            e for e in eventos
            if "dateTime" in e["start"] and "T" in start
            and e["start"]["dateTime"][:19] < end[:19] and start[:19] < e["end"]["dateTime"][:19]
        ]
        eventos.append(ev)
        return json.dumps({"event": ev, "conflicts": [{"event": {"id": c["id"], "title": c["summary"]}} for c in conflitos] or None})

    @srv.tool(name="update-event")
    def update_event(
        calendarId: str, eventId: str, summary: str | None = None, start: str | None = None,
        end: str | None = None, timeZone: str = "", location: str | None = None,
    ) -> str:
        for e in eventos:
            if e["id"] == eventId:
                if summary:
                    e["summary"] = summary
                if location:
                    e["location"] = location
                if start and end:
                    chave = "dateTime" if "T" in start else "date"
                    e["start"] = {chave: start + ("-03:00" if chave == "dateTime" else "")}
                    e["end"] = {chave: end + ("-03:00" if chave == "dateTime" else "")}
                return json.dumps({"event": e})
        raise ToolError(f"Event not found: {eventId}")

    @srv.tool(name="delete-event")
    def delete_event(calendarId: str, eventId: str) -> str:
        for i, e in enumerate(eventos):
            if e["id"] == eventId:
                eventos.pop(i)
                return json.dumps({"success": True, "eventId": eventId, "message": "Event deleted successfully"})
        raise ToolError(f"Event not found: {eventId}")

    return srv


if __name__ == "__main__":
    criar_servidor(datetime.now().date()).run("stdio")
