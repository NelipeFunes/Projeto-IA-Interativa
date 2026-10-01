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
Previa = Callable[[dict[str, Any]], "dict[str, Any] | None"]

LIMITE_RESULTADO = 4000


class ErroFerramenta(Exception):
    """Erro que deve ser contado ao Felipe (ex.: agenda sem login)."""


class ComDados(str):
    """Texto para o modelo que carrega junto dados para a tela (ex.: o evento criado).

    É um `str` de verdade: quem só quer o texto (testes, diagnóstico) nem percebe a diferença.
    """

    dados: Any

    def __new__(cls, texto: str, dados: Any = None) -> ComDados:
        obj = super().__new__(cls, texto)
        obj.dados = dados
        return obj


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
    # Para escrita: como a tela mostra a ação pendente (ex.: o cartão do evento), sem chamar nada externo.
    previa: Previa | None = None
    # Escrita que roda sem "Confirma?" (luzes, a pedido do Felipe em 01/10). Mesmo assim pergunta se o turno
    # (ou o anterior) leu conteúdo de fora: um texto de reunião não pode apagar as luzes sozinho.
    confirmar: bool = True
    # Leitura cujo resultado tem texto de terceiros (convites da agenda, transcrições, notas).
    conteudo_externo: bool = False
    # Não é escrita "de fora" (guardar memória), mas grava algo que volta em todo prompt: depois de ler texto de
    # terceiros, vira confirmação, para uma transcrição não plantar uma "memória" com instruções.
    confirmar_se_externo: bool = False

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
        ok, saida, _ = await self.rodar_com_dados(nome, args)
        return ok, saida

    async def rodar_com_dados(self, nome: str, args: dict[str, Any]) -> tuple[bool, str, Any]:
        """Como `rodar`, mais os dados para a tela (None se a ferramenta não tiver)."""
        f = self.ferramentas.get(nome)
        if f is None:
            return False, f"Ferramenta '{nome}' não existe. Use só as ferramentas listadas.", None
        try:
            saida = await f.executar(f.normalizar_args(args))
        except ErroFerramenta as e:
            return False, str(e), None
        except Exception as e:  # noqa: BLE001 - qualquer falha vira resposta, não derruba a conversa
            return False, f"Erro ao executar {nome}: {type(e).__name__}: {e}", None
        dados = saida.dados if isinstance(saida, ComDados) else None
        saida = str(saida)
        if len(saida) > LIMITE_RESULTADO:
            saida = saida[:LIMITE_RESULTADO] + "\n[... resultado cortado]"
        return True, saida, dados
