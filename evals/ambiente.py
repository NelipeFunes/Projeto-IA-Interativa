"""O conjunto COMPLETO de ferramentas do Vision, com aparelhos e serviços falsos, para avaliar o modelo como ele roda.

A avaliação antiga só registrava agenda, memória e Orbit (cerca de um terço do que o Vision tem), então o prompt dela
ficava bem menor que o de verdade e os tempos saíam otimistas. Aqui a ordem e os esquemas são os da `montagem.py`.

O que é falso: a TV (grava o que pediram), o Spotify, as luzes da Alexa, o PC (nada abre de verdade), o clima e a busca
na web (respostas prontas), as reuniões do Wispr (um servidor MCP vazio: só os esquemas contam). Nada sai da máquina.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
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


def _ddg(consulta: str, maximo: int) -> list[dict[str, str]]:
    return [{"title": "Motor do Fusca 1600: dados técnicos", "href": "https://www.exemplo.com.br/fusca",
             "body": "O Fusca 1600 usa um motor boxer de 4 cilindros refrigerado a ar, 1.584 cm³ e 60 cv."},
            {"title": "Fusca (Wikipedia)", "href": "https://pt.exemplo.org/Fusca", "body": "Produzido de 1959 a 1996 no Brasil."}]


def _noticias(tema: str, maximo: int) -> list[dict[str, str]]:
    return [{"title": "Banco Central mantém a Selic", "url": "https://noticias.exemplo.com/selic", "source": "Jornal X",
             "date": "2026-10-14T09:00:00+00:00", "body": "O Copom decidiu manter a taxa."}]


@dataclass
class Ambiente:
    registro: Registro
    atalhos: list
    host: HostMCP
    tv: TVFalsa
    alexa: AlexaFalsa
    spotify: SpotifyFalso
    timers: Timers
    abertos: list = field(default_factory=list)   # o que o PC "abriu" (programas e sites)
    agenda: Agenda | None = None
    web: Any = None


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
    controle_tv = TV(tv, apps=["YouTube", "Netflix", "Spotify", "Browser"], volume_maximo=50)
    registro.adicionar(*controle_tv.ferramentas())
    atalhos.append(controle_tv.atalho)
    abertos: list = []
    pc = PC(pasta / "logs", abrir=lambda x: abertos.append(("abrir", x)), navegador=lambda u: abertos.append(("site", u)),
            ler_volume=lambda: 40, mudar_volume=lambda *a, **k: None)
    pc._cache_apps.guardar("apps", [{"nome": "Spotify", "id": "spotify"}, {"nome": "Google Chrome", "id": "chrome"},
                                    {"nome": "Bloco de Notas", "id": "notepad"}, {"nome": "Calculadora", "id": "calc"}])
    registro.adicionar(*pc.ferramentas())
    atalhos.append(pc.atalho)
    timers = Timers(pasta / "timers.json")
    temporizador = Temporizador(timers, acoes={k: v[2] for k, v in ACOES_DE_TIMER.items()})
    registro.adicionar(*temporizador.ferramentas())
    atalhos.append(temporizador.atalho)
    registro.adicionar(*ferramentas_sistema.ferramentas())
    atalhos.append(ferramentas_sistema.atalho)
    spotify = SpotifyFalso()
    musica = Musica(spotify)
    pc.spotify = None
    registro.adicionar(*musica.ferramentas())
    atalhos.append(musica.atalho)
    alexa = AlexaFalsa()
    casa = Casa(alexa, confirmar=False)
    registro.adicionar(*casa.ferramentas())
    atalhos.append(casa.atalho)
    clima = modulo_clima.Clima(pasta / "cache", "Cidade Exemplo", transporte=_open_meteo())
    briefing = Briefing("Felipe", clima, agenda, timers)
    registro.adicionar(*briefing.ferramentas())
    atalhos.append(briefing.atalho)
    web = Web(pasta / "cache", chave="", buscar_ddg=_ddg)
    web._noticias_ddg = _noticias
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


__all__ = ["Ambiente", "criar", "host_completo", "SimpleNamespace", "Any"]
