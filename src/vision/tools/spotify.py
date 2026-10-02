"""Ferramentas de música pelo Spotify (vision/spotify.py): tocar uma música, artista, álbum ou playlist, controlar
e saber o que está tocando. Só aparecem com o login feito (`vision spotify-login`)."""

from __future__ import annotations

import re
from typing import Any

from vision.spotify import ErroSpotify, SemLogin, Spotify
from vision.tools.base import PRAZO_PC_S, ErroFerramenta, Ferramenta, esquema, texto
from vision.tools.pc import normalizar

TIPOS = ["musica", "artista", "album", "playlist"]
ACOES = ["pausar", "continuar", "proxima", "anterior"]


class Musica:
    def __init__(self, spotify: Spotify):
        self.spotify = spotify

    async def _chamar(self, coro) -> str:
        try:
            return await coro
        except (ErroSpotify, SemLogin) as e:
            raise ErroFerramenta(str(e)) from e

    async def tocar(self, args: dict[str, Any]) -> str:
        busca = " ".join(str(args.get("busca") or "").split())[:120]
        tipo = str(args.get("tipo") or "musica").lower()
        if not busca:
            raise ErroFerramenta("Tocar o quê? Diga a música, o artista, o álbum ou a playlist.")
        if tipo not in TIPOS:
            tipo = "musica"
        tocando = await self._chamar(self.spotify.tocar(busca, tipo))
        return f"Tocando {tocando}."

    async def descrever_tocar(self, args: dict[str, Any]) -> str:
        return f"Vou tocar no Spotify: {' '.join(str(args.get('busca') or '').split())[:120]}."

    async def controlar(self, args: dict[str, Any]) -> str:
        acao = str(args.get("acao") or "").lower()
        if acao not in ACOES:
            raise ErroFerramenta("Ação: pausar, continuar, proxima ou anterior.")
        return await self._chamar(self.spotify.controlar(acao))

    async def tocando(self, _args: dict[str, Any]) -> str:
        return await self._chamar(self.spotify.tocando())

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """"toca Bohemian Rhapsody", "coloca a playlist X no Spotify": sem passar pelo modelo."""
        if pergunta_o_que_toca(frase):  # o modelo respondia de cabeça ("é do álbum Foco") sem perguntar ao Spotify
            try:
                return "musica_tocando", {}, await self.tocando({}), True
            except ErroFerramenta as e:
                return "musica_tocando", {}, str(e), False
        args = comando_de_musica(frase)
        if args is None:
            return None
        try:
            return "musica_tocar", args, await self.tocar(args), True
        except ErroFerramenta as e:
            return "musica_tocar", args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        return [
            Ferramenta("musica_tocar",
                       "Toca no Spotify do PC uma música, um artista, um álbum ou uma playlist (abre o Spotify se "
                       "precisar).",
                       esquema(["busca"], busca=texto("O que tocar, como o Felipe disse (ex.: 'Bohemian Rhapsody')"),
                               tipo={"type": "string", "enum": TIPOS,
                                     "description": "musica (padrão), artista, album ou playlist"}),
                       self.tocar, escrita=True, grupo="musica", confirmar=False, confirmar_se_externo=True,
                       descrever=self.descrever_tocar, prazo_s=PRAZO_PC_S),
            Ferramenta("musica_controlar", "Pausa, continua, pula ou volta a música do Spotify.",
                       esquema(["acao"], acao={"type": "string", "enum": ACOES}),
                       self.controlar, escrita=True, grupo="musica", confirmar=False, prazo_s=PRAZO_PC_S),
            Ferramenta("musica_tocando", "Diz que música está tocando no Spotify agora.", esquema([]),
                       self.tocando, grupo="musica", prazo_s=PRAZO_PC_S),
        ]


_INICIO = r"^(?:(?:vision|visao|hey|ei|ok|pode|por favor|ai|e|entao|agora|ja|me)\s+)*"
_TOCAR = re.compile(_INICIO + r"(?:toca|tocar|toque|da play em|play em)\s+(.+?)(?:\s+(?:no|do) spotify)?"
                    r"(?:\s+(?:por favor|pf))?$")
_COLOCAR = re.compile(_INICIO + r"(?:coloca|colocar|bota|botar|poe|por)\s+(.+?)\s+(?:no|pra tocar no) spotify"
                      r"(?:\s+(?:por favor|pf))?$")
_VAGO = {"musica", "uma musica", "a musica", "algo", "alguma coisa", "alguma musica", "som", "um som", "de novo",
         "outra", "outra musica", "isso", "ai", "aquela", "aquela musica"}
_NAO_E_PEDIDO = {"nao", "amanha", "depois", "quando", "daqui", "minutos", "horas", "se", "porque", "que horas"}


_O_QUE_TOCA = re.compile(_INICIO + r"(?:que|qual|qual e a|qual que e a|que que e essa) (?:musica|som) "
                         r"(?:e essa|e esse|e|esta tocando|ta tocando|que ta tocando|que esta tocando|tocando)"
                         r"(?: agora| ai)?$")


def pergunta_o_que_toca(frase: str) -> bool:
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    return bool(_O_QUE_TOCA.match(t))


def comando_de_musica(frase: str) -> dict[str, Any] | None:
    """"toca a playlist Foco no Spotify" → {"busca": "foco", "tipo": "playlist"}. None se não for claro."""
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    if not t or _NAO_E_PEDIDO & set(t.split()):
        return None
    m = _TOCAR.match(t) or _COLOCAR.match(t)
    if not m:
        return None
    resto = m.group(1).strip()
    tipo = "musica"
    for prefixo, qual in (("a playlist ", "playlist"), ("playlist ", "playlist"), ("o album ", "album"),
                          ("album ", "album"), ("o artista ", "artista"), ("musicas do ", "artista"),
                          ("musicas da ", "artista"), ("musicas de ", "artista"), ("a musica ", "musica"),
                          ("musica ", "musica")):
        if resto.startswith(prefixo):
            resto, tipo = resto[len(prefixo):].strip(), qual
            break
    if not resto or resto in _VAGO or len(resto) < 2 or resto.split()[0] in {"no", "na", "nos", "nas", "em"}:
        return None  # "toca no assunto da reunião" não é música
        return None
    return {"busca": resto, "tipo": tipo}
