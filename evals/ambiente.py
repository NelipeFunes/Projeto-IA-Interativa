"""O conjunto COMPLETO de ferramentas do Vision, com aparelhos e serviços falsos, para avaliar o modelo como ele roda.

A avaliação antiga só registrava agenda, memória e Orbit (cerca de um terço do que o Vision tem), então o prompt dela
ficava bem menor que o de verdade e os tempos saíam otimistas. Aqui a ordem e os esquemas são os da `montagem.py`
(`tests/test_orcamento_prompt.py` confere que nenhuma ferramenta da montagem falta aqui).

ISOLAMENTO (revisão do PR 60): nada neste ambiente age no PC nem na internet de verdade. A TV grava o que pediram; o
Spotify e a Alexa são falsos; no PC, abrir programa, site, volume (um falso com nível e mudo), travar, fechar programa,
tecla de mídia, energia e comando só registram em `abertos`, e o diagnóstico do PC devolve números fixos; a busca de vídeo da TV, a busca na web, as notícias e a leitura de páginas usam
respostas prontas (transporte HTTP simulado e DNS falso). Um teste roda as ferramentas de ação com `ctypes.windll`,
`subprocess` e `socket` trocados por funções que levantam erro.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from vision import tempo
from vision.timers import Timers
from vision.tools.agenda import Agenda
from vision.tools.base import Registro
from vision.tools.mcp_host import ConexaoMCP, HostMCP
from vision.tools.memoria import FerramentasMemoria

LUZES = [{"nome": "Luz do Quarto", "entity_id": "e1"}, {"nome": "Abajur Sala", "entity_id": "e2"},
         {"nome": "Luz da Cozinha", "entity_id": "e3"}]
APPS = ["YouTube", "Netflix", "Spotify", "Browser"]
VOLUME_MAXIMO_TV = 50


class TVFalsa:
    """Grava o que pediram; responde como uma TV ligada e pareada."""

    def __init__(self):
        self.sessao = {"token": "x", "session_id": "1"}
        self.vol, self.mutada = 7, False
        self.feitas: list[tuple] = []
        self.apps = {"YouTube": "stopped", "Netflix": "stopped", "Spotify": "stopped", "Browser": "stopped"}

    async def acessivel(self): return True
    async def teclas(self, teclas): self.feitas.append(("teclas", list(teclas)))
    async def volume(self): return self.vol
    async def definir_volume(self, n): self.vol = n; self.feitas.append(("volume", n)); return n
    async def mudo(self): return self.mutada
    async def definir_mudo(self, ligado): self.mutada = ligado; self.feitas.append(("mudo", ligado))
    async def estado_app(self, nome): return self.apps.get(nome)
    async def abrir_app(self, nome): self.feitas.append(("app", nome))
    async def youtube(self, video_id): self.feitas.append(("youtube", video_id)); return True
    async def tocar_midia(self, url): self.feitas.append(("midia", url))
    async def fechar(self): pass


class AlexaFalsa:
    def __init__(self):
        self.feitas: list[tuple] = []

    def luzes(self): return LUZES
    async def mudar_luz(self, entity_id, ligar, brilho=None): self.feitas.append((entity_id, ligar, brilho))
    async def fechar(self): pass


class SpotifyFalso:
    def __init__(self):
        self.feitas: list[tuple] = []

    async def tocar(self, busca, tipo="musica", onde=None):
        self.feitas.append(("tocar", busca, tipo, onde))
        return f"{busca} no Spotify"

    async def controlar(self, acao): self.feitas.append(("controlar", acao)); return f"Certo: {acao}."
    async def tocando(self): return "Bohemian Rhapsody, de Queen"
    async def fechar(self): pass


def _open_meteo():
    hoje = tempo.agora().date()

    def responder(req: httpx.Request) -> httpx.Response:
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "Cidade Exemplo", "latitude": 10.0, "longitude": 20.0}]})
        return httpx.Response(200, json={
            "current": {"temperature_2m": 18.4, "apparent_temperature": 14.9, "weather_code": 2,
                        "relative_humidity_2m": 80, "wind_speed_10m": 9.0},
            "daily": {"time": [(hoje + timedelta(days=i)).isoformat() for i in range(7)],
                      "weather_code": [2, 63, 0, 0, 1, 2, 3], "temperature_2m_max": [27.2, 22.0, 30.0, 29, 28, 27, 26],
                      "temperature_2m_min": [13.6, 15.0, 16.0, 15, 14, 14, 13],
                      "precipitation_probability_max": [10, 80, 0, 0, 5, 10, 20]}})

    return httpx.MockTransport(responder)


def _web_falsa() -> httpx.MockTransport:
    """Qualquer página lida pela web_ler: um texto pronto, sem rede."""
    return httpx.MockTransport(lambda req: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"},
        text="<html><body><article><p>O Fusca 1600 tem motor boxer de 4 cilindros refrigerado a ar.</p></article></body></html>"))


def _ddg(consulta: str, maximo: int) -> list[dict[str, str]]:
    return [{"title": "Motor do Fusca 1600: dados técnicos", "href": "https://www.exemplo.com.br/fusca",
             "body": "O Fusca 1600 usa um motor boxer de 4 cilindros refrigerado a ar, 1.584 cm³ e 60 cv. [FUSCA-FALSO-1584]"},
            {"title": "Fusca (Wikipedia)", "href": "https://pt.exemplo.org/Fusca", "body": "Produzido de 1959 a 1996 no Brasil."}]


def _noticias(tema: str, maximo: int) -> list[dict[str, str]]:
    return [{"title": "Banco Central mantém a Selic", "url": "https://noticias.exemplo.com/selic", "source": "Jornal X",
             "date": "2026-10-14T09:00:00+00:00", "body": "O Copom decidiu manter a taxa. [NOTICIA-FALSA-77]"}]


def _videos(consulta: str, maximo: int = 10) -> list[dict[str, Any]]:
    """No lugar da busca no YouTube da TV: um clipe de 4 minutos, sem rede."""
    return [{"content": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": consulta, "publisher": "YouTube",
             "uploader": "Canal Oficial", "duration": "4:00", "statistics": {"viewCount": 1_000_000}}]


class VolumeFalso:
    """O volume do Windows: guarda o nível e o mudo e aplica a mesma conta do volume de verdade (`nivel_alvo`)."""

    def __init__(self, abertos: list, nivel: int = 40):
        self.nivel, self.mudo, self.abertos = nivel, False, abertos

    def ler(self) -> tuple[int, bool]:
        return self.nivel, self.mudo

    def mudar(self, acao: str, nivel: float | None = None) -> int:
        from vision.tools.pc import nivel_alvo

        self.abertos.append(("volume", acao, nivel))
        if acao == "mudo":
            self.mudo = True
        elif acao == "som":
            self.mudo = False
        else:
            self.nivel = round(nivel_alvo(acao, self.nivel / 100, nivel) * 100)
        return self.nivel


def _estado_do_pc():
    """O diagnóstico do PC com números fixos: a avaliação não lê o computador de quem a roda."""
    from vision import sistema

    return sistema.Estado(cpu_pct=23.0, memoria_pct=41.0, memoria_usada_gb=13.1, memoria_total_gb=32.0, disco_livre_gb=420.0,
                          disco_pct=55.0, ligado_ha_s=3 * 3600 + 20 * 60,
                          placas=[sistema.Placa("GPU de teste", 7, 52, 1200, 8192)])


@dataclass
class Ambiente:
    registro: Registro
    atalhos: list
    host: HostMCP
    tv: TVFalsa
    alexa: AlexaFalsa
    spotify: SpotifyFalso
    timers: Timers
    abertos: list = field(default_factory=list)   # o que o PC "fez": programas, sites, travar, fechar, mídia...
    agenda: Agenda | None = None
    web: Any = None

    def fechar(self) -> None:
        """Os timers criados nos casos ficam tarefas asyncio acordando a cada 30 s: fecha ao fim de cada caso."""
        self.timers.fechar()


def _pc_isolado(pc, abertos: list) -> None:
    """Troca no PC tudo que age de verdade (tela, processos, tecla de mídia, energia, PowerShell) por registro."""

    def registra(nome: str, resposta):
        async def feito(args=None):
            abertos.append((nome, args))
            return resposta(args) if callable(resposta) else resposta

        return feito

    pc.travar = registra("travar", "Travei o computador.")
    pc.fechar_programa = registra("fechar", lambda a: f"Fechei {(a or {}).get('nome', 'o programa')}.")
    pc.controlar_midia = registra("midia", lambda a: f"Feito: {(a or {}).get('acao', 'mídia')}.")
    pc.energia = registra("energia", lambda a: f"Vou {(a or {}).get('acao', 'desligar')} o computador em 60 s.")
    pc.cancelar_energia = registra("cancelar_energia", "Cancelei o desligamento.")
    pc.rodar_comando = registra("comando", "Comando executado.")

    async def sem_processo(*cmd, prazo=20):  # qualquer outro PowerShell/taskkill que ainda passe por aqui
        abertos.append(("processo", cmd))
        return 0, ""

    pc._rodar = sem_processo


async def _atalho_do_sistema(frase: str):
    """O atalho "status do sistema" também lê o PC de verdade: aqui responde com o estado fixo."""
    from vision import sistema
    from vision.tools.sistema import pede_status

    if not pede_status(frase):
        return None
    return "status_do_sistema", {}, sistema.relatorio(_estado_do_pc()), True


def criar(cfg, host: HostMCP, memorias, pasta: Path) -> Ambiente:
    """Mesma ordem da `montagem.montar`: o prompt sai com os mesmos esquemas que o Vision de verdade manda."""
    from vision import clima as modulo_clima
    from vision.tools import sistema as ferramentas_sistema
    from vision.tools.ajuda import Ajuda
    from vision.tools.briefing import Briefing
    from vision.tools.casa import Casa
    from vision.tools.orbit import FerramentasOrbit
    from vision.tools.pc import PC
    from vision.tools.protocolos import Protocolos
    from vision.tools.reunioes import Reunioes
    from vision.tools.spotify import Musica
    from vision.tools.timer import Temporizador
    from vision.tools.tv import ACOES_DE_TIMER, TV
    from vision.tools.web import FerramentasWeb
    from vision.web import Web

    registro = Registro()
    atalhos: list = []
    agenda = Agenda(cfg, host)
    registro.adicionar(*agenda.ferramentas(), *FerramentasMemoria(memorias).ferramentas(),
                       *FerramentasOrbit(host).ferramentas(), *Reunioes(cfg, host).ferramentas())
    tv = TVFalsa()
    controle_tv = TV(tv, apps=list(cfg.get("tv.apps") or APPS), volume_maximo=int(cfg.get("tv.volume_maximo", VOLUME_MAXIMO_TV)),
                     buscar=_videos)
    registro.adicionar(*controle_tv.ferramentas())
    atalhos.append(controle_tv.atalho)
    abertos: list = []
    volume = VolumeFalso(abertos)
    pc = PC(pasta / "logs", abrir=lambda x: abertos.append(("abrir", x)), navegador=lambda u: abertos.append(("site", u)),
            ler_volume=volume.ler, mudar_volume=volume.mudar)
    _pc_isolado(pc, abertos)
    pc._cache_apps.guardar("apps", [{"nome": "Spotify", "id": "spotify"}, {"nome": "Google Chrome", "id": "chrome"},
                                    {"nome": "Bloco de Notas", "id": "notepad"}, {"nome": "Calculadora", "id": "calc"}])
    registro.adicionar(*pc.ferramentas())
    atalhos.append(pc.atalho)
    timers = Timers(pasta / "timers.json")
    # O relógio é pedido na hora, não fixado na importação: `tempo.agora` é trocado pelo relógio sintético da avaliação.
    temporizador = Temporizador(timers, relogio=lambda: tempo.agora(), acoes={k: v[2] for k, v in ACOES_DE_TIMER.items()})
    registro.adicionar(*temporizador.ferramentas())
    atalhos.append(temporizador.atalho)
    async def status_fixo(_args=None) -> str:
        from vision import sistema

        return sistema.relatorio(_estado_do_pc())

    registro.adicionar(*[replace(t, executar=status_fixo) for t in ferramentas_sistema.ferramentas()])
    atalhos.append(_atalho_do_sistema)
    spotify = SpotifyFalso()
    musica = Musica(spotify)
    registro.adicionar(*musica.ferramentas())
    atalhos.append(musica.atalho)
    alexa = AlexaFalsa()
    casa = Casa(alexa, confirmar=bool(cfg.get("alexa.confirmar_luzes", False)))
    registro.adicionar(*casa.ferramentas())
    atalhos.append(casa.atalho)
    clima = modulo_clima.Clima(pasta / "cache", "Cidade Exemplo", transporte=_open_meteo())
    briefing = Briefing(cfg.get("usuario.nome", "Felipe"), clima, agenda, timers)
    registro.adicionar(*briefing.ferramentas())
    atalhos.append(briefing.atalho)
    web = Web(pasta / "cache", chave="", buscar_ddg=_ddg, transporte=_web_falsa())
    web._noticias_ddg = _noticias
    web._publico = lambda url: True  # sem DNS: os endereços de teste não existem
    registro.adicionar(*FerramentasWeb(web).ferramentas())
    ajuda = Ajuda(registro)
    registro.adicionar(*ajuda.ferramentas())
    atalhos.append(ajuda.atalho)
    protocolos = Protocolos(pasta / "protocolos.yaml", registro)
    registro.adicionar(*protocolos.ferramentas())
    atalhos.insert(0, protocolos.atalho)
    return Ambiente(registro, atalhos, host, tv, alexa, spotify, timers, abertos, agenda, web)


def host_completo(agenda_srv, orbit_srv) -> HostMCP:
    """Agenda e Orbit falsos de verdade; o Wispr é um servidor vazio (só os esquemas das ferramentas contam)."""
    return HostMCP({
        "google-calendar": ConexaoMCP("google-calendar", agenda_srv, 20),
        "orbit": ConexaoMCP("orbit", orbit_srv, 20),
        "wispr": ConexaoMCP("wispr", MCPServer("wispr-falso"), 20),
    })
