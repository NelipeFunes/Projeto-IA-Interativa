"""LLM roteirizado para testar a lógica do agente sem GPU."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vision.brain.llm import ChamadaFerramenta, RespostaLLM


class LLMFalso:
    modelo = "falso"

    def __init__(self, roteiro: list[RespostaLLM | Callable[[list[dict[str, Any]]], RespostaLLM]]):
        self.roteiro = list(roteiro)
        self.chamadas: list[list[dict[str, Any]]] = []
        self.descarregado = False

    async def conversar(self, mensagens, ferramentas, ao_texto=None) -> RespostaLLM:
        self.chamadas.append([dict(m) for m in mensagens])
        if not self.roteiro:
            raise AssertionError("LLMFalso: roteiro acabou")
        passo = self.roteiro.pop(0)
        r = passo(mensagens) if callable(passo) else passo
        if ao_texto and r.texto:
            ao_texto(r.texto)
        return r

    async def descarregar(self) -> None:
        self.descarregado = True

    async def carregar(self) -> None:
        self.carregado = True


def fala(texto: str) -> RespostaLLM:
    return RespostaLLM(texto=texto)


def chama(nome: str, **args: Any) -> RespostaLLM:
    return RespostaLLM(chamadas=[ChamadaFerramenta(nome, args)])
