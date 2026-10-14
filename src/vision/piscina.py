"""Piscina Tholz pela nuvem da fabricante (T-017, etapa 1: só leitura, ainda sem ferramentas para o modelo).

O app Tholz Casa Inteligente fala com uma API na AWS autenticada por um login Cognito (fluxo SRP: a senha nunca
vai pela rede). Aqui: entrar com a SUA conta (`vision piscina-login`; e-mail e senha digitados por você no
terminal, nunca guardados), guardar a sessão em `data/piscina/sessao.json` e ler a lista de dispositivos
(`vision piscina-ler`). Nada é enviado à piscina nesta etapa.

Os identificadores do backend (pool e client do login, endereço da API) ficam em `data/piscina/app.json`, fora do
git: o repositório é público. O cliente só fala com o endereço desse arquivo, e só se ele for uma API da AWS
(`*.execute-api.<região>.amazonaws.com`) e do Cognito da mesma região: nunca um endereço vindo de texto do
usuário, da web ou da memória. Tokens e senha nunca vão para log nem para mensagem de erro.

Uso pessoal, na própria conta e nos próprios equipamentos. Os termos de uso da Tholz proíbem engenharia reversa
(decisão do dono registrada na T-017): a Tholz pode bloquear a conta ou mudar a API a qualquer momento.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from vision.arquivos import gravar_json, ler_json
from vision.config import Config

log = logging.getLogger(__name__)
for _nome in ("botocore", "boto3", "pycognito", "urllib3"):  # em DEBUG gravam cabeçalhos e corpos do login
    logging.getLogger(_nome).setLevel(logging.WARNING)

PASTA = "piscina"
ARQUIVO_APP = "app.json"
ARQUIVO_SESSAO = "sessao.json"
ARQUIVO_LEITURA = "ultima-leitura.json"
FOLGA_RENOVAR_S = 120  # renova o token quando faltam menos de 2 min
TIMEOUT = httpx.Timeout(15, connect=5)

POOL_ID = re.compile(r"^[a-z]{2}-[a-z]+-\d_[A-Za-z0-9]{6,20}$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9]{20,40}$")
REGIAO = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}$")
# Caminhos de leitura que o cliente conhece (nunca um caminho vindo de fora). Mais de um: o primeiro que existir.
CAMINHOS = {"dispositivos": ("/device", "/devices"), "perfil": ("/user/profile",)}


class ErroPiscina(Exception):
    """Erro que pode ser contado ao Felipe (sem token, senha nem endereço do servidor)."""


class SemConfiguracao(ErroPiscina):
    pass


class SemLogin(ErroPiscina):
    pass


class SessaoVencida(ErroPiscina):
    pass


@dataclass(frozen=True)
class App:
    pool_id: str
    client_id: str
    regiao: str
    api_url: str

    @property
    def cognito_url(self) -> str:
        return f"https://cognito-idp.{self.regiao}.amazonaws.com/"


def _pasta(cfg: Config):
    return cfg.dados / PASTA


def ler_app(cfg: Config) -> App:
    """Os identificadores do backend (data/piscina/app.json), validados: só uma API da AWS e o Cognito da mesma região."""
    d = ler_json(_pasta(cfg) / ARQUIVO_APP)
    if not isinstance(d, dict):
        raise SemConfiguracao("Falta data/piscina/app.json com os identificadores do app da Tholz (ver README).")
    pool, client, regiao, url = (d.get(k) for k in ("pool_id", "client_id", "regiao", "api_url"))
    if not (isinstance(pool, str) and POOL_ID.match(pool) and isinstance(client, str) and CLIENT_ID.match(client)
            and isinstance(regiao, str) and REGIAO.match(regiao) and isinstance(url, str)):
        raise SemConfiguracao("data/piscina/app.json está incompleto ou fora do formato.")
    if not pool.startswith(regiao + "_"):
        raise SemConfiguracao("data/piscina/app.json: a região não bate com a do pool.")
    partes = urlsplit(url)
    host_ok = re.fullmatch(rf"[a-z0-9]{{8,12}}\.execute-api\.{re.escape(regiao)}\.amazonaws\.com", partes.hostname or "")
    if (partes.scheme != "https" or not host_ok or partes.port is not None or partes.username or partes.password
            or partes.query or partes.fragment or not re.fullmatch(r"/[A-Za-z0-9_-]{1,20}", partes.path)):
        raise SemConfiguracao("data/piscina/app.json: o endereço da API precisa ser https://<id>.execute-api."
                              f"{regiao}.amazonaws.com/<estágio>.")
    return App(pool, client, regiao, f"https://{partes.hostname}{partes.path}")


# ---------------------------------------------------------------- sessão

def ler_sessao(cfg: Config) -> dict[str, Any] | None:
    d = ler_json(_pasta(cfg) / ARQUIVO_SESSAO)
    if not (isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k] for k in ("id_token", "refresh_token"))
            and isinstance(d.get("expira"), int | float)):
        return None
    return d


def tem_login(cfg: Config) -> bool:
    return ler_sessao(cfg) is not None


def _expira_do_jwt(token: str) -> float:
    """O `exp` do token (sem verificar a assinatura: só decide quando renovar). Ilegível: já vencido."""
    try:
        corpo = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(corpo + "=" * (-len(corpo) % 4)))["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return 0.0


def _gravar_sessao(cfg: Config, id_token: str, access_token: str, refresh_token: str, expira: float) -> None:
    gravar_json(_pasta(cfg) / ARQUIVO_SESSAO, {"id_token": id_token, "access_token": access_token,
                                               "refresh_token": refresh_token, "expira": expira})


def apagar_sessao(cfg: Config) -> None:
    (_pasta(cfg) / ARQUIVO_SESSAO).unlink(missing_ok=True)


def _traduzir_erro_de_login(e: Exception) -> str:
    """Mensagem para o Felipe, sem repetir o texto da exceção (ele pode trazer dados da conta)."""
    codigo = ""
    resposta = getattr(e, "response", None)
    if isinstance(resposta, dict):
        codigo = str(resposta.get("Error", {}).get("Code", ""))
    nome = type(e).__name__
    if codigo in ("NotAuthorizedException", "UserNotFoundException") or nome in ("NotAuthorizedException",):
        return "E-mail ou senha incorretos."
    if codigo == "UserNotConfirmedException":
        return "A conta ainda não foi confirmada: confirme pelo app da Tholz."
    if "MFA" in nome or "ForceChangePassword" in nome or codigo in ("PasswordResetRequiredException",):
        return "A conta pede uma etapa extra (código ou troca de senha): ainda não é suportado."
    return f"Não deu para entrar na conta da Tholz ({codigo or nome})."


async def login(cfg: Config, email: str, senha: str, *, fabrica=None) -> None:
    """Entra com e-mail e senha (SRP) e grava a sessão. A senha só existe nesta função, na memória.
    `fabrica`: troca o pycognito nos testes."""
    if not isinstance(email, str) or not EMAIL.match(email.strip()):
        raise ErroPiscina("E-mail inválido.")
    if not isinstance(senha, str) or not (1 <= len(senha) <= 256) or "\n" in senha or "\r" in senha:
        raise ErroPiscina("Senha inválida.")
    app = ler_app(cfg)

    def entrar() -> tuple[str, str, str]:
        if fabrica is None:
            from botocore import UNSIGNED
            from botocore.config import Config as ConfigBoto
            from pycognito import Cognito

            # Sem assinatura: nenhuma credencial da AWS do PC (variáveis, ~/.aws) é lida nem enviada.
            usuario = Cognito(app.pool_id, app.client_id, user_pool_region=app.regiao, username=email.strip(),
                              botocore_config=ConfigBoto(signature_version=UNSIGNED))
        else:
            usuario = fabrica(app.pool_id, app.client_id, app.regiao, email.strip())
        usuario.authenticate(password=senha)
        return usuario.id_token, usuario.access_token, usuario.refresh_token

    try:
        id_token, access_token, refresh_token = await asyncio.to_thread(entrar)
    except Exception as e:  # noqa: BLE001 - toda falha vira mensagem fixa, sem a senha nem o texto da exceção
        raise ErroPiscina(_traduzir_erro_de_login(e)) from None
    _gravar_sessao(cfg, id_token, access_token, refresh_token, _expira_do_jwt(id_token))
    log.info("login na Tholz feito")


# ---------------------------------------------------------------- leitura

class Piscina:
    """Leitura da API da Tholz com a sua sessão. Só o endereço de data/piscina/app.json; só os caminhos de CAMINHOS."""

    def __init__(self, cfg: Config, *, transporte: httpx.AsyncBaseTransport | None = None, relogio=time.time):
        self.cfg = cfg
        self.app = ler_app(cfg)
        self.relogio = relogio
        self._http = httpx.AsyncClient(timeout=TIMEOUT, transport=transporte, follow_redirects=False)
        self._com_bearer = False  # descoberto na primeira leitura: o token vai puro ou como "Bearer ..."
        self._trava = asyncio.Lock()

    async def fechar(self) -> None:
        await self._http.aclose()

    async def _renovar(self, sessao: dict[str, Any]) -> dict[str, Any]:
        corpo = {"AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": self.app.client_id,
                 "AuthParameters": {"REFRESH_TOKEN": sessao["refresh_token"]}}
        try:
            r = await self._http.post(self.app.cognito_url, json=corpo, headers={
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                "Content-Type": "application/x-amz-json-1.1"})
        except httpx.HTTPError as e:
            raise ErroPiscina(f"Sem conexão com o login da Tholz ({type(e).__name__}).") from None
        if r.status_code >= 500 or r.status_code == 429:
            raise ErroPiscina(f"O login da Tholz está indisponível agora (HTTP {r.status_code}).")
        try:
            resultado = r.json().get("AuthenticationResult") if r.status_code == 200 else None
            id_token = resultado["IdToken"]
            access = resultado.get("AccessToken", sessao.get("access_token", ""))
        except (ValueError, KeyError, TypeError, AttributeError):
            apagar_sessao(self.cfg)  # o refresh token não vale mais: um login novo é o caminho
            raise SessaoVencida("A sessão da Tholz venceu: faça `vision piscina-login` de novo.") from None
        nova = {"id_token": id_token, "access_token": access, "refresh_token": sessao["refresh_token"],
                "expira": _expira_do_jwt(id_token)}
        _gravar_sessao(self.cfg, **nova)
        return nova

    async def _sessao_valida(self, forcar: bool = False) -> dict[str, Any]:
        sessao = ler_sessao(self.cfg)
        if sessao is None:
            raise SemLogin("Sem login na Tholz: faça `vision piscina-login`.")
        if forcar or sessao["expira"] - self.relogio() < FOLGA_RENOVAR_S:
            sessao = await self._renovar(sessao)
        return sessao

    async def _pedir(self, caminho: str, token: str, bearer: bool) -> httpx.Response:
        try:
            return await self._http.get(self.app.api_url + caminho,
                                        headers={"Authorization": f"Bearer {token}" if bearer else token})
        except httpx.HTTPError as e:
            raise ErroPiscina(f"A nuvem da Tholz não respondeu ({type(e).__name__}).") from None

    async def ler(self, o_que: str) -> Any:
        """O JSON de uma leitura de CAMINHOS ("dispositivos", "perfil"). Erros viram ErroPiscina."""
        caminhos = CAMINHOS.get(o_que)
        if caminhos is None:
            raise ErroPiscina("Leitura desconhecida.")
        async with self._trava:
            sessao = await self._sessao_valida()
            renovou = False
            for caminho in caminhos:
                for tentativa in range(3):
                    r = await self._pedir(caminho, sessao["id_token"], self._com_bearer)
                    if r.status_code in (401, 403):
                        if not renovou:  # 1º: pode ser só o token velho
                            sessao, renovou = await self._sessao_valida(forcar=True), True
                            continue
                        if not self._com_bearer and tentativa < 2:  # 2º: talvez a API queira "Bearer ..."
                            self._com_bearer = True
                            continue
                        raise SessaoVencida("A Tholz recusou a sessão: faça `vision piscina-login` de novo.")
                    break
                if r.status_code == 404:
                    continue  # tenta o próximo caminho conhecido
                if r.status_code != 200:
                    raise ErroPiscina(f"A nuvem da Tholz respondeu HTTP {r.status_code}.")
                try:
                    return r.json()
                except ValueError:
                    raise ErroPiscina("A nuvem da Tholz devolveu algo que não é JSON.") from None
            raise ErroPiscina("A nuvem da Tholz não conhece essa leitura (HTTP 404 em todos os caminhos).")


def forma(dados: Any, fundo: int = 4) -> Any:
    """O formato de um JSON sem os valores (para descobrir a estrutura sem imprimir dados da conta): chaves e tipos."""
    if fundo <= 0:
        return "..."
    if isinstance(dados, dict):
        return {str(k): forma(v, fundo - 1) for k, v in list(dados.items())[:40]}
    if isinstance(dados, list):
        return [f"{len(dados)} itens", forma(dados[0], fundo - 1)] if dados else []
    if isinstance(dados, bool):
        return "bool"
    if isinstance(dados, int | float):
        return "número"
    if isinstance(dados, str):
        return f"texto({len(dados)})"
    return "nulo" if dados is None else type(dados).__name__


# ---------------------------------------------------------------- comandos do terminal

async def login_interativo(cfg: Config) -> int:
    """`vision piscina-login`: pede e-mail e senha no terminal (a senha não aparece nem é guardada)."""
    import getpass

    try:
        ler_app(cfg)
        email = input("E-mail da conta Tholz: ").strip()
        senha = getpass.getpass("Senha (não aparece enquanto você digita): ")
        await login(cfg, email, senha)
    except ErroPiscina as e:
        print(e)
        return 1
    print("Conectado à Tholz. Teste com: vision piscina-ler")
    return 0


async def ler_interativo(cfg: Config) -> int:
    """`vision piscina-ler`: lê os dispositivos, guarda o JSON cru em data/piscina/ e mostra só o formato."""
    try:
        piscina = Piscina(cfg)
    except ErroPiscina as e:
        print(e)
        return 1
    try:
        dados = await piscina.ler("dispositivos")
    except ErroPiscina as e:
        print(e)
        return 1
    finally:
        await piscina.fechar()
    gravar_json(_pasta(cfg) / ARQUIVO_LEITURA, dados)
    print("Formato da resposta (valores omitidos):")
    print(json.dumps(forma(dados), ensure_ascii=False, indent=1))
    print(f"\nO JSON completo ficou em data/{PASTA}/{ARQUIVO_LEITURA} (só no seu PC, fora do git).")
    return 0
