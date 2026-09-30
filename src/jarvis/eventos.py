"""Barramento de eventos do núcleo: agente, voz e bandeja publicam; a tela (pelo WebSocket) assina.

Os eventos são dicionários no formato de ui/src/tipos.ts ({"tipo": "estado", "valor": "ouvindo"}, ...).
Uma tela lenta nunca trava o núcleo: cada assinante tem uma fila limitada e, cheia, perde os mais velhos.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

Evento = dict[str, Any]

# Guardados para quem conecta depois (a tela que abre no meio de uma fala já nasce no estado certo).
LEMBRADOS = ("estado", "aviso", "pendente")


class Barramento:
    def __init__(self, tamanho_fila: int = 500):
        self._assinantes: set[asyncio.Queue[Evento]] = set()
        self._ouvintes: list[Callable[[Evento], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self.tamanho_fila = tamanho_fila
        self.ultimos: dict[str, Evento] = {}

    def ligar(self, loop: asyncio.AbstractEventLoop) -> None:
        """O loop do núcleo: publicar_de_thread entrega nele."""
        self._loop = loop

    def publicar(self, evento: Evento) -> None:
        """Só no loop do núcleo (agente e voz já rodam nele). De outra thread, use publicar_de_thread."""
        tipo = evento.get("tipo")
        if tipo in LEMBRADOS:
            self.ultimos[tipo] = evento
        elif tipo == "pendente_resolvido" and self.ultimos.get("pendente", {}).get("pendente", {}).get("id") == evento.get("id"):
            self.ultimos.pop("pendente", None)
        for fila in list(self._assinantes):
            if fila.full():
                try:
                    fila.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            fila.put_nowait(evento)
        for ouvinte in list(self._ouvintes):
            try:
                ouvinte(evento)
            except Exception:  # noqa: BLE001 - um ouvinte com defeito (ex.: bandeja) não derruba os outros
                log.exception("ouvinte do barramento falhou")

    def publicar_de_thread(self, evento: Evento) -> None:
        if self._loop is None:
            raise RuntimeError("barramento sem loop: chame ligar() no núcleo")
        self._loop.call_soon_threadsafe(self.publicar, evento)

    def ouvir(self, ouvinte: Callable[[Evento], None]) -> None:
        """Callback síncrono chamado no loop a cada evento (a bandeja usa para mudar a cor do ícone)."""
        self._ouvintes.append(ouvinte)

    @contextmanager
    def assinar(self) -> Iterator[asyncio.Queue[Evento]]:
        fila: asyncio.Queue[Evento] = asyncio.Queue(maxsize=self.tamanho_fila)
        self._assinantes.add(fila)
        try:
            yield fila
        finally:
            self._assinantes.discard(fila)

    @property
    def assinantes(self) -> int:
        return len(self._assinantes)
