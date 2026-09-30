"""Cliente MCP genérico.

Cada servidor roda numa tarefa própria que é dona da conexão: assim dá para reconectar
(ex.: depois de refazer o login do Google) sem violar os escopos do anyio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp import Client, StdioServerParameters

from jarvis.config import Config

log = logging.getLogger(__name__)


@dataclass
class ResultadoMCP:
    ok: bool
    texto: str

    def json(self) -> Any:
        return json.loads(self.texto)


class ConexaoMCP:
    def __init__(self, nome: str, alvo: Any, timeout_s: float = 30):
        self.nome = nome
        self.alvo = alvo  # StdioServerParameters ou um MCPServer em memória (testes)
        self.timeout_s = timeout_s
        self.cliente: Client | None = None
        self.erro: str | None = None
        self._pronto = asyncio.Event()
        self._fechar = asyncio.Event()
        self._tarefa: asyncio.Task[None] | None = None

    async def iniciar(self) -> None:
        self._pronto.clear()
        self._fechar.clear()
        self._tarefa = asyncio.create_task(self._rodar(), name=f"mcp-{self.nome}")
        try:
            await asyncio.wait_for(self._pronto.wait(), timeout=self.timeout_s)
        except TimeoutError:
            self.erro = f"servidor '{self.nome}' não respondeu em {self.timeout_s:.0f}s"

    async def _rodar(self) -> None:
        try:
            async with Client(self.alvo, read_timeout_seconds=self.timeout_s) as c:
                self.cliente = c
                self.erro = None
                self._pronto.set()
                await self._fechar.wait()
        except BaseException as e:  # noqa: BLE001 - inclui ExceptionGroup do anyio
            self.erro = _resumir_erro(e)
            log.warning("MCP %s caiu: %s", self.nome, self.erro)
        finally:
            self.cliente = None
            self._pronto.set()

    async def fechar(self) -> None:
        self._fechar.set()
        if self._tarefa:
            try:
                await asyncio.wait_for(self._tarefa, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._tarefa.cancel()

    async def reconectar(self) -> None:
        await self.fechar()
        await self.iniciar()

    async def chamar(self, ferramenta: str, args: dict[str, Any]) -> ResultadoMCP:
        if self.cliente is None:
            return ResultadoMCP(False, f"servidor '{self.nome}' indisponível: {self.erro or 'não iniciado'}")
        try:
            r = await self.cliente.call_tool(ferramenta, args, read_timeout_seconds=self.timeout_s)
        except BaseException as e:  # noqa: BLE001
            if isinstance(e, asyncio.CancelledError):
                raise
            return ResultadoMCP(False, _resumir_erro(e))
        partes = [getattr(c, "text", "") for c in (r.content or [])]
        return ResultadoMCP(not r.is_error, "\n".join(p for p in partes if p))

    async def listar(self) -> list[str]:
        if self.cliente is None:
            return []
        return [t.name for t in (await self.cliente.list_tools()).tools]


class HostMCP:
    def __init__(self, conexoes: dict[str, ConexaoMCP]):
        self.conexoes = conexoes
        self._usos = 0  # permite `async with` aninhado (testes passam um host já aberto)

    @classmethod
    def da_config(cls, cfg: Config) -> HostMCP:
        conexoes = {}
        for nome, s in (cfg.get("mcp") or {}).items():
            if not s.get("ativo", True):
                continue
            env = dict(os.environ)
            for k, v in (s.get("env") or {}).items():
                v = str(v)
                env[k] = str(cfg.caminho(v)) if v.startswith("data/") else v
            params = StdioServerParameters(
                command=str(cfg.caminho(s["comando"])),
                args=[str(cfg.caminho(a)) if "/" in a and not a.startswith("-") else a for a in s.get("args", [])],
                env=env,
                cwd=str(cfg.raiz),
            )
            conexoes[nome] = ConexaoMCP(nome, params, float(s.get("timeout_s", 30)))
        return cls(conexoes)

    async def __aenter__(self) -> HostMCP:
        self._usos += 1
        if self._usos == 1:
            await asyncio.gather(*(c.iniciar() for c in self.conexoes.values()))
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._usos -= 1
        if self._usos == 0:
            await asyncio.gather(*(c.fechar() for c in self.conexoes.values()), return_exceptions=True)

    def status(self) -> dict[str, str]:
        return {n: ("ok" if c.cliente else f"fora: {c.erro}") for n, c in self.conexoes.items()}

    async def chamar(self, servidor: str, ferramenta: str, args: dict[str, Any]) -> ResultadoMCP:
        c = self.conexoes.get(servidor)
        if c is None:
            return ResultadoMCP(False, f"servidor MCP '{servidor}' não está configurado ou está desativado")
        return await c.chamar(ferramenta, args)

    async def reconectar(self, servidor: str) -> None:
        if servidor in self.conexoes:
            await self.conexoes[servidor].reconectar()


def _resumir_erro(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup):
        return "; ".join(_resumir_erro(x) for x in e.exceptions)
    return f"{type(e).__name__}: {e}"
