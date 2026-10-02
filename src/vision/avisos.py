"""Avisos que o Vision dá sozinho, sem ninguém perguntar, como o JARVIS ("Felipe, em 10 minutos você tem aula").

Por enquanto, os compromissos da agenda: alguns minutos antes de cada um (`agenda.avisar_antes_min`), uma vez só.
Usa a agenda do cache (vision/tools/agenda.py), então checar a cada minuto não pesa no Google.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from vision import tempo

log = logging.getLogger(__name__)

ESPERA_DEPOIS_DE_ERRO_S = (60, 120, 300)  # agenda fora (sem rede, login vencido): não insiste a cada 30 s


class AvisosDaAgenda:
    def __init__(self, agenda: Any, antes_min: float = 10, nome: str = "Felipe",
                 relogio: Callable[[], datetime] = tempo.agora):
        self.agenda = agenda
        self.antes_min = antes_min
        self.nome = nome
        self.relogio = relogio
        self._avisados: set[tuple[str, str, str]] = set()  # (dia, id, início): mudou o horário, avisa de novo
        self._erros = 0
        self._parado_ate = 0.0
        self.monotonico = time.monotonic

    async def checar(self) -> list[str]:
        """As frases a dizer agora (vazio quase sempre). Erro de agenda vira lista vazia: tenta no próximo minuto."""
        if self.antes_min <= 0:
            return []
        agora = self.relogio()
        hoje = agora.date().isoformat()
        self._avisados = {k for k in self._avisados if k[0] == hoje}  # vira o dia, a lista recomeça
        if self.monotonico() < self._parado_ate:
            return []
        try:
            eventos = await self.agenda.hoje()
        except Exception as e:  # noqa: BLE001
            self._erros += 1
            espera = ESPERA_DEPOIS_DE_ERRO_S[min(self._erros, len(ESPERA_DEPOIS_DE_ERRO_S)) - 1]
            self._parado_ate = self.monotonico() + espera
            (log.warning if self._erros == 1 else log.debug)("avisos sem agenda (%s); de novo em %d s", e, espera)
            return []
        self._erros = 0
        frases = []
        for ev in eventos:
            if ev.get("diaInteiro") or ev.get("feriado") or not ev.get("inicio"):
                continue
            try:
                h, m = (int(x) for x in str(ev["inicio"]).split(":"))
            except ValueError:
                continue
            inicio = agora.replace(hour=h, minute=m, second=0, microsecond=0)
            faltam = (inicio - agora).total_seconds() / 60
            chave = (hoje, str(ev.get("id") or ev.get("titulo")), str(ev["inicio"]))
            if 0 < faltam <= self.antes_min and chave not in self._avisados:
                self._avisados.add(chave)
                frases.append(frase_de_aviso(self.nome, ev, faltam))
        return frases


def frase_de_aviso(nome: str, ev: dict[str, Any], faltam_min: float) -> str:
    minutos = max(1, round(faltam_min))
    quando = "em 1 minuto" if minutos == 1 else f"em {minutos} minutos"
    titulo = " ".join(str(ev.get("titulo") or "um compromisso").split())[:80]
    # O título vem de convites (texto de terceiros) e vai para o alto-falante: sem o nome do assistente, para não
    # soar como um chamado ("Hey Vision, ...") para o próprio Vision ou outro aparelho.
    titulo = re.sub(r"\b(?:hey|ei|ok)?\s*vis(?:ion|ão|ao)\b[,:]?\s*", "", titulo, flags=re.IGNORECASE).strip() or \
        "um compromisso"
    local = " ".join(str(ev.get("local") or "").split())[:60]
    return f"{nome}, {quando} você tem {titulo}, às {ev['inicio']}" + (f", em {local}." if local else ".")
