"""Spotify pela API oficial (Web API), para o Vision tocar música no Spotify do PC (pedido de 01/10; conta Premium).

Login: `vision spotify-login` faz o OAuth com PKCE (sem segredo) no navegador, uma vez. O Client ID vem de um app
que o próprio Felipe cria em developer.spotify.com, com o endereço de retorno `http://127.0.0.1:8769/callback`.
Client ID e tokens ficam em data/spotify-oauth.json (fora do git). O token de acesso dura 1 h e é renovado
sozinho; a renovação pode trocar o refresh token, por isso cada troca é gravada na hora, por inteiro.

Tocar exige um "dispositivo" ativo: se o Spotify do PC estiver fechado, ele é aberto e o Vision espera ele
aparecer na conta antes de mandar tocar.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from vision.config import Config

log = logging.getLogger(__name__)

PORTA_RETORNO = 8769
RETORNO = f"http://127.0.0.1:{PORTA_RETORNO}/callback"
ESCOPOS = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
URL_CONTAS = "https://accounts.spotify.com"
URL_API = "https://api.spotify.com/v1"
PRAZO_LOGIN_S = 300
ESPERA_DISPOSITIVO_S = 15


class SemLogin(RuntimeError):
    """Sem login do Spotify (ou ele foi revogado): rode `vision spotify-login`."""


class ErroSpotify(RuntimeError):
    """Erro para contar ao Felipe (sem dispositivo, nada encontrado, Spotify fora do ar)."""


def arquivo(cfg: Config) -> Path:
    return cfg.dados / "spotify-oauth.json"


def ler(cfg: Config) -> dict[str, Any]:
    try:
        dados = json.loads(arquivo(cfg).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("spotify-oauth.json ilegível: o login do Spotify vai precisar ser refeito")
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar(cfg: Config, dados: dict[str, Any]) -> None:
    destino = arquivo(cfg)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(dados, ensure_ascii=False))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, destino)  # o refresh token antigo pode ter morrido: nunca fica pela metade


def tem_login(cfg: Config) -> bool:
    d = ler(cfg)
    return bool(d.get("client_id") and d.get("refresh_token"))


# ---------------------------------------------------------------------- login (PKCE)

def _pkce() -> tuple[str, str]:
    verificador = secrets.token_urlsafe(64)[:96]
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def _atender(recebido: asyncio.Future, estado: str):
    """Página única em 127.0.0.1 que recebe ?code=...&state=...; só o retorno com o `state` deste login conta."""

    async def atender(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            try:
                linha = (await asyncio.wait_for(leitor.readline(), 10)).decode("latin-1")
            except (TimeoutError, ValueError, asyncio.LimitOverrunError):
                linha = ""
            partes = linha.split(" ")
            alvo = urlparse(partes[1] if len(partes) > 1 else "/")
            q = parse_qs(alvo.query)
            do_login = alvo.path == "/callback" and q.get("state", [None])[0] == estado
            ok = do_login and "code" in q
            if ok and not recebido.done():
                recebido.set_result(q["code"][0])
            elif do_login and "error" in q and not recebido.done():
                recebido.set_exception(SemLogin(f"o Spotify recusou o login: {q['error'][0]}"))
            corpo = ("Pronto: o Vision está ligado ao seu Spotify. Pode fechar esta aba." if ok
                     else "Não deu certo. Volte ao terminal.").encode("utf-8")
            escritor.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           b"Content-Length: " + str(len(corpo)).encode() + b"\r\nConnection: close\r\n\r\n" + corpo)
            await escritor.drain()
        finally:
            escritor.close()

    return atender


async def login(cfg: Config, client_id: str | None = None, abrir=webbrowser.open) -> int:
    """`vision spotify-login [--client-id ID]`: login no navegador; guarda o Client ID e os tokens."""
    client_id = (client_id or ler(cfg).get("client_id") or "").strip()
    if not client_id:
        print("Falta o Client ID do seu app do Spotify. Crie um em https://developer.spotify.com/dashboard")
        print(f"(Web API, endereço de retorno {RETORNO}) e rode: vision spotify-login --client-id SEU_CLIENT_ID")
        return 1
    verificador, desafio = _pkce()
    estado = secrets.token_urlsafe(24)
    recebido: asyncio.Future = asyncio.get_running_loop().create_future()
    try:
        servidor = await asyncio.start_server(_atender(recebido, estado), "127.0.0.1", PORTA_RETORNO)
    except OSError:
        print(f"A porta {PORTA_RETORNO} está ocupada. Feche o que estiver usando e tente de novo.")
        return 1
    url = f"{URL_CONTAS}/authorize?" + urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": RETORNO, "scope": ESCOPOS,
        "code_challenge_method": "S256", "code_challenge": desafio, "state": estado})
    try:
        print("Abrindo o navegador para o login do Spotify (se não abrir, cole este endereço):")
        print(url)
        abrir(url)
        try:
            codigo = await asyncio.wait_for(recebido, PRAZO_LOGIN_S)
        except TimeoutError:
            print("O login não foi concluído a tempo (5 min). Rode `vision spotify-login` de novo.")
            return 1
        except SemLogin as e:
            print(e)
            return 1
    finally:
        servidor.close()
    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(f"{URL_CONTAS}/api/token", data={
            "grant_type": "authorization_code", "code": codigo, "redirect_uri": RETORNO,
            "client_id": client_id, "code_verifier": verificador})
    if r.status_code != 200:
        print(f"O Spotify não aceitou o código ({r.status_code}): {r.text[:200]}")
        return 1
    t = r.json()
    gravar(cfg, {"client_id": client_id, "access_token": t["access_token"], "refresh_token": t["refresh_token"],
                 "expira": time.time() + int(t.get("expires_in", 3600)) - 60})
    async with Spotify(cfg) as sp:
        dispositivos = await sp.dispositivos()
    print(f"Spotify ligado. Login guardado em {arquivo(cfg)}.")
    print("Dispositivos agora:", ", ".join(d.get("name", "?") for d in dispositivos) or "nenhum (abra o Spotify)")
    print("Reinicie o Vision (bandeja → Sair, e abrir de novo) para ele usar.")
    return 0


# ---------------------------------------------------------------------- cliente

class Spotify:
    def __init__(self, cfg: Config, abrir_app: Callable[[str], Any] | None = None,
                 transporte: httpx.AsyncBaseTransport | None = None, dormir=asyncio.sleep):
        self.cfg = cfg
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), transport=transporte)
        self._abrir_app = abrir_app if abrir_app is not None else os.startfile
        self._dormir = dormir
        self._trava = asyncio.Lock()

    async def __aenter__(self) -> Spotify:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.fechar()

    async def fechar(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ token

    async def _token(self, forcar: bool = False) -> str:
        async with self._trava:
            d = ler(self.cfg)
            if not d.get("refresh_token") or not d.get("client_id"):
                raise SemLogin("O Spotify não está ligado. Diga ao Felipe para rodar `vision spotify-login`.")
            if not forcar and d.get("access_token") and time.time() < float(d.get("expira", 0)):
                return d["access_token"]
            r = await self.http.post(f"{URL_CONTAS}/api/token", data={
                "grant_type": "refresh_token", "refresh_token": d["refresh_token"], "client_id": d["client_id"]})
            if r.status_code in (400, 401):
                raise SemLogin("O login do Spotify expirou ou foi revogado. Diga ao Felipe para rodar "
                               "`vision spotify-login`.")
            r.raise_for_status()
            t = r.json()
            d.update(access_token=t["access_token"], expira=time.time() + int(t.get("expires_in", 3600)) - 60)
            if t.get("refresh_token"):
                d["refresh_token"] = t["refresh_token"]
            gravar(self.cfg, d)
            return d["access_token"]

    async def _api(self, metodo: str, caminho: str, **kw: Any) -> httpx.Response:
        renovar = False
        for tentativa in range(3):
            token = await self._token(forcar=renovar)
            renovar = False
            try:
                r = await self.http.request(metodo, URL_API + caminho,
                                            headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.HTTPError as e:
                raise ErroSpotify(f"O Spotify não respondeu ({type(e).__name__}).") from e
            if r.status_code == 401 and tentativa == 0:
                renovar = True  # token vencido antes da hora: renova uma vez
                continue
            if r.status_code == 429 and tentativa < 2:
                await self._dormir(min(float(r.headers.get("Retry-After", "1") or 1), 3))
                continue
            return r
        return r

    # ------------------------------------------------------------------ dispositivos

    async def dispositivos(self) -> list[dict[str, Any]]:
        r = await self._api("GET", "/me/player/devices")
        if r.status_code != 200:
            raise ErroSpotify(f"Não consegui ver os dispositivos do Spotify ({r.status_code}).")
        return list(r.json().get("devices") or [])

    @staticmethod
    def _escolher(dispositivos: list[dict[str, Any]]) -> dict[str, Any] | None:
        """O que já está tocando; senão o computador (este PC); senão o primeiro."""
        uteis = [d for d in dispositivos if not d.get("is_restricted")]
        for criterio in (lambda d: d.get("is_active"), lambda d: d.get("type") == "Computer", lambda d: True):
            achado = next((d for d in uteis if criterio(d)), None)
            if achado is not None:
                return achado
        return None

    async def dispositivo(self) -> dict[str, Any]:
        """Um dispositivo para tocar. Sem nenhum, abre o Spotify do PC e espera ele aparecer na conta."""
        d = self._escolher(await self.dispositivos())
        if d is not None:
            return d
        await asyncio.to_thread(self._abrir_app, "spotify:")
        prazo = time.monotonic() + ESPERA_DISPOSITIVO_S
        while time.monotonic() < prazo:
            await self._dormir(1)
            d = self._escolher(await self.dispositivos())
            if d is not None:
                return d
        raise ErroSpotify("Abri o Spotify, mas ele não apareceu na sua conta a tempo. Tente de novo em instantes.")

    # ------------------------------------------------------------------ ações

    async def buscar(self, busca: str, tipo: str) -> dict[str, Any]:
        tipo_api = {"musica": "track", "artista": "artist", "album": "album", "playlist": "playlist"}[tipo]
        r = await self._api("GET", "/search", params={"q": busca, "type": tipo_api, "limit": 5})
        if r.status_code != 200:
            raise ErroSpotify(f"A busca no Spotify falhou ({r.status_code}).")
        itens = [i for i in (r.json().get(tipo_api + "s") or {}).get("items") or [] if i]
        if not itens:
            raise ErroSpotify(f"Não achei '{busca}' no Spotify.")
        return itens[0]

    async def tocar(self, busca: str, tipo: str = "musica") -> str:
        item = await self.buscar(busca, tipo)
        disp = await self.dispositivo()
        corpo = {"uris": [item["uri"]]} if tipo == "musica" else {"context_uri": item["uri"]}
        r = await self._api("PUT", "/me/player/play", params={"device_id": disp["id"]}, json=corpo)
        if r.status_code == 403:
            raise ErroSpotify("O Spotify recusou tocar (a conta precisa ser Premium).")
        if r.status_code not in (200, 202, 204):
            raise ErroSpotify(f"O Spotify não conseguiu tocar ({r.status_code}).")
        return descrever_item(item, tipo)

    async def controlar(self, acao: str) -> str:
        metodo, caminho, feito = {
            "pausar": ("PUT", "/me/player/pause", "Pausei."),
            "continuar": ("PUT", "/me/player/play", "Voltei a tocar."),
            "proxima": ("POST", "/me/player/next", "Próxima."),
            "anterior": ("POST", "/me/player/previous", "Anterior."),
        }[acao]
        r = await self._api(metodo, caminho)
        if r.status_code == 404:
            raise ErroSpotify("Nada está tocando no Spotify agora.")
        if r.status_code not in (200, 202, 204):
            raise ErroSpotify(f"O Spotify não aceitou ({r.status_code}).")
        return feito

    async def tocando(self) -> str:
        r = await self._api("GET", "/me/player/currently-playing")
        if r.status_code == 204 or not r.content:
            return "Nada está tocando no Spotify."
        if r.status_code != 200:
            raise ErroSpotify(f"Não consegui ver o que está tocando ({r.status_code}).")
        dados = r.json()
        item = dados.get("item")
        if not item:
            return "Nada está tocando no Spotify."
        estado = "Tocando" if dados.get("is_playing") else "Pausado"
        return f"{estado}: {descrever_item(item, 'musica')}."


def descrever_item(item: dict[str, Any], tipo: str) -> str:
    nome = item.get("name") or "?"
    if tipo == "musica":
        artistas = ", ".join(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))
        return f"{nome}, de {artistas}" if artistas else nome
    if tipo == "album":
        artistas = ", ".join(a.get("name", "") for a in item.get("artists") or [] if a.get("name"))
        return f"o álbum {nome}" + (f", de {artistas}" if artistas else "")
    if tipo == "playlist":
        return f"a playlist {nome}"
    return f"músicas de {nome}"
