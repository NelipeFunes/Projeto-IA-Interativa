"""Liga as peças: config → modelo, memória, MCPs, ferramentas → agente."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from vision.brain.agent import Agente
from vision.brain.llm import LLM, OllamaLLM
from vision.config import Config
from vision.memory.store import EmbedderOllama, Memorias
from vision.tools.agenda import Agenda
from vision.tools.base import Registro
from vision.tools.mcp_host import HostMCP
from vision.tools.memoria import FerramentasMemoria
from vision.timers import Timers

PERFIL_INICIAL = """# Perfil
<!-- Este texto entra em TODA conversa com o Vision. Mantenha curto (até ~25 linhas):
     quem você é, sua rotina e as regras que ele deve respeitar. Troque os exemplos pelos seus. -->

- Moro no Brasil (fuso de São Paulo). Estudo e trabalho.
- Trabalho fecha às 17h: não marcar trabalho depois disso.
- Sexta a partir das 19h é tempo livre.
- Antes de prova, reservo blocos de estudo com 1 a 3 semanas de antecedência.
"""


@dataclass
class Vision:
    cfg: Config
    agente: Agente
    registro: Registro
    host: HostMCP
    memorias: Memorias | None
    timers: Timers | None = None
    agenda: Agenda | None = None  # o núcleo atualiza o cache dela de tempos em tempos


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
    ao_disparar_timer=None,
) -> AsyncIterator[Vision]:
    """`ao_disparar_timer`: quem avisa o fim de um timer (o núcleo). Vem antes de recarregar os timers guardados:
    um que venceu com o núcleo desligado dispara já com ele (revisão do PR 20)."""
    garantir_perfil(cfg)
    host = host or HostMCP.da_config(cfg)
    if memorias is None and com_memoria:
        memorias = criar_memorias(cfg)
    registro = Registro()
    agenda = None
    if cfg.get("agenda.servidor") in host.conexoes:
        agenda = Agenda(cfg, host)
        registro.adicionar(*agenda.ferramentas())
    if memorias is not None:
        registro.adicionar(*FerramentasMemoria(memorias).ferramentas())
    if "orbit" in host.conexoes:
        from vision.tools.orbit import FerramentasOrbit

        registro.adicionar(*FerramentasOrbit(host).ferramentas())
    if "wispr" in host.conexoes:
        from vision.tools.reunioes import Reunioes

        registro.adicionar(*Reunioes(cfg, host).ferramentas())
    alexa = None
    atalhos = []
    timers = None
    if cfg.get("pc.ativo", True):
        from vision.tools.pc import PC
        from vision.tools.timer import Temporizador

        pc = PC(cfg.dados / "logs")
        registro.adicionar(*pc.ferramentas())
        atalhos.append(pc.atalho)
        timers = Timers(cfg.dados / "timers.json")
        timers.ao_disparar = ao_disparar_timer
        timers.iniciar()
        temporizador = Temporizador(timers)
        registro.adicionar(*temporizador.ferramentas())
        atalhos.append(temporizador.atalho)
        from vision.tools import sistema as ferramentas_sistema

        registro.adicionar(*ferramentas_sistema.ferramentas())
        atalhos.append(ferramentas_sistema.atalho)
    spotify = None
    if cfg.get("pc.ativo", True) and cfg.get("spotify.ativo", True):
        from vision import spotify as modulo_spotify

        if modulo_spotify.tem_login(cfg):  # sem `vision spotify-login`, as ferramentas de música nem aparecem
            from vision.tools.spotify import Musica

            spotify = modulo_spotify.Spotify(cfg)
            musica = Musica(spotify)
            registro.adicionar(*musica.ferramentas())
            atalhos.append(musica.atalho)
    if cfg.get("alexa.ativo", True):
        from vision import alexa as modulo_alexa

        if modulo_alexa.tem_login(cfg):  # sem `vision alexa-login`, as ferramentas de luz nem aparecem
            from vision.tools.casa import Casa

            alexa = modulo_alexa.Alexa(cfg)
            casa = Casa(alexa, confirmar=bool(cfg.get("alexa.confirmar_luzes", False)))
            registro.adicionar(*casa.ferramentas())
            atalhos.append(casa.atalho)
    if cfg.get("clima.ativo", True) or agenda is not None:
        from vision.clima import Clima
        from vision.tools.briefing import Briefing

        clima = Clima(cfg.dados / "cache", str(cfg.get("clima.cidade") or "")) if cfg.get("clima.ativo", True) else None
        briefing = Briefing(cfg.get("usuario.nome", "Felipe"), clima, agenda, timers)
        registro.adicionar(*briefing.ferramentas())
        atalhos.append(briefing.atalho)
    if cfg.get("web.ativo", True):
        from vision.tools.web import FerramentasWeb
        from vision.web import Web

        # Sem TAVILY_API_KEY no .env, busca pelo DuckDuckGo.
        registro.adicionar(*FerramentasWeb(Web(
            cfg.dados / "cache", validade_h=float(cfg.get("web.cache_horas", 6)),
            max_resultados=int(cfg.get("web.resultados", 5)), limite_cota=int(cfg.get("web.cota_mensal", 1000)),
        )).ferramentas())
    from vision.tools.ajuda import Ajuda

    ajuda = Ajuda(registro)  # por último: lista o que estiver ligado
    registro.adicionar(*ajuda.ferramentas())
    atalhos.append(ajuda.atalho)
    if cfg.get("protocolos.ativo", True):
        from vision.tools.protocolos import Protocolos

        # Por último: os passos de cada protocolo são conferidos contra as ferramentas que já existem.
        protocolos = Protocolos(cfg.dados / "protocolos.yaml", registro)
        registro.adicionar(*protocolos.ferramentas())
        atalhos.insert(0, protocolos.atalho)  # a frase do protocolo vale antes dos outros atalhos
    agente = Agente(
        llm or criar_llm(cfg),
        registro,
        memorias,
        nome_usuario=cfg.get("usuario.nome", "Felipe"),
        nome_assistente=cfg.get("assistente.nome", "Vision"),
        perfil=cfg.dados / "perfil.md",
        pasta_conversas=cfg.dados / "conversas",
        turnos_historico=int(cfg.get("conversa.turnos_no_historico", 6)),
        expira_min=float(cfg.get("conversa.sessao_expira_min", 10)),
        top_k=int(cfg.get("memoria.top_k", 3)),
        similaridade_minima=float(cfg.get("memoria.similaridade_minima", 0.25)),
        confirmacao=_modo_confirmacao(cfg),
    )
    agente.atalhos = atalhos
    async with host:
        try:
            yield Vision(cfg, agente, registro, host, memorias, timers, agenda)
        finally:
            if timers is not None:
                timers.fechar()
            if memorias is not None:
                memorias.fechar()
            if alexa is not None:
                await alexa.fechar()
            if spotify is not None:
                await spotify.fechar()


def _modo_confirmacao(cfg: Config) -> str:
    """`assistente.confirmacao`; a chave antiga `confirmar_acoes` (PR 14) ainda vale se a nova não existir."""
    modo = cfg.get("assistente.confirmacao")
    if modo is None and cfg.get("assistente.confirmar_acoes") is not None:
        modo = "todas" if cfg.get("assistente.confirmar_acoes") else "nenhuma"
    return str(modo or "todas")
