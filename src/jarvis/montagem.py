"""Liga as peças: config → modelo, memória, MCPs, ferramentas → agente."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from jarvis.brain.agent import Agente
from jarvis.brain.llm import LLM, OllamaLLM
from jarvis.config import Config
from jarvis.memory.store import EmbedderOllama, Memorias
from jarvis.tools.agenda import Agenda
from jarvis.tools.base import Registro
from jarvis.tools.mcp_host import HostMCP
from jarvis.tools.memoria import FerramentasMemoria

PERFIL_INICIAL = """# Perfil
<!-- Este texto entra em TODA conversa com o Jarvis. Mantenha curto (até ~25 linhas):
     quem você é, sua rotina e as regras que ele deve respeitar. Troque os exemplos pelos seus. -->

- Moro no Brasil (fuso de São Paulo). Estudo e trabalho.
- Trabalho fecha às 17h: não marcar trabalho depois disso.
- Sexta a partir das 19h é tempo livre.
- Antes de prova, reservo blocos de estudo com 1 a 3 semanas de antecedência.
"""


@dataclass
class Jarvis:
    cfg: Config
    agente: Agente
    registro: Registro
    host: HostMCP
    memorias: Memorias | None


def criar_llm(cfg: Config, modelo: str | None = None) -> LLM:
    return OllamaLLM(
        modelo=modelo or cfg.get("modelo.nome"),
        host=cfg.get("modelo.host", "http://127.0.0.1:11434"),
        pensar=bool(cfg.get("modelo.pensar", False)),
        contexto=int(cfg.get("modelo.contexto", 8192)),
        temperatura=float(cfg.get("modelo.temperatura", 0.3)),
        manter=str(cfg.get("modelo.manter_carregado", "30m")),
    )


def criar_memorias(cfg: Config) -> Memorias:
    embedder = EmbedderOllama(
        cfg.get("memoria.modelo_embedding", "embeddinggemma"),
        cfg.get("modelo.host", "http://127.0.0.1:11434"),
        na_cpu=bool(cfg.get("memoria.embedding_na_cpu", True)),
    )
    return Memorias(cfg.dados / "memoria.db", embedder, float(cfg.get("memoria.similaridade_duplicata", 0.9)))


def garantir_perfil(cfg: Config) -> None:
    perfil = cfg.dados / "perfil.md"
    if not perfil.exists():
        perfil.write_text(PERFIL_INICIAL, encoding="utf-8")


@asynccontextmanager
async def montar(
    cfg: Config,
    *,
    llm: LLM | None = None,
    host: HostMCP | None = None,
    memorias: Memorias | None = None,
    com_memoria: bool = True,
) -> AsyncIterator[Jarvis]:
    garantir_perfil(cfg)
    host = host or HostMCP.da_config(cfg)
    if memorias is None and com_memoria:
        memorias = criar_memorias(cfg)
    registro = Registro()
    if cfg.get("agenda.servidor") in host.conexoes:
        registro.adicionar(*Agenda(cfg, host).ferramentas())
    if memorias is not None:
        registro.adicionar(*FerramentasMemoria(memorias).ferramentas())
    if "orbit" in host.conexoes:
        from jarvis.tools.orbit import FerramentasOrbit

        registro.adicionar(*FerramentasOrbit(host).ferramentas())
    agente = Agente(
        llm or criar_llm(cfg),
        registro,
        memorias,
        nome_usuario=cfg.get("usuario.nome", "Felipe"),
        perfil=cfg.dados / "perfil.md",
        pasta_conversas=cfg.dados / "conversas",
        turnos_historico=int(cfg.get("conversa.turnos_no_historico", 6)),
        expira_min=float(cfg.get("conversa.sessao_expira_min", 10)),
        top_k=int(cfg.get("memoria.top_k", 3)),
        similaridade_minima=float(cfg.get("memoria.similaridade_minima", 0.25)),
    )
    async with host:
        try:
            yield Jarvis(cfg, agente, registro, host, memorias)
        finally:
            if memorias is not None:
                memorias.fechar()
