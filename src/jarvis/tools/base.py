"""Ferramentas que o modelo pode chamar.

Cada ferramenta tem um esquema simples, em português, pensado para modelo pequeno.
As de escrita (`escrita=True`) nunca rodam direto: o agente pede confirmação antes.
"""

from __future__ import annotations

import difflib
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

    def normalizar_args(self, args: dict[str, Any] | None) -> dict[str, Any]:
        """Modelo pequeno às vezes manda `event_id` em vez de `evento_id`: traduz apelidos e nomes parecidos."""
        props = set((self.parametros.get("properties") or {}).keys())
        saida: dict[str, Any] = {}
        for chave, valor in (args or {}).items():
            if chave in props:
                saida[chave] = valor
                continue
            alvo = APELIDOS.get(chave.lower())
            alvo = alvo if alvo in props else next((a for a in (APELIDOS_MULTI.get(chave.lower()) or []) if a in props), None)
            if alvo is None:
                parecidos = difflib.get_close_matches(chave, list(props), n=1, cutoff=0.6)
                alvo = parecidos[0] if parecidos else None
            if alvo is not None and alvo not in saida:
                saida[alvo] = valor
        return saida


APELIDOS = {
    "event_id": "evento_id", "eventid": "evento_id", "id": "evento_id", "evento": "evento_id",
    "title": "titulo", "summary": "titulo", "nome": "titulo", "name": "titulo",
    "date": "data", "dia": "data", "start_date": "data_inicio", "end_date": "data_fim",
    "start_time": "hora_inicio", "time": "hora_inicio", "hora": "hora_inicio", "start": "hora_inicio",
    "end_time": "hora_fim", "end": "hora_fim",
    "location": "local", "place": "local", "description": "descricao", "notes": "descricao",
    "amount": "valor", "value": "valor", "price": "valor", "category": "categoria",
    "due_date": "vencimento", "due": "vencimento", "priority": "prioridade",
    "month": "mes", "fact": "fato", "memoria": "fato", "memory": "fato", "limit": "limite",
}
# Apelidos que servem a mais de um parâmetro, dependendo da ferramenta.
APELIDOS_MULTI = {
    "query": ["texto", "consulta"], "text": ["texto", "fato", "consulta"], "search": ["texto", "consulta"],
    "busca": ["texto", "consulta"], "termo": ["texto", "consulta"],
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
            saida = await f.executar(f.normalizar_args(args))
        except ErroFerramenta as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001 - qualquer falha vira resposta, não derruba a conversa
            return False, f"Erro ao executar {nome}: {type(e).__name__}: {e}"
        if len(saida) > LIMITE_RESULTADO:
            saida = saida[:LIMITE_RESULTADO] + "\n[... resultado cortado]"
        return True, saida
