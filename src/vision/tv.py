"""TV Samsung pela rede (pedido de 05/10): uma Samsung de 2015 (Tizen, série J).

O que essa TV aceita, testado nela em 05/10:
- Teclas do controle: só pelo protocolo CRIPTOGRAFADO das séries H/J (porta 8000), com pareamento por PIN mostrado
  na tela (`vision tv-parear` ou Ajustes → Conexões). O websocket comum das TVs de 2016+ (porta 8001) conecta,
  mas recusa as teclas. A primeira tecla logo depois de conectar se perde: espera o aperto de mão antes.
- Volume e mudo exatos: UPnP RenderingControl (porta 9197), sem pareamento.
- Abrir app e vídeo do YouTube: DIAL (porta 8080). Com o YouTube fechado, ele abre na tela de perfis com o vídeo
  guardado; um OK escolhe o perfil marcado e o vídeo segue. NUNCA feche app pelo DIAL: fechar e abrir em
  sequência reiniciou a TV no teste.
- Tocar um link de mídia: UPnP AVTransport (porta 9197).
- Ligar: não dá (o Wi-Fi da TV desliga junto). Fica para outro meio (infravermelho, Broadlink): por isso as
  ferramentas falam com qualquer objeto com os métodos de `TVSamsung`.

Segurança: o endereço vem SÓ de `tv.ip` no config local (data/config-local.yaml, fora do git) e tem que ser da
rede local. Nada que o modelo, a web ou a memória digam vira host. O token do pareamento fica em
data/tv/sessao.json (fora do git) e nunca vai para log: a lib `samsungtvws` loga o token em INFO, então o
logger dela fica preso em WARNING aqui.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import time
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import httpx

from vision.arquivos import gravar_json, ler_json
from vision.config import Config

log = logging.getLogger(__name__)
logging.getLogger("samsungtvws").setLevel(logging.WARNING)  # em INFO ela grava o token e a sessão

PORTA_TECLAS = 8000
PORTA_PAREAR = 8080
PORTA_DIAL = 8080
PORTA_UPNP = 9197
PRAZO_S = 4.0  # cada pedido HTTP à TV
PRAZO_CONEXAO_S = 1.5  # TV desligada ou fora da rede: a resposta tem que ser rápida
PAUSA_PIN_VELHO_S = 2.0  # depois de fechar a tela de PIN velha, antes de abrir a nova
CONFIRMACAO_S = 2.5  # a TV responde cada tecla em instantes; sem resposta, a sessão do pareamento morreu
APERTO_DE_MAO_S = 1.0  # a 1ª tecla logo depois de conectar se perde (visto em 05/10)
INTERVALO_TECLAS_S = 0.4  # 3 de 3 teclas chegaram com 0,4 s e com 1 s
FOLGA_PERFIS_S = 8.0  # do YouTube "rodando" até a tela de perfis aceitar o OK (funcionou com 8 s)
PRAZO_APP_S = 20.0
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
NOME_APP = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
VOLUME_MAXIMO = 100


class TVInacessivel(RuntimeError):
    """A TV não respondeu: desligada ou fora da rede."""


class SemPareamento(RuntimeError):
    """Sem o pareamento das teclas (data/tv/sessao.json)."""


def pasta(cfg: Config) -> Path:
    return cfg.dados / "tv"


def arquivo_sessao(cfg: Config) -> Path:
    return pasta(cfg) / "sessao.json"


def ip_valido(valor: Any) -> str | None:
    """O IP, se for um IPv4 da rede local (192.168.x, 10.x, 172.16–31.x). Qualquer outra coisa: None."""
    try:
        ip = ipaddress.ip_address(str(valor).strip())
    except ValueError:
        return None
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast \
            or ip.is_unspecified:
        return None
    return str(ip)


def endereco(cfg: Config) -> str | None:
    return ip_valido(cfg.get("tv.ip") or "")


def ler_sessao(cfg: Config) -> dict[str, str] | None:
    d = ler_json(arquivo_sessao(cfg))
    if isinstance(d, dict) and re.fullmatch(r"[0-9a-fA-F]{32}", str(d.get("token") or "")) \
            and re.fullmatch(r"\d{1,12}", str(d.get("session_id") or "")):
        return {"token": str(d["token"]), "session_id": str(d["session_id"])}
    return None


def tecla_confirmada(sessao: Any, evento: str) -> bool:
    """A TV confirma cada tecla com {"api":"SendRemoteKey","result":{}}, cifrado com a sessão. Sessão morta: vazio."""
    m = re.search(r'"args":"\[([\d,]+)\]"', evento)
    if sessao is None or not m:
        return False
    try:
        aberto = sessao._decrypt(bytes(int(n) for n in m.group(1).split(",")).hex().encode())
    except (ValueError, TypeError):
        return False
    return '"SendRemoteKey"' in aberto


def tem_pareamento(cfg: Config) -> bool:
    return ler_sessao(cfg) is not None


def _corpo_soap(servico: str, acao: str, argumentos: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{acao} xmlns:u="urn:schemas-upnp-org:service:{servico}:1"><InstanceID>0</InstanceID>{argumentos}'
            f'</u:{acao}></s:Body></s:Envelope>')


class TVSamsung:
    """Fala só com `ip` (vem do config, validado por `ip_valido`). `sessao` = token e session_id do pareamento."""

    def __init__(self, ip: str, sessao: dict[str, str] | None, *, folga_perfis_s: float = FOLGA_PERFIS_S,
                 ok_no_perfil: bool = True):
        if ip_valido(ip) is None:
            raise ValueError("tv.ip tem que ser um IP da rede local")
        self.ip = ip
        self.sessao = sessao
        self.folga_perfis_s = folga_perfis_s
        self.ok_no_perfil = ok_no_perfil
        # Conexão curta (TV desligada responde rápido); a resposta pode demorar mais.
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(PRAZO_S, connect=PRAZO_CONEXAO_S),
                                       follow_redirects=False)
        self._trava = asyncio.Lock()  # um comando por vez: a TV de 2015 reinicia com pedidos em rajada
        self.ok_pendente: asyncio.Task | None = None  # o OK da tela de perfis, em segundo plano

    async def fechar(self) -> None:
        if self.ok_pendente is not None:
            self.ok_pendente.cancel()
        await self._http.aclose()

    # ------------------------------------------------------------------ alcance

    async def _porta_aberta(self, porta: int) -> bool:
        try:
            _, escrita = await asyncio.wait_for(asyncio.open_connection(self.ip, porta), PRAZO_CONEXAO_S)
        except (OSError, TimeoutError):
            return False
        escrita.close()
        with contextlib.suppress(OSError):
            await escrita.wait_closed()
        return True

    async def acessivel(self) -> bool:
        """Ligada = alguma das portas respondeu. A do UPnP demora a abrir numa TV recém-ligada (visto em 05/10)."""
        return any(await asyncio.gather(*(self._porta_aberta(p) for p in {PORTA_UPNP, PORTA_DIAL, PORTA_TECLAS})))

    async def _exigir(self) -> None:
        if not await self.acessivel():
            raise TVInacessivel("A TV não responde: deve estar desligada ou fora da rede.")

    # ------------------------------------------------------------------ teclas (protocolo criptografado)

    async def teclas(self, teclas: list[str], intervalo_s: float = INTERVALO_TECLAS_S) -> None:
        if self.sessao is None:
            raise SemPareamento("A TV ainda não foi pareada: rode `vision tv-parear` ou use Ajustes → Conexões.")
        async with self._trava:
            await self._exigir()
            await self._enviar_teclas(teclas, intervalo_s)

    async def _enviar_teclas(self, teclas: list[str], intervalo_s: float) -> None:
        import aiohttp
        from samsungtvws.encrypted.remote import (
            SamsungTVEncryptedWSAsyncRemote,
            SendRemoteKey,
        )

        assert self.sessao is not None
        from websockets.exceptions import ConnectionClosed

        class Remoto(SamsungTVEncryptedWSAsyncRemote):
            """Guarda o que a TV responde: uma sessão de pareamento morta recebe tecla e não reage a ela."""

            respostas: list[str]

            async def _do_start_listening(self, connection):
                with contextlib.suppress(ConnectionClosed):
                    while True:
                        self.respostas.append(str(await connection.recv()))

        async with aiohttp.ClientSession() as web:
            remoto = Remoto(self.ip, web_session=web, token=self.sessao["token"],
                            session_id=self.sessao["session_id"], port=PORTA_TECLAS,
                            timeout=PRAZO_S, key_press_delay=intervalo_s)
            remoto.respostas = []
            try:
                await remoto.start_listening()
                await asyncio.sleep(APERTO_DE_MAO_S)
                await remoto.send_commands([SendRemoteKey.click(t) for t in teclas])
                limite = time.monotonic() + CONFIRMACAO_S
                while not any(tecla_confirmada(remoto._session, r) for r in remoto.respostas):
                    if time.monotonic() > limite:
                        raise SemPareamento("A TV não reagiu às teclas: o pareamento deve ter vencido (acontece "
                                            "quando ela reinicia). Pareie de novo em Ajustes → Conexões.")
                    await asyncio.sleep(0.1)
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                log.warning("TV: teclas não foram (%s)", type(e).__name__)
                raise TVInacessivel("A TV não aceitou o comando (a conexão caiu).") from e
            finally:
                await remoto.close()

    # ------------------------------------------------------------------ volume e mudo (UPnP)

    async def _soap(self, servico: str, acao: str, argumentos: str = "") -> str:
        try:
            r = await self._http.post(
                f"http://{self.ip}:{PORTA_UPNP}/upnp/control/{servico}1",
                content=_corpo_soap(servico, acao, argumentos).encode("utf-8"),
                headers={"Content-Type": 'text/xml; charset="utf-8"',
                         "SOAPACTION": f'"urn:schemas-upnp-org:service:{servico}:1#{acao}"'})
        except httpx.HTTPError as e:
            raise TVInacessivel("A TV não responde: deve estar desligada ou fora da rede.") from e
        if r.status_code != 200:
            raise RuntimeError(f"a TV recusou {acao} (HTTP {r.status_code})")
        return r.text

    async def volume(self) -> int:
        texto = await self._soap("RenderingControl", "GetVolume", "<Channel>Master</Channel>")
        if not (m := re.search(r"<CurrentVolume>(\d+)</CurrentVolume>", texto)):
            raise RuntimeError("a TV não disse o volume")
        return int(m.group(1))

    async def definir_volume(self, nivel: int) -> int:
        nivel = max(0, min(VOLUME_MAXIMO, int(nivel)))
        async with self._trava:
            await self._soap("RenderingControl", "SetVolume",
                             f"<Channel>Master</Channel><DesiredVolume>{nivel}</DesiredVolume>")
        return nivel

    async def mudo(self) -> bool:
        texto = await self._soap("RenderingControl", "GetMute", "<Channel>Master</Channel>")
        return bool(re.search(r"<CurrentMute>(1|true)</CurrentMute>", texto, re.IGNORECASE))

    async def definir_mudo(self, ligado: bool) -> None:
        async with self._trava:
            await self._soap("RenderingControl", "SetMute",
                             f"<Channel>Master</Channel><DesiredMute>{1 if ligado else 0}</DesiredMute>")

    # ------------------------------------------------------------------ apps (DIAL)

    async def estado_app(self, nome: str) -> str | None:
        """"running", "stopped"... ou None se a TV não conhece o app."""
        if not NOME_APP.match(nome):
            raise ValueError("nome de app inválido")
        try:
            r = await self._http.get(f"http://{self.ip}:{PORTA_DIAL}/ws/apps/{nome}")
        except httpx.HTTPError as e:
            raise TVInacessivel("A TV não responde: deve estar desligada ou fora da rede.") from e
        if r.status_code == 404:
            return None
        m = re.search(r"<state>([^<]{1,40})</state>", r.text)
        return m.group(1).strip().lower() if r.status_code == 200 and m else None

    async def _abrir(self, nome: str, corpo: str = "") -> None:
        try:
            r = await self._http.post(f"http://{self.ip}:{PORTA_DIAL}/ws/apps/{nome}", content=corpo.encode("utf-8"),
                                      headers={"Content-Type": "text/plain; charset=utf-8"})
        except httpx.HTTPError as e:
            raise TVInacessivel("A TV não responde: deve estar desligada ou fora da rede.") from e
        if r.status_code not in (200, 201):
            raise RuntimeError(f"a TV não abriu {nome} (HTTP {r.status_code})")

    async def abrir_app(self, nome: str) -> None:
        async with self._trava:
            await self._exigir()
            if await self.estado_app(nome) is None:
                raise ValueError(f"a TV não tem o app {nome}")
            await self._abrir(nome)

    async def youtube(self, video_id: str) -> bool:
        """Abre o vídeo no YouTube da TV. Com o app fechado, espera ele subir e aperta OK na tela de perfis
        (o perfil marcado é o último usado), em segundo plano: a resposta não espera os ~12 s do app subir.
        Devolve True se vai apertar o OK."""
        if not VIDEO_ID.match(video_id):
            raise ValueError("ID de vídeo do YouTube inválido")
        async with self._trava:
            await self._exigir()
            antes = await self.estado_app("YouTube")
            if antes is None:
                raise ValueError("a TV não tem o app do YouTube")
            await self._abrir("YouTube", f"v={video_id}")
        if antes == "running" or not self.ok_no_perfil or self.sessao is None:
            return False
        if self.ok_pendente is not None:
            self.ok_pendente.cancel()  # pediu outro vídeo antes do OK do anterior: vale só o último
        self.ok_pendente = asyncio.get_running_loop().create_task(self._ok_no_perfil(), name="tv-ok-perfil")
        return True

    async def _ok_no_perfil(self) -> None:
        try:
            limite = time.monotonic() + PRAZO_APP_S
            while await self.estado_app("YouTube") != "running":  # nunca encadear sem esperar: a TV reinicia
                if time.monotonic() > limite:
                    log.info("TV: o YouTube não subiu a tempo; sem o OK do perfil")
                    return
                await asyncio.sleep(1)
            await asyncio.sleep(self.folga_perfis_s)
            async with self._trava:
                await self._enviar_teclas(["KEY_ENTER"], INTERVALO_TECLAS_S)
        except (TVInacessivel, ValueError, RuntimeError) as e:
            log.info("TV: OK do perfil não foi (%s)", type(e).__name__)

    # ------------------------------------------------------------------ mídia (UPnP AVTransport)

    async def tocar_midia(self, url: str) -> None:
        async with self._trava:
            await self._exigir()
            await self._soap("AVTransport", "SetAVTransportURI",
                             f"<CurrentURI>{escape(url)}</CurrentURI><CurrentURIMetaData></CurrentURIMetaData>")
            await asyncio.sleep(1)
            await self._soap("AVTransport", "Play", "<Speed>1</Speed>")


# ---------------------------------------------------------------------- pareamento


class Pareamento:
    """Os dois passos do PIN: `iniciar` mostra o PIN na TV; `confirmar` com o número digitado grava a sessão."""

    def __init__(self, cfg: Config, ip: str):
        if ip_valido(ip) is None:
            raise ValueError("o IP da TV tem que ser da rede local (ex.: 192.168.0.50)")
        self.cfg = cfg
        self.ip = ip_valido(ip) or ""
        self.quando = time.monotonic()
        self._web = None
        self._auth = None

    async def iniciar(self) -> None:
        import aiohttp
        from samsungtvws.encrypted.authenticator import (
            SamsungTVEncryptedWSAsyncAuthenticator,
        )

        self._web = aiohttp.ClientSession()
        self._auth = SamsungTVEncryptedWSAsyncAuthenticator(self.ip, web_session=self._web, port=PORTA_PAREAR,
                                                            timeout=PRAZO_S)
        try:
            # Uma tela de PIN de tentativa anterior faz a TV reaproveitar o PIN velho e expirar (visto em 05/10).
            with contextlib.suppress(aiohttp.ClientError, OSError, TimeoutError):
                await self._auth._close_pin_page_on_tv()
                await asyncio.sleep(PAUSA_PIN_VELHO_S)
            await self._auth.start_pairing()
        except (aiohttp.ClientError, OSError, TimeoutError) as e:
            await self.fechar()
            raise TVInacessivel("A TV não respondeu ao pareamento: confira se ela está ligada e o IP.") from e

    async def confirmar(self, pin: str) -> bool:
        """True: pareada (sessão gravada). False: PIN recusado (dá para tentar de novo)."""
        import aiohttp
        from samsungtvws.encrypted.authenticator import SamsungTVEncryptedError

        if self._auth is None:
            raise RuntimeError("pareamento não iniciado")
        pin = "".join(c for c in str(pin) if c.isdigit())
        if len(pin) != 4:
            return False
        try:
            token = await self._auth.try_pin(pin)
            if token is None:
                return False
            session_id = await self._auth.get_session_id_and_close()
        except (SamsungTVEncryptedError, ValueError) as e:  # ValueError: a lib erra a conta da chave às vezes
            await self.fechar()
            raise RuntimeError("a TV encerrou o pareamento: comece de novo") from e
        except (aiohttp.ClientError, OSError, TimeoutError) as e:
            await self.fechar()
            raise TVInacessivel("A TV parou de responder no meio do pareamento: comece de novo.") from e
        gravar_json(arquivo_sessao(self.cfg), {"token": str(token), "session_id": str(session_id)})
        await self.fechar()
        return True

    async def fechar(self) -> None:
        if self._web is not None:
            await self._web.close()
            self._web = None


async def parear_interativo(cfg: Config, ip: str | None = None, ler=input, avisar=print) -> int:
    """`vision tv-parear`: mostra o PIN na TV e pede para digitar aqui. Grava o IP no config local."""
    from vision.config import salvar_ajustes

    ip = ip or endereco(cfg) or ler("IP da TV na sua rede (ex.: 192.168.0.50): ")
    if ip_valido(ip) is None:
        avisar("IP inválido: tem que ser da rede local (ex.: 192.168.0.50).")
        return 1
    par = Pareamento(cfg, ip)
    try:
        await par.iniciar()
        avisar("A TV deve estar mostrando um PIN de 4 dígitos.")
        for _ in range(3):
            if await par.confirmar(await asyncio.to_thread(ler, "PIN: ")):
                salvar_ajustes(cfg, {"tv.ip": par.ip})
                avisar("TV pareada.")
                return 0
            avisar("PIN recusado. Tente de novo.")
        avisar("Três PINs recusados: rode o comando de novo.")
        return 1
    except (TVInacessivel, RuntimeError) as e:
        avisar(str(e))
        return 1
    finally:
        await par.fechar()
