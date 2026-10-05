"""Timers e alarmes do Vision (pedido de 01/10): ele mesmo conta, e no fim toca o alarme e avisa.

Cada timer é uma tarefa asyncio que dorme até o fim. A lista fica em data/timers.json (gravação atômica):
se o núcleo reiniciar, os timers voltam; um que venceu com o núcleo desligado dispara assim que ele sobe
(se venceu há pouco) ou é descartado (se faz muito tempo, o aviso não serve mais).

Quem avisa é `ao_disparar` (o núcleo: alarme, fala e aviso na bandeja). Sem ele, só vai para o log.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ATRASO_MAXIMO_S = 3600  # venceu com o núcleo desligado há mais que isso: o aviso já não serve
MAXIMO_TIMERS = 20


@dataclass
class Timer:
    id: str
    nome: str
    fim: float  # time.time() do fim
    rotulo: str  # "10 minutos", "às 15:00": para falar e listar
    alarme: bool = False  # True = horário marcado ("me avisa às 15h"), False = contagem ("timer de 10 min")
    # O que fazer no fim, além de avisar (pedido de 05/10: "desliga a TV em 30 minutos"). Só um nome da lista
    # fechada de ações (montagem.py); o resultado vai para `resultado` quando dispara.
    acao: str = ""
    resultado: str = ""

    def aviso(self) -> str:
        if self.acao:
            return self.resultado or "Timer acabou."
        if self.alarme:
            return f"Alarme {self.rotulo}" + (f": {self.nome}." if self.nome else ".")
        return f"O timer {f'{self.nome!r} ' if self.nome else ''}de {self.rotulo} acabou."


class Timers:
    def __init__(self, arquivo: Path, relogio: Callable[[], float] = time.time):
        self.arquivo = arquivo
        self.relogio = relogio
        self.ao_disparar: Callable[[Timer], Awaitable[None] | None] | None = None
        self._timers: dict[str, Timer] = {}
        self._tarefas: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------ ciclo de vida

    def iniciar(self) -> None:
        """Recarrega o que estava guardado (chamar com o event loop rodando)."""
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8")) if self.arquivo.exists() else []
        except (OSError, json.JSONDecodeError):
            log.exception("timers.json ilegível; começando sem timers")
            dados = []
        agora = self.relogio()
        for d in dados:
            try:
                t = Timer(**d)
            except TypeError:
                continue
            if agora - t.fim > ATRASO_MAXIMO_S:
                continue
            self._agendar(t)
        self._gravar()

    def fechar(self) -> None:
        for tarefa in self._tarefas.values():
            tarefa.cancel()
        self._tarefas.clear()

    # ------------------------------------------------------------------ operações

    def criar(self, segundos: float, rotulo: str, nome: str = "", alarme: bool = False, acao: str = "") -> Timer:
        if len(self._timers) >= MAXIMO_TIMERS:
            raise ValueError(f"Já tem {MAXIMO_TIMERS} timers ligados.")
        t = Timer(uuid.uuid4().hex[:8], nome.strip(), self.relogio() + segundos, rotulo, alarme, acao)
        self._agendar(t)
        self._gravar()
        return t

    def listar(self) -> list[Timer]:
        return sorted(self._timers.values(), key=lambda t: t.fim)

    def falta(self, t: Timer) -> float:
        return max(0.0, t.fim - self.relogio())

    def cancelar(self, t: Timer) -> None:
        if (tarefa := self._tarefas.pop(t.id, None)) is not None:
            tarefa.cancel()
        self._timers.pop(t.id, None)
        self._gravar()

    # ------------------------------------------------------------------ por dentro

    def _agendar(self, t: Timer) -> None:
        self._timers[t.id] = t
        self._tarefas[t.id] = asyncio.get_running_loop().create_task(self._esperar(t), name=f"timer-{t.id}")

    async def _esperar(self, t: Timer) -> None:
        # Dorme em pedaços: se o PC suspender, o relógio de parede manda (e não o tempo de sono acumulado).
        while (resta := t.fim - self.relogio()) > 0:
            await asyncio.sleep(min(resta, 30))
        self._timers.pop(t.id, None)
        self._tarefas.pop(t.id, None)
        self._gravar()
        log.info("timer disparou: %s", t.rotulo)
        if self.ao_disparar is None:
            return
        try:
            r = self.ao_disparar(t)
            if inspect.isawaitable(r):
                await r
        except Exception:  # noqa: BLE001 - um aviso que falha não derruba os outros timers
            log.exception("falha ao avisar o fim do timer")

    def _gravar(self) -> None:
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(t) for t in self._timers.values()], ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.arquivo)
        except OSError:
            log.exception("não consegui gravar timers.json")


def descrever_duracao(segundos: float) -> str:
    """3725 → "1 hora, 2 minutos e 5 segundos"."""
    s = round(segundos)
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    partes = [f"{v} {nome}{'s' if v != 1 else ''}" for v, nome in ((h, "hora"), (m, "minuto"), (s, "segundo")) if v]
    if not partes:
        return "0 segundos"
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]


def para_dados(t: Timer, falta: float) -> dict[str, Any]:
    return {"id": t.id, "nome": t.nome, "rotulo": t.rotulo, "falta_s": round(falta)}
