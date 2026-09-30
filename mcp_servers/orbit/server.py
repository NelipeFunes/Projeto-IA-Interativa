"""Servidor MCP do Orbit: finanças e tarefas do Felipe.

Roda por stdio (o Jarvis sobe sozinho) e também serve para qualquer cliente MCP.
Credenciais vêm do .env do projeto: ORBIT_TOKEN, ou ORBIT_EMAIL + ORBIT_PASSWORD.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).parent))
from orbit_api import ErroOrbit, OrbitAPI, campo, numero  # noqa: E402

FUSO = ZoneInfo("America/Sao_Paulo")


def _mes(mes: str | None) -> str:
    if mes:
        m = mes.strip()[:7]
        datetime.strptime(m, "%Y-%m")
        return m
    return datetime.now(FUSO).strftime("%Y-%m")


def _reais(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + "R$ " + s


def _eh_receita(t: dict[str, Any]) -> bool:
    tipo = str(campo(t, "tipo", "")).lower()
    return tipo in {"income", "receita", "entrada", "credit"}


def criar_servidor(api: OrbitAPI | None = None) -> MCPServer:
    api = api or OrbitAPI()
    srv = MCPServer("orbit", instructions="Finanças e tarefas do Felipe (app Orbit).")

    async def _seguro(coro) -> str:
        try:
            return await coro
        except ErroOrbit as e:
            raise ToolError(str(e)) from e

    @srv.tool(name="financas_resumo_mes")
    async def resumo_mes(mes: str | None = None) -> str:
        """Resumo do mês (AAAA-MM; padrão: mês atual): total gasto, receitas e gasto por categoria vs orçamento."""

        async def fazer() -> str:
            m = _mes(mes)
            trans = await api.transacoes(m)
            try:
                orc = await api.orcamentos(m)
            except ErroOrbit:
                orc = []
            gastos: dict[str, float] = defaultdict(float)
            receitas = 0.0
            for t in trans:
                v = abs(numero(campo(t, "valor", 0)))
                if _eh_receita(t):
                    receitas += v
                else:
                    gastos[str(campo(t, "categoria", "Sem categoria"))] += v
            orcado = {str(campo(o, "categoria", "?")): numero(campo(o, "orcado", 0)) for o in orc}
            linhas = [f"Mês {m}: {len(trans)} lançamento(s). Gasto total {_reais(sum(gastos.values()))}."]
            if receitas:
                linhas.append(f"Receitas lançadas: {_reais(receitas)}.")
            for cat, v in sorted(gastos.items(), key=lambda x: -x[1]):
                extra = f" de {_reais(orcado[cat])} orçados" if orcado.get(cat) else ""
                linhas.append(f"- {cat}: {_reais(v)}{extra}")
            for cat, v in orcado.items():
                if cat not in gastos and v:
                    linhas.append(f"- {cat}: nada gasto de {_reais(v)} orçados")
            return "\n".join(linhas)

        return await _seguro(fazer())

    @srv.tool(name="financas_lancamentos")
    async def lancamentos(mes: str | None = None, categoria: str | None = None, limite: int = 15) -> str:
        """Lista os lançamentos do mês (AAAA-MM), opcionalmente de uma categoria, do mais recente ao mais antigo."""

        async def fazer() -> str:
            m = _mes(mes)
            trans = await api.transacoes(m)
            if categoria:
                c = categoria.lower()
                trans = [t for t in trans if c in str(campo(t, "categoria", "")).lower()
                         or c in str(campo(t, "subcategoria", "")).lower()]
            trans.sort(key=lambda t: str(campo(t, "data", "")), reverse=True)
            if not trans:
                return f"Nenhum lançamento em {m}" + (f" na categoria {categoria}." if categoria else ".")
            linhas = [
                f"- {str(campo(t, 'data', ''))[:10]} {_reais(numero(campo(t, 'valor', 0)))} "
                f"{campo(t, 'descricao', '')} ({campo(t, 'categoria', 'sem categoria')})"
                for t in trans[: max(1, min(limite, 50))]
            ]
            return f"Lançamentos de {m}:\n" + "\n".join(linhas)

        return await _seguro(fazer())

    @srv.tool(name="financas_lancar")
    async def lancar(valor: float, descricao: str, categoria: str | None = None, data: str | None = None) -> str:
        """Lança um gasto no Orbit. valor em reais (positivo), data AAAA-MM-DD (padrão: hoje)."""

        async def fazer() -> str:
            dia = date.fromisoformat(data[:10]) if data else datetime.now(FUSO).date()
            await api.lancar(abs(float(valor)), descricao, categoria, dia, dia.strftime("%Y-%m"))
            return f"Lançado: {_reais(abs(float(valor)))} — {descricao}" + (f" ({categoria})" if categoria else "")

        return await _seguro(fazer())

    @srv.tool(name="financas_categorias")
    async def categorias() -> str:
        """Lista as categorias de gasto cadastradas no Orbit."""

        async def fazer() -> str:
            cats = await api.categorias()
            nomes = [str(campo(c, "descricao") or campo(c, "titulo") or campo(c, "id")) for c in cats]
            return "Categorias: " + (", ".join(nomes) if nomes else "nenhuma")

        return await _seguro(fazer())

    @srv.tool(name="tarefas_listar")
    async def tarefas_listar(incluir_concluidas: bool = False) -> str:
        """Lista as tarefas do Orbit (abertas, por padrão), com vencimento e prioridade."""

        async def fazer() -> str:
            ts = await api.tarefas()
            if not incluir_concluidas:
                ts = [t for t in ts if campo(t, "concluida") not in (True, "done", "completed")]
            if not ts:
                return "Nenhuma tarefa aberta."
            ts.sort(key=lambda t: str(campo(t, "vencimento", "9999")))
            linhas = []
            for t in ts:
                venc = campo(t, "vencimento")
                pri = campo(t, "prioridade")
                extra = ", ".join(x for x in [f"vence {str(venc)[:10]}" if venc else "", f"prioridade {pri}" if pri else ""] if x)
                linhas.append(f"- {campo(t, 'titulo', '(sem título)')}" + (f" ({extra})" if extra else ""))
            return "Tarefas:\n" + "\n".join(linhas)

        return await _seguro(fazer())

    @srv.tool(name="tarefas_criar")
    async def tarefas_criar(titulo: str, vencimento: str | None = None, prioridade: str | None = None) -> str:
        """Cria uma tarefa no Orbit. vencimento AAAA-MM-DD; prioridade: baixa, media ou alta."""

        async def fazer() -> str:
            mapa = {"baixa": "low", "media": "medium", "média": "medium", "alta": "high"}
            await api.criar_tarefa(titulo, vencimento, mapa.get((prioridade or "").lower(), prioridade))
            return f"Tarefa criada: {titulo}" + (f" (vence {vencimento})" if vencimento else "")

        return await _seguro(fazer())

    return srv


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    criar_servidor().run("stdio")
