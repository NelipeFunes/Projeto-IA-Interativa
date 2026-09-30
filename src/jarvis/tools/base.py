"""Ferramentas que o modelo pode chamar.

Cada ferramenta tem um esquema simples, em português, pensado para modelo pequeno.
As de escrita (`escrita=True`) nunca rodam direto: o agente pede confirmação antes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Manipulador = Callable[[dict[str, Any]], Awaitable[str]]
Descritor = Callable[[dict[str, Any]], Awaitable[str]]

LIMITE_RESULTADO = 4000


class ErroFerramenta(Exception):
    """Erro que deve ser contado ao Felipe (ex.: agenda sem login)."""


@dataclass
class Ferramenta:
    nome: str
    descricao: str
    parametros: dict[str, Any]
    executar: Manipulador
    escrita: bool = False
    # Para escrita: frase que descreve a ação antes da confirmação ("Vou criar ...").
    descrever: Descritor | None = None
    grupo: str = "geral"

    def para_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.nome, "description": self.descricao, "parameters": self.parametros},
        }


def esquema(obrigatorios: list[str] | None = None, **props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": obrigatorios or []}


def texto(descricao: str) -> dict[str, Any]:
    return {"type": "string", "description": descricao}


def numero(descricao: str) -> dict[str, Any]:
    return {"type": "number", "description": descricao}


@dataclass
class Registro:
    ferramentas: dict[str, Ferramenta] = field(default_factory=dict)

    def adicionar(self, *ferramentas: Ferramenta) -> None:
        for f in ferramentas:
            self.ferramentas[f.nome] = f

    def get(self, nome: str) -> Ferramenta | None:
        return self.ferramentas.get(nome)

    def para_ollama(self) -> list[dict[str, Any]]:
        return [f.para_ollama() for f in self.ferramentas.values()]

    def nomes_do_grupo(self, grupo: str) -> list[str]:
        return [f.nome for f in self.ferramentas.values() if f.grupo == grupo]

    async def rodar(self, nome: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Executa e devolve (ok, texto). Nunca levanta: erro vira texto para o modelo."""
        f = self.ferramentas.get(nome)
        if f is None:
            return False, f"Ferramenta '{nome}' não existe. Use só as ferramentas listadas."
        try:
            saida = await f.executar(args or {})
        except ErroFerramenta as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001 - qualquer falha vira resposta, não derruba a conversa
            return False, f"Erro ao executar {nome}: {type(e).__name__}: {e}"
        if len(saida) > LIMITE_RESULTADO:
            saida = saida[:LIMITE_RESULTADO] + "\n[... resultado cortado]"
        return True, saida
