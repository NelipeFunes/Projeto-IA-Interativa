"""Wispr Flow: reuniões, transcrições, notas e agenda, pelo servidor MCP oficial dele (só leitura).

O login é OAuth 2.1 (PKCE + registro dinâmico de cliente), feito uma vez por você no navegador com
`vision wispr-login`. Os tokens ficam em data/wispr-oauth.json (fora do git). O token de acesso dura
~5 min e o de renovação muda a cada uso: por isso cada troca é gravada na hora, por inteiro.

O núcleo nunca abre o navegador sozinho: sem login válido, a conexão falha e as ferramentas dizem para
rodar `vision wispr-login`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from vision.config import Config

log = logging.getLogger(__name__)

URL_PADRAO = "https://api.wisprflow.ai/connect/mcp"
PORTA_RETORNO = 8767  # o endereço de retorno é registrado no Wispr: muda só junto com um login novo
PRAZO_LOGIN_S = 300


class PrecisaLogin(RuntimeError):
    """Sem token válido do Wispr Flow (ou ele foi revogado): rode `vision wispr-login`."""


class ArmazemTokens:
    """TokenStorage do SDK do MCP num arquivo JSON, gravado de forma atômica."""

    def __init__(self, arquivo: Path):
        self.arquivo = arquivo

    def _ler(self) -> dict[str, Any]:
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return dados if isinstance(dados, dict) else {}

    def _gravar(self, chave: str, valor: dict[str, Any]) -> None:
        dados = {**self._ler(), chave: valor}
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.arquivo)  # o refresh token antigo já morreu: nunca pode ficar pela metade

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        t = self._ler().get("tokens")
        return OAuthToken.model_validate(t) if t else None

    async def set_tokens(self, tokens) -> None:
        self._gravar("tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        c = self._ler().get("cliente")
        return OAuthClientInformationFull.model_validate(c) if c else None

    async def set_client_info(self, client_info) -> None:
        self._gravar("cliente", client_info.model_dump(mode="json", exclude_none=True))


def arquivo_tokens(cfg: Config) -> Path:
    return cfg.caminho(str(cfg.get("mcp.wispr.oauth", "data/wispr-oauth.json")))


def tem_login(cfg: Config) -> bool:
    return bool(ArmazemTokens(arquivo_tokens(cfg))._ler().get("tokens"))


def _provedor(cfg: Config, interativo: bool):
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

    url = str(cfg.get("mcp.wispr.url", URL_PADRAO))
    retorno = f"http://127.0.0.1:{PORTA_RETORNO}/callback"
    recebido: asyncio.Future[AuthorizationCodeResult] | None = None
    servidor: asyncio.base_events.Server | None = None

    async def ao_redirecionar(endereco: str) -> None:
        nonlocal recebido, servidor
        if not interativo:
            raise PrecisaLogin("o Wispr Flow pediu login de novo: rode `vision wispr-login`")
        recebido = asyncio.get_running_loop().create_future()
        servidor = await asyncio.start_server(_atender(recebido), "127.0.0.1", PORTA_RETORNO)
        print("Abrindo o navegador para entrar no Wispr Flow (use Google, Apple ou Microsoft;")
        print("e-mail e senha não funcionam aqui). Se não abrir, copie este endereço:\n")
        print(endereco + "\n")
        webbrowser.open(endereco)

    async def ao_voltar() -> AuthorizationCodeResult:
        assert recebido is not None and servidor is not None
        try:
            return await asyncio.wait_for(recebido, PRAZO_LOGIN_S)
        finally:
            servidor.close()

    return OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="Vision (assistente local)",
            redirect_uris=[retorno],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=ArmazemTokens(arquivo_tokens(cfg)),
        redirect_handler=ao_redirecionar,
        callback_handler=ao_voltar,
    )


def _atender(recebido: asyncio.Future):
    """Servidor de uma página só, em 127.0.0.1, que recebe o ?code=...&state=... do navegador."""
    from mcp.shared.auth import AuthorizationCodeResult

    async def atender(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            linha = (await asyncio.wait_for(leitor.readline(), 10)).decode("latin-1")
            alvo = linha.split(" ")[1] if len(linha.split(" ")) > 1 else "/"
            q = parse_qs(urlparse(alvo).query)
            ok = urlparse(alvo).path == "/callback" and "code" in q
            if ok and not recebido.done():
                recebido.set_result(AuthorizationCodeResult(
                    code=q["code"][0], state=q.get("state", [None])[0], iss=q.get("iss", [None])[0]))
            elif "error" in q and not recebido.done():
                recebido.set_exception(PrecisaLogin(f"o Wispr recusou o login: {q['error'][0]}"))
            corpo = ("Pronto: o Vision está ligado ao Wispr Flow. Pode fechar esta aba." if ok
                     else "Não deu certo. Volte ao terminal.").encode("utf-8")
            escritor.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           b"Content-Length: " + str(len(corpo)).encode() + b"\r\nConnection: close\r\n\r\n" + corpo)
            await escritor.drain()
        finally:
            escritor.close()

    return atender


def transporte(cfg: Config, interativo: bool = False):
    """Alvo do ConexaoMCP: um transporte novo a cada conexão."""
    from vision.tools.mcp_host import Remoto

    def criar():
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        cliente = httpx2.AsyncClient(auth=_provedor(cfg, interativo), timeout=httpx2.Timeout(30, read=60))
        return streamable_http_client(str(cfg.get("mcp.wispr.url", URL_PADRAO)), http_client=cliente)

    return Remoto(criar)


async def login(cfg: Config) -> int:
    """`vision wispr-login`: faz o login no navegador e mostra o que ficou disponível."""
    from vision.tools.mcp_host import ConexaoMCP

    c = ConexaoMCP("wispr", transporte(cfg, interativo=True), timeout_s=PRAZO_LOGIN_S + 30)
    await c.iniciar()
    try:
        if c.cliente is None:
            if "Timeout" in str(c.erro):
                print("O login não foi concluído a tempo (5 min). Rode `vision wispr-login` de novo.")
            else:
                print(f"Não consegui conectar: {c.erro}")
            return 1
        ferramentas = await c.listar()
        r = await c.chamar("list_upcoming_meetings", {"window_hours": 24, "limit": 5})
        print(f"Conectado ao Wispr Flow ({len(ferramentas)} ferramentas). Login guardado em {arquivo_tokens(cfg)}.")
        print("Próximas reuniões (24 h):", "ok" if r.ok else f"erro: {r.texto[:200]}")
        print("Reinicie o Vision (bandeja → Sair, e abrir de novo) para ele usar.")
        return 0
    finally:
        await c.fechar()
