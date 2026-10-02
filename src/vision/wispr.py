"""Wispr Flow: reuniões, transcrições, notas e agenda, pelo servidor MCP oficial dele (só leitura).

O login é OAuth 2.1 (PKCE + registro dinâmico de cliente), feito uma vez por você no navegador com
`vision wispr-login`. Os tokens ficam em data/wispr-oauth.json (fora do git). O token de acesso dura
~5 min e o de renovação muda a cada uso: por isso cada troca é gravada na hora, por inteiro.

O núcleo nunca abre o navegador sozinho: sem login válido, a conexão falha e as ferramentas dizem para
rodar `vision wispr-login`.
"""

from __future__ import annotations

import asyncio
import contextlib
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
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("%s ilegível: o login do Wispr Flow vai precisar ser refeito", self.arquivo.name)
            return {}
        return dados if isinstance(dados, dict) else {}

    def _gravar(self, chave: str, valor: dict[str, Any]) -> None:
        dados = {**self._ler(), chave: valor}
        self.arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(dados, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
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


def _provedor(cfg: Config, interativo: bool, avisar=print):
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
        esperado = parse_qs(urlparse(endereco).query).get("state", [None])[0]
        servidor = await asyncio.start_server(_atender(recebido, esperado), "127.0.0.1", PORTA_RETORNO)
        avisar("Abrindo o navegador para entrar no Wispr Flow (use Google, Apple ou Microsoft; "
               "e-mail e senha não funcionam aqui).")
        avisar(f"Se não abrir, copie este endereço: {endereco}")
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


def _atender(recebido: asyncio.Future, esperado: str | None = None):
    """Servidor de uma página só, em 127.0.0.1, que recebe o ?code=...&state=... do navegador.

    Só o retorno com o `state` deste login conta: uma aba qualquer chamando o endereço não derruba o login."""
    from mcp.shared.auth import AuthorizationCodeResult

    async def atender(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            try:
                linha = (await asyncio.wait_for(leitor.readline(), 10)).decode("latin-1")
            except (TimeoutError, ValueError, asyncio.LimitOverrunError):
                linha = ""
            alvo = linha.split(" ")[1] if len(linha.split(" ")) > 1 else "/"
            q = parse_qs(urlparse(alvo).query)
            do_login = urlparse(alvo).path == "/callback" and (esperado is None or q.get("state", [None])[0] == esperado)
            ok = do_login and "code" in q
            if ok and not recebido.done():
                recebido.set_result(AuthorizationCodeResult(
                    code=q["code"][0], state=q.get("state", [None])[0], iss=q.get("iss", [None])[0]))
            elif do_login and "error" in q and not recebido.done():
                recebido.set_exception(PrecisaLogin(f"o Wispr recusou o login: {q['error'][0]}"))
            corpo = ("Pronto: o Vision está ligado ao Wispr Flow. Pode fechar esta aba." if ok
                     else "Não deu certo. Volte ao terminal.").encode("utf-8")
            escritor.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                           b"Content-Length: " + str(len(corpo)).encode() + b"\r\nConnection: close\r\n\r\n" + corpo)
            await escritor.drain()
        finally:
            escritor.close()

    return atender


def transporte(cfg: Config, interativo: bool = False, avisar=print):
    """Alvo do ConexaoMCP: um transporte novo a cada conexão."""
    from vision.tools.mcp_host import Remoto

    @contextlib.asynccontextmanager
    async def criar():
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        # O SDK não fecha um cliente HTTP que ele não criou: este fecha junto com a conexão.
        async with httpx2.AsyncClient(auth=_provedor(cfg, interativo, avisar), timeout=httpx2.Timeout(30, read=60)) as cliente:
            async with streamable_http_client(str(cfg.get("mcp.wispr.url", URL_PADRAO)), http_client=cliente) as fluxos:
                yield fluxos

    return Remoto(criar)


async def login(cfg: Config, avisar=print) -> int:
    """`vision wispr-login` e a tela de Conexões: faz o login no navegador e mostra o que ficou disponível."""
    from vision.tools.mcp_host import ConexaoMCP

    c = ConexaoMCP("wispr", transporte(cfg, interativo=True, avisar=avisar), timeout_s=PRAZO_LOGIN_S + 30)
    try:
        await c.iniciar()  # dentro do try: cancelado pela tela, o finally fecha o fluxo e a porta de retorno
        if c.cliente is None:
            if "Timeout" in str(c.erro):
                avisar("O login não foi concluído a tempo (5 min). Tente de novo.")
            else:
                avisar(f"Não consegui conectar: {c.erro}")
            return 1
        ferramentas = await c.listar()
        r = await c.chamar("list_upcoming_meetings", {"window_hours": 24, "limit": 5})
        avisar(f"Conectado ao Wispr Flow ({len(ferramentas)} ferramentas). Próximas reuniões: "
               + ("ok" if r.ok else f"erro: {r.texto[:200]}"))
        return 0
    finally:
        await c.fechar()
