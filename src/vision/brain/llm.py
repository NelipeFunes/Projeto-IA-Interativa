"""Interface mínima com o modelo, para trocar o Ollama por um falso nos testes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class Interrompido(Exception):
    """O Felipe cortou a resposta (falou por cima): a geração para no próximo pedaço de texto."""


@dataclass
class ChamadaFerramenta:
    nome: str
    args: dict[str, Any]


@dataclass
class RespostaLLM:
    texto: str = ""
    chamadas: list[ChamadaFerramenta] = field(default_factory=list)
    tokens: int = 0
    segundos: float = 0.0


class LLM(Protocol):
    modelo: str

    async def conversar(
        self,
        mensagens: list[dict[str, Any]],
        ferramentas: list[dict[str, Any]] | None,
        ao_texto: Callable[[str], None] | None = None,
    ) -> RespostaLLM: ...

    async def descarregar(self) -> None: ...

    async def carregar(self) -> None: ...


class OllamaLLM:
    def __init__(self, modelo: str, host: str, pensar: bool, contexto: int, temperatura: float, manter: str):
        import ollama

        self.modelo = modelo
        self.cliente = ollama.AsyncClient(host=host, timeout=300)
        self.pensar = pensar
        self.opcoes = {"num_ctx": contexto, "temperature": temperatura}
        self.manter = manter

    async def conversar(self, mensagens, ferramentas, ao_texto=None) -> RespostaLLM:
        comum = dict(
            model=self.modelo,
            messages=mensagens,
            tools=ferramentas or None,
            think=self.pensar,
            options=self.opcoes,
            keep_alive=self.manter,
        )
        if ao_texto is None:
            r = await self.cliente.chat(**comum)
            return RespostaLLM(
                texto=r.message.content or "",
                chamadas=_chamadas(r.message.tool_calls),
                tokens=r.eval_count or 0,
                segundos=(r.total_duration or 0) / 1e9,
            )
        texto, chamadas, tokens, segundos = [], [], 0, 0.0
        fluxo = await self.cliente.chat(**comum, stream=True)
        try:
            async for parte in fluxo:
                if parte.message.content:
                    texto.append(parte.message.content)
                    ao_texto(parte.message.content)  # pode levantar Interrompido: o finally fecha a conexão já
                chamadas += _chamadas(parte.message.tool_calls)
                if parte.done:
                    tokens, segundos = parte.eval_count or 0, (parte.total_duration or 0) / 1e9
        finally:
            fechar = getattr(fluxo, "aclose", None)
            if fechar is not None:
                await fechar()  # sem esperar o coletor de lixo: o Ollama para de gerar quando a conexão cai
        return RespostaLLM("".join(texto), chamadas, tokens, segundos)

    async def carregar(self) -> None:
        """Põe o modelo na VRAM já com o contexto certo. Sem o num_ctx, o Ollama carrega com o máximo do
        modelo (262 mil tokens no Qwen3.5: 13 GB, só 35% na GPU) e recarrega na primeira pergunta de verdade."""
        await self.cliente.generate(model=self.modelo, prompt="", options=self.opcoes, keep_alive=self.manter)

    async def descarregar(self) -> None:
        """Tira o modelo da VRAM (modo jogo)."""
        await self.cliente.generate(model=self.modelo, prompt="", keep_alive=0)


def _chamadas(tool_calls) -> list[ChamadaFerramenta]:
    return [ChamadaFerramenta(tc.function.name, dict(tc.function.arguments or {})) for tc in (tool_calls or [])]
