"""Alexa: as luzes que a Alexa controla, pelo AlexaPy (API não oficial da Amazon).

O login é feito por você uma vez com `vision alexa-login`: o navegador abre a página de login da Amazon
passando por um proxy em 127.0.0.1 (o AlexaPy captura a sessão no fim). O Vision nunca vê nem guarda a
sua senha. O que fica em data/alexa/ (fora do git): os tokens da sessão e a lista de luzes com o nome
que elas têm no app da Alexa.

Nunca ligue `debug=True` no AlexaLogin nem o log do alexapy em DEBUG: ele passa a gravar páginas da Amazon
e a registrar tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import unicodedata
import webbrowser
from pathlib import Path
from typing import Any

from vision.config import Config

log = logging.getLogger(__name__)

PORTA_LOGIN = 8768
PRAZO_LOGIN_S = 600
CAMPOS_OAUTH = ("access_token", "refresh_token", "mac_dms", "expires_in", "code_verifier", "code_challenge",
                "authorization_code")


class SemLogin(RuntimeError):
    """Sem sessão da Alexa válida: rode `vision alexa-login`."""


def pasta(cfg: Config) -> Path:
    return cfg.dados / "alexa"


def _ler(arquivo: Path) -> Any:
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("%s ilegível", arquivo.name)
        return None


def _gravar(arquivo: Path, dados: Any) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    tmp = arquivo.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(dados, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, arquivo)


def tem_login(cfg: Config) -> bool:
    conta = _ler(pasta(cfg) / "conta.json")
    return bool(conta and conta.get("email") and (conta.get("oauth") or {}).get("refresh_token"))


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).replace("_", " ").split())


def _novo_login(cfg: Config, email: str, conta: dict[str, Any] | None = None):
    from alexapy import AlexaLogin

    base = pasta(cfg)
    base.mkdir(parents=True, exist_ok=True)

    def caminho(nome: str) -> str:
        p = base / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)

    conta = conta or {}
    return AlexaLogin(url=str(cfg.get("alexa.dominio", "amazon.com.br")), email=email, password="",
                      outputpath=caminho, oauth=conta.get("oauth") or {}, uuid=conta.get("uuid"), oauth_login=True)


def _guardar_conta(cfg: Config, login) -> None:
    _gravar(pasta(cfg) / "conta.json", {
        "email": login.email,
        "uuid": login.uuid,
        "oauth": {c: getattr(login, c, None) for c in CAMPOS_OAUTH if getattr(login, c, None) is not None},
    })


def falha_da_resposta(r: Any) -> str | None:
    """None se a Alexa aceitou o comando; senão, o motivo.

    `set_light_state` devolve o JSON cru do PUT /api/phoenix/state: None quando o HTTP falha; `errors` e,
    por item, `controlResponses[].code` quando a Amazon recusa. Formato sem o que esperamos vale como
    aceito só se não houver sinal nenhum de erro.
    """
    if r is None:
        return "sem resposta"
    if not isinstance(r, dict):
        return "resposta inesperada"
    erros = [e for e in (r.get("errors") or []) if e]
    if erros:
        e = erros[0]
        return str(e.get("code") or e.get("message") or e)[:120] if isinstance(e, dict) else str(e)[:120]
    for item in r.get("controlResponses") or []:
        codigo = str(item.get("code", "SUCCESS")) if isinstance(item, dict) else "SUCCESS"
        if codigo.upper() != "SUCCESS":
            return codigo[:120]
    return None


def _luzes_de(aparelhos: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    luzes = []
    for a in aparelhos or []:
        tipos = a.get("applianceTypes") or []
        if "LIGHT" in tipos and a.get("entityId") and a.get("friendlyName"):
            luzes.append({"nome": str(a["friendlyName"]), "entity_id": str(a["entityId"])})
    return sorted(luzes, key=lambda x: x["nome"])


class Alexa:
    """Uma sessão da Alexa aberta sob demanda e reaproveitada (a renovação dos tokens é gravada a cada vez)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.login = None
        self._trava = asyncio.Lock()

    async def _sessao(self):
        async with self._trava:
            if self.login is not None:
                return self.login
            conta = _ler(pasta(self.cfg) / "conta.json")
            if not conta or not conta.get("email"):
                raise SemLogin("a Alexa ainda não foi ligada: rode `vision alexa-login`")
            login = _novo_login(self.cfg, conta["email"], conta)
            try:
                await login.login(cookies=await login.load_cookie())
            except Exception as e:
                await login.close()  # sem deixar a sessão HTTP aberta sem dono
                raise RuntimeError(f"não consegui falar com a Alexa ({type(e).__name__})") from e
            if not (login.status or {}).get("login_successful"):
                await login.close()
                raise SemLogin("a sessão da Alexa venceu: rode `vision alexa-login`")
            _guardar_conta(self.cfg, login)
            self.login = login
            return login

    async def _descartar(self) -> None:
        if self.login is not None:
            await self.login.close()
            self.login = None

    def luzes(self) -> list[dict[str, str]]:
        return _ler(pasta(self.cfg) / "luzes.json") or []

    async def atualizar_luzes(self) -> list[dict[str, str]]:
        from alexapy import AlexaAPI

        login = await self._sessao()
        luzes = _luzes_de(await AlexaAPI.get_network_details(login))
        if luzes:
            _gravar(pasta(self.cfg) / "luzes.json", luzes)
        return luzes

    async def mudar_luz(self, entity_id: str, ligar: bool, brilho: int | None = None) -> None:
        from alexapy import AlexaAPI
        from alexapy.errors import AlexapyConnectionError, AlexapyLoginCloseRequested, AlexapyLoginError

        motivo = "sem resposta"
        for tentativa in (1, 2):
            login = await self._sessao()
            try:
                r = await AlexaAPI.set_light_state(login, entity_id, power_on=ligar, brightness=brilho)
            except (AlexapyLoginError, AlexapyLoginCloseRequested) as e:
                await self._descartar()  # sessão morta não fica guardada
                if tentativa == 2:
                    raise SemLogin("a sessão da Alexa venceu: rode `vision alexa-login`") from e
                continue
            except AlexapyConnectionError as e:
                await self._descartar()
                if tentativa == 2:
                    raise RuntimeError("a Alexa está fora do ar") from e
                continue
            motivo = falha_da_resposta(r)
            if motivo is None:
                _guardar_conta(self.cfg, login)
                return
            log.warning("a Alexa recusou o comando de luz: %s", motivo)
            await self._descartar()  # sessão velha: abre outra e tenta de novo
        raise RuntimeError(f"a Alexa não aceitou o comando ({motivo})")

    async def fechar(self) -> None:
        await self._descartar()


async def atualizar_lista(cfg: Config) -> int:
    """`vision alexa-luzes`: busca de novo as luzes na Alexa (depois de renomear no app), sem refazer o login."""
    a = Alexa(cfg)
    try:
        luzes = await a.atualizar_luzes()
    except SemLogin as e:
        print(f"{e}.")
        return 1
    finally:
        await a.fechar()
    print(f"Luzes na Alexa ({len(luzes)}):")
    for luz in luzes:
        print(f"  - {luz['nome']}")
    print("Reinicie o Vision para ele ver a lista nova." if luzes else "Nenhuma luz encontrada.")
    return 0


async def login_interativo(cfg: Config) -> int:
    """`vision alexa-login`: login da Amazon no navegador (pelo proxy local do AlexaPy)."""
    from alexapy import AlexaProxy
    from yarl import URL

    print("Ligar o Vision à sua Alexa. A senha você digita na página da Amazon; o Vision não a vê nem guarda.")
    email = input("E-mail da sua conta Amazon: ").strip()
    if "@" not in email:
        print("E-mail inválido.")
        return 1
    conta = _ler(pasta(cfg) / "conta.json") or {}
    login = _novo_login(cfg, email, conta if conta.get("email") == email else None)
    pronto = asyncio.Event()

    class Proxy(AlexaProxy):
        async def test_amazon_url(self, resp, data, query):
            resultado = await super().test_amazon_url(resp, data, query)
            if resultado is not None and URL(str(resp.url)).path in ("/ap/maplanding", "/spa/index.html"):
                pronto.set()
            return resultado

    proxy = Proxy(login, f"http://127.0.0.1:{PORTA_LOGIN}/")
    await proxy.start_proxy(host="127.0.0.1")  # só nesta máquina (o padrão do authcaptureproxy seria 0.0.0.0)
    try:
        endereco = str(proxy.access_url())
        print("\nAbrindo o navegador. Entre na Amazon normalmente (com o código de verificação, se pedir).")
        print(f"Se não abrir, acesse: {endereco}\n")
        webbrowser.open(endereco)
        await asyncio.wait_for(pronto.wait(), PRAZO_LOGIN_S)
    except TimeoutError:
        print("O login não foi concluído em 10 min. Rode `vision alexa-login` de novo.")
        await login.close()
        return 1
    finally:
        await proxy.stop_proxy()  # o proxy nunca fica no ar depois do login (nem se algo der errado)
    if not await login.test_loggedin():
        print("A Amazon não confirmou o login. Tente de novo.")
        await login.close()
        return 1
    _guardar_conta(cfg, login)
    alexa = Alexa(cfg)
    alexa.login = login
    try:
        luzes = await alexa.atualizar_luzes()
    finally:
        await alexa.fechar()
    print(f"\nPronto, Alexa ligada. Luzes encontradas ({len(luzes)}):")
    for luz in luzes:
        print(f"  - {luz['nome']}")
    if not luzes:
        print("  (nenhuma: confira no app da Alexa se as lâmpadas aparecem em Dispositivos)")
    print("Reinicie o Vision (bandeja → Sair, e abrir de novo) para ele usar.")
    return 0
