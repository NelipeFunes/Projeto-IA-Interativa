"""TV da sala pela rede (vision/tv.py faz a conversa com a TV).

O modelo só escolhe entre ações fixas (um enum de teclas, um número de volume ou de canal, um app da lista do
config): nada de texto livre para a TV e nenhum endereço vindo da conversa. Tudo é escrita que roda sem
"Confirma?" (como as luzes), mas pergunta se a conversa acabou de ler conteúdo de fora: uma página ou um
e-mail não pode desligar a TV sozinho. Tocar um link de mídia pergunta SEMPRE: o link pode ter vindo da web.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from vision.tools.base import ErroFerramenta, Ferramenta, esquema, numero, texto
from vision.tools.pc import normalizar
from vision.tv import VIDEO_ID, SemPareamento, TVInacessivel

# Ação → tecla do controle (protocolo criptografado). Volume e mudo vão pelo UPnP (valor exato).
TECLAS = {
    "desligar": "KEY_POWER", "canal_mais": "KEY_CHUP", "canal_menos": "KEY_CHDOWN", "fonte": "KEY_SOURCE",
    "hdmi": "KEY_HDMI", "cima": "KEY_UP", "baixo": "KEY_DOWN", "esquerda": "KEY_LEFT", "direita": "KEY_RIGHT",
    "ok": "KEY_ENTER", "voltar": "KEY_RETURN", "home": "KEY_HOME", "menu": "KEY_MENU", "sair": "KEY_EXIT",
    "play": "KEY_PLAY", "pausa": "KEY_PAUSE",
}
PELO_SOM = ("volume_mais", "volume_menos", "mudo", "desmutar")
ACOES = (*PELO_SOM, *TECLAS)
REPETIVEIS = {"volume_mais", "volume_menos", "canal_mais", "canal_menos", "cima", "baixo", "esquerda", "direita",
              "voltar"}
MAXIMO_VEZES = 20
FEITO = {
    "desligar": "Desliguei a TV.", "mudo": "TV no mudo.", "desmutar": "Som da TV de volta.",
    "canal_mais": "Próximo canal.", "canal_menos": "Canal anterior.", "fonte": "Abri a lista de fontes.",
    "hdmi": "Troquei a entrada HDMI.", "ok": "OK.", "voltar": "Voltei.", "home": "Abri o menu inicial da TV.",
    "menu": "Abri o menu da TV.", "sair": "Saí.", "play": "Play.", "pausa": "Pausado.",
}
# O que um timer pode fazer no fim ("desliga a TV em 30 minutos"): nome → (ferramenta, args, como falar).
# Lista fechada: um timer criado depois de ler texto de fora não faz nada além disso (e pergunta antes).
ACOES_DE_TIMER = {
    "desligar_tv": ("tv_controle", {"acao": "desligar"}, "desligar a TV"),
    "mutar_tv": ("tv_controle", {"acao": "mudo"}, "pôr a TV no mudo"),
}
SEM_LIGAR = "Não consigo ligar a TV pela rede (ela desliga o Wi-Fi junto): ligue pelo controle."
DURACAO_MINIMA_S = 90  # menos que isso é corte, short ou TikTok, não a música


def _segundos(duracao: Any) -> int:
    """"4:33" → 273; "1:02:03" → 3723; ilegível → 0."""
    partes = str(duracao or "").strip().split(":")
    if not partes or not all(p.isdigit() for p in partes):
        return 0
    total = 0
    for p in partes:
        total = total * 60 + int(p)
    return total


def id_do_youtube(url: Any) -> str | None:
    """O ID de um link do YouTube (watch?v=, youtu.be/, /shorts/, /embed/), ou None."""
    try:
        p = urlparse(str(url).strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    candidato = None
    if host in ("youtu.be", "www.youtu.be"):
        candidato = p.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if p.path == "/watch":
            candidato = (parse_qs(p.query).get("v") or [""])[0]
        elif m := re.match(r"^/(?:shorts|embed|live)/([^/?#]+)", p.path):
            candidato = m.group(1)
    return candidato if candidato and VIDEO_ID.match(candidato) else None


def escolher_video(resultados: list[dict[str, Any]]) -> dict[str, Any] | None:
    """O vídeo "mais certo" da busca: do YouTube, com pelo menos 1 min 30 s, o mais visto (o clipe oficial
    costuma ganhar de longe). Devolve {"id", "titulo", "canal", "duracao"} ou None."""
    candidatos = []
    for r in resultados:
        vid = id_do_youtube(r.get("content") or r.get("url") or "")
        if vid is None:
            continue
        duracao = _segundos(r.get("duration"))
        views = (r.get("statistics") or {}).get("viewCount") or 0
        try:
            views = int(views)
        except (TypeError, ValueError):
            views = 0
        candidatos.append((duracao >= DURACAO_MINIMA_S, views, {
            "id": vid, "titulo": str(r.get("title") or "")[:70].strip(),
            "canal": str(r.get("uploader") or r.get("publisher") or "")[:40].strip(), "duracao": duracao}))
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidatos[0][2]


def buscar_videos(consulta: str, maximo: int = 10) -> list[dict[str, Any]]:
    """Pelo DuckDuckGo (o mesmo `ddgs` da busca na web), sem chave de API."""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        return list(DDGS().videos(f"{consulta} youtube", safesearch="moderate", max_results=maximo) or [])
    except DDGSException as e:
        if "no results" in str(e).lower():
            return []
        raise


class TV:
    def __init__(self, tv, apps: list[str] | None = None, volume_maximo: int = 50, buscar=buscar_videos):
        self.tv = tv
        self.apps = [a for a in (apps or ["YouTube", "Netflix"]) if re.fullmatch(r"[A-Za-z0-9._-]{1,40}", str(a))]
        self.volume_maximo = max(1, min(100, int(volume_maximo)))
        self.buscar = buscar

    # ------------------------------------------------------------------ apoio

    async def _rodar(self, corrotina):
        try:
            return await corrotina
        except TVInacessivel as e:
            raise ErroFerramenta(f"{e} {SEM_LIGAR}" if "desligada" in str(e) else str(e)) from e
        except SemPareamento as e:
            raise ErroFerramenta(str(e)) from e
        except ValueError as e:
            raise ErroFerramenta(f"TV: {e}.") from e
        except RuntimeError as e:
            raise ErroFerramenta(f"A TV não fez: {e}.") from e

    @staticmethod
    def _acao(args: dict[str, Any]) -> str:
        acao = normalizar(str(args.get("acao") or "")).replace(" ", "_")
        if acao in ("ligar", "liga"):
            raise ErroFerramenta(SEM_LIGAR)
        if acao not in ACOES:
            raise ErroFerramenta("Ação de TV desconhecida. Use uma destas: " + ", ".join(ACOES) + ".")
        return acao

    @staticmethod
    def _vezes(args: dict[str, Any], acao: str) -> int:
        bruto = args.get("vezes")
        if bruto in (None, ""):
            return 1
        try:
            vezes = int(float(str(bruto)))
        except (ValueError, OverflowError) as e:
            raise ErroFerramenta(f"'vezes' é um número de 1 a {MAXIMO_VEZES}.") from e
        if not 1 <= vezes <= MAXIMO_VEZES:
            raise ErroFerramenta(f"'vezes' é um número de 1 a {MAXIMO_VEZES}.")
        return vezes if acao in REPETIVEIS else 1

    # ------------------------------------------------------------------ ferramentas

    async def controle(self, args: dict[str, Any]) -> str:
        acao = self._acao(args)
        vezes = self._vezes(args, acao)
        if acao in ("volume_mais", "volume_menos"):
            atual = await self._rodar(self.tv.volume())
            alvo = atual + vezes if acao == "volume_mais" else atual - vezes
            alvo = max(0, min(self.volume_maximo, alvo))
            await self._rodar(self.tv.definir_volume(alvo))
            limite = " (é o máximo que deixo pela voz)" if acao == "volume_mais" and alvo == self.volume_maximo else ""
            return f"Volume da TV em {alvo}{limite}."
        if acao in ("mudo", "desmutar"):
            await self._rodar(self.tv.definir_mudo(acao == "mudo"))
            return FEITO[acao]
        await self._rodar(self.tv.teclas([TECLAS[acao]] * vezes))
        feito = FEITO.get(acao, f"Apertei '{acao}' no controle da TV.")
        return feito if vezes == 1 else f"{feito.rstrip('.')} ({vezes} vezes)."

    async def descrever_controle(self, args: dict[str, Any]) -> str:
        acao = self._acao(args)
        vezes = self._vezes(args, acao)
        nomes = {"desligar": "desligar a TV", "volume_mais": "aumentar o volume da TV",
                 "volume_menos": "abaixar o volume da TV", "mudo": "deixar a TV no mudo",
                 "desmutar": "tirar a TV do mudo"}
        frase = nomes.get(acao, f"apertar '{acao.replace('_', ' ')}' no controle da TV")
        return f"Vou {frase}" + (f" {vezes} vezes." if vezes > 1 else ".")

    def _nivel(self, args: dict[str, Any]) -> int:
        try:
            nivel = int(float(str(args.get("nivel")).replace("%", "")))
        except (ValueError, OverflowError) as e:
            raise ErroFerramenta(f"Volume da TV de 0 a {self.volume_maximo}.") from e
        if not 0 <= nivel <= 100:
            raise ErroFerramenta(f"Volume da TV de 0 a {self.volume_maximo}.")
        return nivel

    async def volume(self, args: dict[str, Any]) -> str:
        nivel = self._nivel(args)
        if nivel > self.volume_maximo:
            raise ErroFerramenta(f"Pela voz eu deixo a TV no máximo em {self.volume_maximo} (para não estourar).")
        await self._rodar(self.tv.definir_volume(nivel))
        return f"Volume da TV em {nivel}."

    async def descrever_volume(self, args: dict[str, Any]) -> str:
        return f"Vou pôr o volume da TV em {self._nivel(args)}."

    @staticmethod
    def _canal(args: dict[str, Any]) -> str:
        bruto = str(args.get("numero") or "").strip().replace(",", ".")
        if not re.fullmatch(r"\d{1,4}(\.\d{1,2})?", bruto) or float(bruto) <= 0:
            raise ErroFerramenta("Canal é um número (ex.: 5 ou 5.1).")
        return bruto

    async def canal(self, args: dict[str, Any]) -> str:
        canal = self._canal(args)
        teclas = ["KEY_PLUS100" if c == "." else f"KEY_{c}" for c in canal] + ["KEY_ENTER"]
        await self._rodar(self.tv.teclas(teclas))
        return f"Canal {canal}."

    async def descrever_canal(self, args: dict[str, Any]) -> str:
        return f"Vou pôr a TV no canal {self._canal(args)}."

    async def status(self, _args: dict[str, Any]) -> str:
        if not await self.tv.acessivel():
            return "A TV está desligada ou fora da rede."
        partes = ["A TV está ligada"]
        try:
            vol, mudo = await self.tv.volume(), await self.tv.mudo()
            partes.append(f"volume {vol}" + (" (no mudo)" if mudo else ""))
        except (TVInacessivel, RuntimeError):
            pass
        abertos = []
        for app in self.apps:
            try:
                if await self.tv.estado_app(app) == "running":
                    abertos.append(app)
            except (TVInacessivel, ValueError):
                pass
        if abertos:
            partes.append("aberto: " + ", ".join(abertos))
        if self.tv.sessao is None:
            partes.append("o controle ainda não foi pareado")
        return ", ".join(partes) + "."

    def _app(self, args: dict[str, Any]) -> str:
        pedido = normalizar(str(args.get("app") or "")).replace(" ", "")
        for app in self.apps:
            if normalizar(app).replace(" ", "") == pedido:
                return app
        raise ErroFerramenta("Esse app não está na lista da TV. Os que sei abrir: " + ", ".join(self.apps) + ".")

    async def abrir_app(self, args: dict[str, Any]) -> str:
        app = self._app(args)
        await self._rodar(self.tv.abrir_app(app))
        return f"Abri o {app} na TV."

    async def descrever_abrir_app(self, args: dict[str, Any]) -> str:
        return f"Vou abrir o {self._app(args)} na TV."

    async def _video(self, args: dict[str, Any]) -> dict[str, Any]:
        pedido = str(args.get("busca") or "").strip()
        if not pedido:
            raise ErroFerramenta("O que tocar no YouTube? Diga a música ou o vídeo.")
        if (vid := id_do_youtube(pedido)) is not None:
            return {"id": vid, "titulo": "", "canal": "", "duracao": 0}
        try:
            achados = await asyncio.to_thread(self.buscar, pedido[:120])
        except Exception as e:
            raise ErroFerramenta(f"A busca de vídeos não respondeu ({type(e).__name__}).") from e
        video = escolher_video(achados)
        if video is None:
            raise ErroFerramenta(f"Não achei '{pedido[:60]}' no YouTube.")
        return video

    async def youtube(self, args: dict[str, Any]) -> str:
        video = await self._video(args)
        apertou_ok = await self._rodar(self.tv.youtube(video["id"]))
        nome = f"'{video['titulo']}'" if video["titulo"] else "o vídeo"
        extra = " (escolhi o perfil na tela de perfis)" if apertou_ok else ""
        return f"Tocando {nome} no YouTube da TV{extra}."

    async def tocar_midia(self, args: dict[str, Any]) -> str:
        url = self._url(args)
        await self._rodar(self.tv.tocar_midia(url))
        return "Mandei a TV tocar o link."

    @staticmethod
    def _url(args: dict[str, Any]) -> str:
        url = str(args.get("url") or "").strip()
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or len(url) > 2000 or any(c in url for c in " <>\"'"):
            raise ErroFerramenta("Preciso de um link http(s) direto para o arquivo de mídia.")
        return url

    async def descrever_tocar_midia(self, args: dict[str, Any]) -> str:
        return f"Vou mandar a TV tocar este link: {self._url(args)}"

    # ------------------------------------------------------------------ atalho (sem o modelo)

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        cmd = comando_de_tv(frase)
        if cmd is None:
            return None
        nome, args = cmd
        executar = {"tv_controle": self.controle, "tv_volume": self.volume, "tv_youtube": self.youtube}[nome]
        try:
            return nome, args, await executar(args), True
        except ErroFerramenta as e:
            return nome, args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        comum = {"escrita": True, "grupo": "tv", "confirmar": False, "confirmar_se_externo": True}
        return [
            Ferramenta("tv_controle",
                       "Controle da TV da sala: desligar, volume, mudo, canal +/-, fonte/HDMI, setas, ok, voltar, "
                       "home, menu, play/pausa. Não liga a TV (ela some da rede quando desligada).",
                       esquema(["acao"], acao={"type": "string", "enum": list(ACOES), "description": "O que fazer"},
                               vezes=numero(f"Quantas vezes (1 a {MAXIMO_VEZES}), para volume, canal e setas")),
                       self.controle, descrever=self.descrever_controle, **comum),
            Ferramenta("tv_volume", f"Põe o volume da TV num número exato (0 a {self.volume_maximo}).",
                       esquema(["nivel"], nivel=numero("Volume de 0 a 100")), self.volume,
                       descrever=self.descrever_volume, **comum),
            Ferramenta("tv_canal", "Muda a TV para um canal pelo número.",
                       esquema(["numero"], numero=texto("Número do canal, ex.: '5' ou '5.1'")), self.canal,
                       descrever=self.descrever_canal, **comum),
            Ferramenta("tv_status", "Se a TV está ligada, o volume e o app aberto.", esquema([]), self.status,
                       grupo="tv"),
            Ferramenta("tv_abrir_app", "Abre um app na TV.",
                       esquema(["app"], app={"type": "string", "enum": list(self.apps)}), self.abrir_app,
                       descrever=self.descrever_abrir_app, **comum),
            Ferramenta("tv_youtube",
                       "Toca uma música ou vídeo no YouTube da TV: busca pelo nome e escolhe o mais certo (o clipe "
                       "oficial, o mais visto). Também aceita um link do YouTube.",
                       esquema(["busca"], busca=texto("O que o Felipe pediu, ex.: 'blank space taylor swift'")),
                       self.youtube, descrever=self._descrever_youtube, **comum),
            Ferramenta("tv_tocar_midia",
                       "Manda a TV tocar um link direto de mídia (mp4, mp3). Só para um link que o Felipe passou.",
                       esquema(["url"], url=texto("Link http(s) do arquivo")), self.tocar_midia,
                       escrita=True, grupo="tv", sempre_confirmar=True, confirmar_se_externo=True,
                       descrever=self.descrever_tocar_midia),
        ]

    async def _descrever_youtube(self, args: dict[str, Any]) -> str:
        pedido = str(args.get("busca") or "").strip()
        if not pedido:
            raise ErroFerramenta("O que tocar no YouTube? Diga a música ou o vídeo.")
        return f"Vou tocar '{pedido[:80]}' no YouTube da TV."


# ---------------------------------------------------------------------- frases curtas

_INICIO = r"^(?:(?:vision|visao|hey|ei|ok|pode|por favor|ai|e|entao|agora|ja|me)\s+)*"
_TV = r"(?:a\s+|na\s+|da\s+)?(?:tv|televisao)"
_DESLIGAR = re.compile(_INICIO + r"(?:desliga|desligue|desligar|apaga|apague)\s+" + _TV + r"$")
_MUDO = re.compile(_INICIO + r"(?:muta|mute|mutar|silencia|silenciar)\s+" + _TV + r"$")
_DESMUTAR = re.compile(_INICIO + r"(?:desmuta|desmute|desmutar|volta o som d)\s*" + _TV + r"$")
_VOLUME = re.compile(_INICIO + r"(?:(?:coloca|poe|bota|deixa|muda)\s+)?(?:o\s+)?volume\s+" + _TV
                     + r"\s+(?:no|em|pra|para|pro)\s+(\d{1,3})$")
_SUBIR = re.compile(_INICIO + r"(aumenta|aumentar|sobe|subir|abaixa|abaixar|diminui|diminuir|baixa)\s+(?:o\s+)?"
                    r"(?:volume|som)\s+" + _TV + r"(?:\s+(?:em\s+)?(\d{1,2})(?:\s+vezes)?)?$")
_YOUTUBE = re.compile(_INICIO + r"(?:toca|tocar|toque|coloca|colocar|poe|bota|abre|abrir)\s+(.{2,80}?)\s+no\s+youtube"
                      r"\s+" + _TV + r"$")


def comando_de_tv(frase: str) -> tuple[str, dict[str, Any]] | None:
    """"desliga a TV", "aumenta o volume da TV em 3", "volume da TV no 10", "toca X no youtube da TV".
    Com prazo ("em 30 minutos") ou "não", fica com o modelo (timer, dúvida)."""
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    palavras = set(t.split())
    if not t or "nao" in palavras or palavras & {"minuto", "minutos", "hora", "horas", "segundo", "segundos"}:
        return None
    if _DESLIGAR.match(t):
        return "tv_controle", {"acao": "desligar"}
    if _MUDO.match(t):
        return "tv_controle", {"acao": "mudo"}
    if _DESMUTAR.match(t):
        return "tv_controle", {"acao": "desmutar"}
    if m := _VOLUME.match(t):
        return "tv_volume", {"nivel": int(m.group(1))}
    if m := _SUBIR.match(t):
        acao = "volume_mais" if m.group(1).startswith(("aument", "sob", "sub")) else "volume_menos"
        return "tv_controle", {"acao": acao, "vezes": int(m.group(2) or 1)}
    if m := _YOUTUBE.match(t):
        return "tv_youtube", {"busca": m.group(1).strip()}
    return None
