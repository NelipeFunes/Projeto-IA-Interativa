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


# No modo jogo, uma pergunta pelo atalho (ou pela janela) carrega o modelo de novo: com manter_carregado "-1m" ele
# ficaria preso na VRAM até o jogo fechar (revisão do PR 44). Depois de descarregar, cada resposta só segura 2 min.
MANTER_NO_JOGO = "2m"


class OllamaLLM:
    def __init__(self, modelo: str, host: str, pensar: bool, contexto: int, temperatura: float, manter: str | int):
        import ollama

        self.modelo = modelo
        self.cliente = ollama.AsyncClient(host=host, timeout=300)
        self.pensar = pensar
        self.opcoes = {"num_ctx": contexto, "temperature": temperatura}
        self.manter_normal = manter
        self.manter = manter  # o que vale agora: MANTER_NO_JOGO depois de descarregar, o normal depois de carregar

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
            try:
                r = await self.cliente.chat(**comum)
            finally:
                await self._jogo_comecou_no_meio(comum["keep_alive"])
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
            # Também quando a resposta é cortada (Interrompido) ou falha: o jogo pode ter aberto no meio (2ª revisão)
            await self._jogo_comecou_no_meio(comum["keep_alive"])
        return RespostaLLM("".join(texto), chamadas, tokens, segundos)

    async def _jogo_comecou_no_meio(self, usado) -> None:
        """O jogo abriu enquanto esta resposta saía: ela terminou com o keep_alive de antes e desfez o descarregar."""
        if self.manter == MANTER_NO_JOGO and usado != MANTER_NO_JOGO:
            try:
                await self.cliente.generate(model=self.modelo, prompt="", keep_alive=0)
            except Exception:  # noqa: BLE001 - não esconde o erro da própria resposta (está num finally)
                pass

    async def soltar(self) -> None:
        """O Vision está fechando: com "-1m" o Ollama seguraria o modelo na placa para sempre, e um jogo aberto
        depois (sem o Vision para descarregar) ficaria sem os 3,2 GB (2ª revisão do PR 44)."""
        await self.cliente.generate(model=self.modelo, prompt="", keep_alive=0)

    async def carregar(self) -> None:
        """Põe o modelo na VRAM já com o contexto certo. Sem o num_ctx, o Ollama carrega com o máximo do
        modelo (262 mil tokens no Qwen3.5: 13 GB, só 35% na GPU) e recarrega na primeira pergunta de verdade."""
        self.manter = self.manter_normal  # fim do jogo: volta a ficar o tempo configurado
        await self.cliente.generate(model=self.modelo, prompt="", options=self.opcoes, keep_alive=self.manter)

    async def descarregar(self) -> None:
        """Tira o modelo da VRAM (modo jogo). Até carregar de novo, cada resposta só o segura por 2 min."""
        self.manter = MANTER_NO_JOGO
        await self.cliente.generate(model=self.modelo, prompt="", keep_alive=0)


def _chamadas(tool_calls) -> list[ChamadaFerramenta]:
    return [ChamadaFerramenta(tc.function.name, dict(tc.function.arguments or {})) for tc in (tool_calls or [])]
