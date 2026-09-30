"""MCP do Orbit falso (mesmos nomes de ferramenta do mcp_servers/orbit), para avaliações."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def criar_servidor() -> MCPServer:
    srv = MCPServer("orbit-falso")
    srv.lancados = []  # type: ignore[attr-defined]
    srv.tarefas = [  # type: ignore[attr-defined]
        {"titulo": "Pagar IPVA", "vencimento": "2026-10-10", "prioridade": "alta"},
        {"titulo": "Renovar matrícula da faculdade", "vencimento": "2026-10-20", "prioridade": "media"},
    ]

    @srv.tool(name="financas_resumo_mes")
    def resumo(mes: str | None = None) -> str:
        return (f"Mês {mes or 'atual'}: 14 lançamento(s). Gasto total R$ 1.482,90.\n"
                "- Alimentação: R$ 862,90 de R$ 900,00 orçados\n- Pessoal & Lazer: R$ 420,00 de R$ 500,00 orçados\n"
                "- Saúde: R$ 200,00 de R$ 250,00 orçados")

    @srv.tool(name="financas_lancamentos")
    def lancamentos(mes: str | None = None, categoria: str | None = None, limite: int = 15) -> str:
        linhas = [
            "- 2026-09-27 R$ 58,90 iFood hambúrguer (Alimentação)",
            "- 2026-09-20 R$ 120,00 Gasolina Civic (Pessoal & Lazer)",
            "- 2026-09-15 R$ 64,00 iFood japonês (Alimentação)",
            "- 2026-09-05 R$ 610,00 Mercado do mês (Alimentação)",
        ]
        if categoria:
            linhas = [x for x in linhas if categoria.lower() in x.lower()] or ["(nada nessa categoria)"]
        return "Lançamentos:\n" + "\n".join(linhas)

    @srv.tool(name="financas_lancar")
    def lancar(valor: float, descricao: str, categoria: str | None = None, data: str | None = None) -> str:
        srv.lancados.append({"valor": valor, "descricao": descricao, "categoria": categoria, "data": data})  # type: ignore[attr-defined]
        return f"Lançado: R$ {valor:.2f} — {descricao}"

    @srv.tool(name="tarefas_listar")
    def tarefas_listar(incluir_concluidas: bool = False) -> str:
        return "Tarefas:\n" + "\n".join(
            f"- {t['titulo']} (vence {t['vencimento']}, prioridade {t['prioridade']})" for t in srv.tarefas  # type: ignore[attr-defined]
        )

    @srv.tool(name="tarefas_criar")
    def tarefas_criar(titulo: str, vencimento: str | None = None, prioridade: str | None = None) -> str:
        srv.tarefas.append({"titulo": titulo, "vencimento": vencimento, "prioridade": prioridade})  # type: ignore[attr-defined]
        return f"Tarefa criada: {titulo}"

    return srv
