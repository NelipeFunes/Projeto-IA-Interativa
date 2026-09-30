"""Ferramentas de finanças e tarefas, por cima do MCP do Orbit (mcp_servers/orbit)."""

from __future__ import annotations

from typing import Any

from vision.tools.base import ErroFerramenta, Ferramenta, esquema, numero, texto
from vision.tools.mcp_host import HostMCP


def _reais(v: Any) -> str:
    try:
        f = abs(float(str(v).replace(",", ".")))
    except ValueError as e:
        raise ErroFerramenta(f"Valor inválido: {v}") from e
    return "R$ " + f"{f:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class FerramentasOrbit:
    def __init__(self, host: HostMCP, servidor: str = "orbit"):
        self.host = host
        self.servidor = servidor

    def _chamar(self, ferramenta: str):
        async def executar(args: dict[str, Any]) -> str:
            limpos = {k: v for k, v in (args or {}).items() if v not in (None, "")}
            r = await self.host.chamar(self.servidor, ferramenta, limpos)
            if not r.ok:
                raise ErroFerramenta(f"Orbit: {r.texto[:300]}")
            return r.texto

        return executar

    async def descrever_lancar(self, args: dict[str, Any]) -> str:
        if not args.get("descricao"):
            raise ErroFerramenta("Informe a descrição do gasto.")
        cat = f" em {args['categoria']}" if args.get("categoria") else ""
        quando = f" no dia {args['data']}" if args.get("data") else ""
        return f"Vou lançar {_reais(args.get('valor', 0))} de {args['descricao']}{cat}{quando}."

    async def descrever_tarefa(self, args: dict[str, Any]) -> str:
        if not args.get("titulo"):
            raise ErroFerramenta("Informe o título da tarefa.")
        venc = f" para {args['vencimento']}" if args.get("vencimento") else ""
        return f"Vou adicionar a tarefa '{args['titulo']}'{venc}."

    def ferramentas(self) -> list[Ferramenta]:
        mes = texto("Mês no formato AAAA-MM (padrão: mês atual)")
        return [
            Ferramenta(
                "financas_resumo_mes",
                "Resumo das finanças do Felipe no mês: quanto gastou no total e por categoria, e o orçamento.",
                esquema([], mes=mes),
                self._chamar("financas_resumo_mes"),
                grupo="financas",
            ),
            Ferramenta(
                "financas_lancamentos",
                "Lista os gastos do mês, opcionalmente de uma categoria (ex.: iFood, Mercado).",
                esquema([], mes=mes, categoria=texto("Categoria ou subcategoria, opcional")),
                self._chamar("financas_lancamentos"),
                grupo="financas",
            ),
            Ferramenta(
                "financas_lancar",
                "Lança um gasto no Orbit (ex.: 'gastei 50 no iFood').",
                esquema(
                    ["valor", "descricao"],
                    valor=numero("Valor em reais, positivo"),
                    descricao=texto("O que foi"),
                    categoria=texto("Categoria, opcional (ex.: Alimentação)"),
                    data=texto("AAAA-MM-DD, opcional (padrão: hoje)"),
                ),
                self._chamar("financas_lancar"),
                escrita=True,
                descrever=self.descrever_lancar,
                grupo="financas",
            ),
            Ferramenta(
                "tarefas_listar",
                "Lista as tarefas abertas do Felipe no Orbit.",
                esquema([]),
                self._chamar("tarefas_listar"),
                grupo="tarefas",
            ),
            Ferramenta(
                "tarefas_criar",
                "Cria uma tarefa (to-do) no Orbit.",
                esquema(
                    ["titulo"],
                    titulo=texto("O que fazer"),
                    vencimento=texto("AAAA-MM-DD, opcional"),
                    prioridade={"type": "string", "enum": ["baixa", "media", "alta"]},
                ),
                self._chamar("tarefas_criar"),
                escrita=True,
                descrever=self.descrever_tarefa,
                grupo="tarefas",
            ),
        ]
