"""Spotify pela API oficial, com uma API falsa (httpx.MockTransport): nada sai da máquina."""

import asyncio
import base64
import hashlib
import json
import time

import httpx
import pytest
from fakes.llm_falso import LLMFalso

from vision import spotify
from vision.brain.agent import Agente
from vision.tools.base import Registro
from vision.tools.spotify import Musica, comando_de_musica

FAIXA = {"uri": "spotify:track:1", "name": "Bohemian Rhapsody", "artists": [{"name": "Queen"}]}


class ApiFalsa:
    """`carrega_parado`: o Spotify do PC carrega a música avulsa e fica parado até outro play (visto em 02/10).
    `nunca_toca`: nem com o segundo play."""

    def __init__(self, dispositivos_depois=1, status_play=204, expirar_token=False, carrega_parado=False,
                 nunca_toca=False, volume=50, suporta_volume=True):
        self.chamadas: list[tuple[str, str, dict]] = []
        self.dispositivos_vazios = dispositivos_depois  # quantas consultas sem dispositivo antes de aparecer um
        self.status_play = status_play
        self.expirar_token = expirar_token
        self.renovacoes = 0
        self.carrega_parado = carrega_parado
        self.nunca_toca = nunca_toca
        self.volume = volume
        self.suporta_volume = suporta_volume
        self.player: dict | None = None  # o que o /me/player devolve (None = nada ativo)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        corpo = json.loads(req.content) if req.content and req.headers.get("content-type") == "application/json" else {}
        self.chamadas.append((req.method, req.url.path, corpo))
        if req.url.path == "/api/token":
            self.renovacoes += 1
            return httpx.Response(200, json={"access_token": f"novo{self.renovacoes}", "expires_in": 3600,
                                             "refresh_token": f"r{self.renovacoes}"})
        if self.expirar_token and req.headers["Authorization"] == "Bearer velho":
            return httpx.Response(401)
        if req.url.path == "/v1/search":
            return httpx.Response(200, json={"tracks": {"items": [FAIXA]},
                                             "playlists": {"items": [None, {"uri": "spotify:playlist:9",
                                                                             "name": "Foco"}]}})
        if req.url.path == "/v1/me/player/devices":
            if self.dispositivos_vazios > 0:
                self.dispositivos_vazios -= 1
                return httpx.Response(200, json={"devices": []})
            return httpx.Response(200, json={"devices": [{"id": "pc1", "name": "DESKTOP", "type": "Computer",
                                                          "is_active": False}]})
        if req.url.path == "/v1/me/player/play":
            if self.status_play in (200, 202, 204):
                if corpo:  # pedido novo: carrega a música (ou o contexto)
                    item = FAIXA if "uris" in corpo else {"uri": "spotify:track:da-playlist", "name": "X"}
                    contexto = {"uri": corpo["context_uri"]} if "context_uri" in corpo else None
                    self.player = {"is_playing": not (self.carrega_parado or self.nunca_toca), "item": item,
                                   "context": contexto, "device": self._aparelho()}
                elif self.player is not None and not self.nunca_toca:  # play sem corpo: continua
                    self.player["is_playing"] = True
            return httpx.Response(self.status_play)
        if req.url.path == "/v1/me/player":
            return httpx.Response(200, json=self.player) if self.player else httpx.Response(204)
        if req.url.path == "/v1/me/player/volume":
            self.volume = int(req.url.params["volume_percent"])
            if self.player:
                self.player["device"] = self._aparelho()
            return httpx.Response(204)
        if req.url.path == "/v1/me/player/currently-playing":
            return httpx.Response(200, json={"is_playing": True, "item": FAIXA})
        return httpx.Response(204)

    def _aparelho(self) -> dict:
        return {"id": "pc1", "name": "DESKTOP", "type": "Computer", "volume_percent": self.volume,
                "supports_volume": self.suporta_volume}


@pytest.fixture
def com_login(cfg):
    spotify.gravar(cfg, {"client_id": "cid", "access_token": "valido", "refresh_token": "r0",
                         "expira": time.time() + 3000})
    return cfg


def _sp(cfg, api, abertos=None):
    async def nao_dorme(_s):
        await asyncio.sleep(0)

    return spotify.Spotify(cfg, abrir_app=(abertos.append if abertos is not None else lambda _x: None),
                           transporte=httpx.MockTransport(api), dormir=nao_dorme)


async def test_toca_abrindo_o_spotify_se_nao_ha_dispositivo(com_login):
    api, abertos = ApiFalsa(dispositivos_depois=2), []
    async with _sp(com_login, api, abertos) as sp:
        assert await sp.tocar("bohemian rhapsody") == "Bohemian Rhapsody, de Queen"
    assert abertos == ["spotify:"]  # estava fechado: abriu e esperou aparecer
    play = [c for c in api.chamadas if c[1] == "/v1/me/player/play"]
    assert play == [("PUT", "/v1/me/player/play", {"uris": ["spotify:track:1"]})]


async def test_playlist_vai_como_contexto_e_ignora_item_nulo(com_login):
    api = ApiFalsa(dispositivos_depois=0)
    async with _sp(com_login, api) as sp:
        assert await sp.tocar("foco", "playlist") == "a playlist Foco"
    assert ("PUT", "/v1/me/player/play", {"context_uri": "spotify:playlist:9"}) in api.chamadas


async def test_sem_premium(com_login):
    async with _sp(com_login, ApiFalsa(dispositivos_depois=0, status_play=403)) as sp:
        with pytest.raises(spotify.ErroSpotify, match="Premium"):
            await sp.tocar("x")


async def test_spotify_que_nao_aparece_vira_mensagem(com_login, monkeypatch):
    monkeypatch.setattr(spotify, "ESPERA_DISPOSITIVO_S", 0.05)
    api = ApiFalsa(dispositivos_depois=10_000)

    async def dorme_pouco(_s):
        await asyncio.sleep(0.01)

    sp = spotify.Spotify(com_login, abrir_app=lambda _x: None, transporte=httpx.MockTransport(api), dormir=dorme_pouco)
    with pytest.raises(spotify.ErroSpotify, match="não apareceu"):
        await sp.tocar("x")
    await sp.fechar()


async def test_token_vencido_e_renovado_e_o_novo_refresh_fica_gravado(cfg):
    spotify.gravar(cfg, {"client_id": "cid", "access_token": "velho", "refresh_token": "r0", "expira": 0})
    api = ApiFalsa(dispositivos_depois=0)
    async with _sp(cfg, api) as sp:
        assert await sp.tocando() == "Tocando: Bohemian Rhapsody, de Queen."
    salvo = spotify.ler(cfg)
    assert salvo["access_token"] == "novo1" and salvo["refresh_token"] == "r1"  # o refresh antigo morreu


async def test_401_renova_uma_vez(cfg):
    spotify.gravar(cfg, {"client_id": "cid", "access_token": "velho", "refresh_token": "r0",
                         "expira": time.time() + 3000})
    api = ApiFalsa(dispositivos_depois=0, expirar_token=True)
    async with _sp(cfg, api) as sp:
        await sp.tocando()
    assert api.renovacoes == 1


async def test_sem_login(cfg):
    assert not spotify.tem_login(cfg)
    async with _sp(cfg, ApiFalsa()) as sp:
        with pytest.raises(spotify.SemLogin):
            await sp.tocando()


def test_pkce():
    verificador, desafio = spotify._pkce()
    assert 43 <= len(verificador) <= 128
    esperado = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    assert desafio == esperado


async def test_retorno_do_login_so_vale_com_o_state_certo():
    recebido = asyncio.get_running_loop().create_future()
    servidor = await asyncio.start_server(spotify._atender(recebido, "certo"), "127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]
    async with httpx.AsyncClient() as http:
        await http.get(f"http://127.0.0.1:{porta}/callback?code=abc&state=errado")
        assert not recebido.done()  # uma aba qualquer não conclui o login
        await http.get(f"http://127.0.0.1:{porta}/callback?code=abc&state=certo")
    servidor.close()
    assert await recebido == "abc"


@pytest.mark.parametrize("frase,esperado", [
    ("Toca Bohemian Rhapsody no Spotify.", {"busca": "bohemian rhapsody", "tipo": "musica"}),
    ("toca a playlist Foco", {"busca": "foco", "tipo": "playlist"}),
    ("Vision, toca músicas do Queen", {"busca": "queen", "tipo": "artista"}),
    ("coloca o álbum Thriller no spotify", {"busca": "thriller", "tipo": "album"}),
    ("toca a música Envolver", {"busca": "envolver", "tipo": "musica"}),
    ("toca no assunto da reunião", None),
    ("toca uma música", None),
    ("não toca nada", None),
    ("coloca um timer de 5 minutos", None),
    ("toca isso amanhã", None),
])
def test_comando_de_musica(frase, esperado):
    assert comando_de_musica(frase) == esperado


async def test_toca_sem_passar_pelo_modelo(com_login):
    api = ApiFalsa(dispositivos_depois=0)
    sp = _sp(com_login, api)
    musica = Musica(sp)
    r = Registro()
    r.adicionar(*musica.ferramentas())
    agente = Agente(LLMFalso([]), r, None)  # sem roteiro: se fosse ao modelo, quebraria
    agente.atalhos = [musica.atalho]
    resp = await agente.responder("toca Bohemian Rhapsody no Spotify", "voz", "t")
    assert resp.texto == "Tocando Bohemian Rhapsody, de Queen."
    await sp.fechar()


def test_musica_pedida_por_texto_de_fora_pergunta(com_login):
    ferr = {f.nome: f for f in Musica(None).ferramentas()}
    assert ferr["musica_tocar"].confirmar_se_externo and not ferr["musica_tocar"].sensivel


@pytest.mark.parametrize("frase,sim", [
    ("Que música é essa?", True), ("qual música está tocando?", True), ("Vision, que música tá tocando agora?", True),
    ("qual sua música favorita?", False), ("que música você recomenda?", False),
])
def test_pergunta_o_que_toca(frase, sim):
    from vision.tools.spotify import pergunta_o_que_toca

    assert pergunta_o_que_toca(frase) is sim


async def test_que_musica_e_essa_pergunta_ao_spotify(com_login):
    sp = _sp(com_login, ApiFalsa(dispositivos_depois=0))
    musica = Musica(sp)
    agente = Agente(LLMFalso([]), Registro(), None)
    agente.atalhos = [musica.atalho]
    resp = await agente.responder("Que música é essa?", "voz", "t")
    assert resp.texto == "Tocando: Bohemian Rhapsody, de Queen."
    await sp.fechar()


# ---------------------------------------------------------------------- revisão do PR 21

@pytest.mark.parametrize("frase", ["toca a próxima", "toca a música anterior", "toca a campainha",
                                   "toca o telefone", "toca a seguinte"])
def test_atalho_nao_confunde_pular_nem_campainha(frase):
    assert comando_de_musica(frase) is None


@pytest.mark.parametrize("frase,musica", [
    ("Toca Bohemian Rhapsody", True), ("Vision, toca Anitta", True), ("coloca Thriller no Spotify", True),
    ("Isso me toca bastante", False), ("o sino toca às 7", False), ("o álbum caiu no chão", False),
    ("que música está tocando?", True),
])
def test_intencao_de_musica_so_em_pedido(frase, musica):
    from vision.brain import intencao

    assert ("musica" in intencao.detectar(frase)) is musica


async def test_sem_internet_na_renovacao_vira_mensagem_no_atalho(cfg):
    spotify.gravar(cfg, {"client_id": "cid", "access_token": "velho", "refresh_token": "r0", "expira": 0})

    def sem_rede(req):
        raise httpx.ConnectError("offline", request=req)

    sp = spotify.Spotify(cfg, abrir_app=lambda _x: None, transporte=httpx.MockTransport(sem_rede))
    agente = Agente(LLMFalso([]), Registro(), None)
    agente.atalhos = [Musica(sp).atalho]
    resp = await agente.responder("toca Bohemian Rhapsody", "voz", "t")
    assert "não respondeu" in resp.texto
    await sp.fechar()


async def test_tipo_com_acento(com_login):
    api = ApiFalsa(dispositivos_depois=0)
    sp = _sp(com_login, api)
    await Musica(sp).tocar({"busca": "foco", "tipo": "Playlist"})
    assert ("PUT", "/v1/me/player/play", {"context_uri": "spotify:playlist:9"}) in api.chamadas
    await sp.fechar()


class ApiComEcho(ApiFalsa):
    """O Echo Dot é o último aparelho ativo, como em 02/10; o PC também está ligado (ou não)."""

    def __init__(self, com_pc=True, **kw):
        super().__init__(dispositivos_depois=0, **kw)
        self.com_pc = com_pc

    def __call__(self, req):
        if req.url.path == "/v1/me/player/devices":
            self.chamadas.append((req.method, req.url.path, {}))
            aparelhos = [{"id": "echo1", "name": "Echo Dot", "type": "Speaker", "is_active": True}]
            if self.com_pc:
                aparelhos.append({"id": "pc1", "name": "DESKTOP-X", "type": "Computer", "is_active": False})
            return httpx.Response(200, json={"devices": aparelhos})
        return super().__call__(req)


def _onde_tocou(api):
    return [c for c in api.chamadas if c[1] == "/v1/me/player/play"]


async def test_padrao_e_o_pc_mesmo_com_o_echo_ativo(com_login):
    api = ApiComEcho()
    async with _sp(com_login, api) as sp:
        assert await sp.tocar("bohemian rhapsody") == "Bohemian Rhapsody, de Queen"
        disp = await sp.dispositivo()
    assert disp["id"] == "pc1"


async def test_pc_fechado_abre_o_spotify_em_vez_de_tocar_no_echo(com_login, monkeypatch):
    monkeypatch.setattr(spotify, "ESPERA_DISPOSITIVO_S", 0.05)
    api, abertos = ApiComEcho(com_pc=False), []

    async def dorme_pouco(_s):
        await asyncio.sleep(0.01)

    sp = spotify.Spotify(com_login, abrir_app=abertos.append, transporte=httpx.MockTransport(api), dormir=dorme_pouco)
    with pytest.raises(spotify.ErroSpotify, match="não apareceu"):
        await sp.tocar("x")
    await sp.fechar()
    assert abertos == ["spotify:"] and not _onde_tocou(api)  # nunca tocou no Echo


async def test_na_alexa_so_quando_pedido(com_login):
    api = ApiComEcho()
    async with _sp(com_login, api) as sp:
        assert await sp.tocar("bohemian rhapsody", onde="alexa") == "Bohemian Rhapsody, de Queen (Echo Dot)"
        assert (await sp.dispositivo("echo"))["id"] == "echo1"
        with pytest.raises(spotify.ErroSpotify, match="Não achei 'celular'"):
            await sp.dispositivo("celular")


def test_atalho_entende_o_aparelho_no_fim():
    assert comando_de_musica("toca Queen na Alexa") == {"busca": "queen", "tipo": "musica", "onde": "alexa"}
    assert comando_de_musica("toca a playlist foco no echo dot") == {"busca": "foco", "tipo": "playlist",
                                                                     "onde": "echo dot"}
    assert comando_de_musica("toca garota de ipanema") == {"busca": "garota de ipanema", "tipo": "musica"}
    assert comando_de_musica("toca na alexa") is None


async def test_ferramenta_no_pc_e_o_padrao(com_login):
    api = ApiComEcho()
    async with _sp(com_login, api) as sp:
        assert await Musica(sp).tocar({"busca": "queen", "onde": "no PC"}) == "Tocando Bohemian Rhapsody, de Queen."



async def test_confirmacao_diz_o_aparelho_e_nome_curto_nao_casa(com_login):
    api = ApiComEcho()
    async with _sp(com_login, api) as sp:
        m = Musica(sp)
        assert await m.descrever_tocar({"busca": "Queen", "onde": "alexa"}) == "Vou tocar no Spotify: Queen, em alexa."
        assert await m.descrever_tocar({"busca": "Queen"}) == "Vou tocar no Spotify do PC: Queen."
    assert spotify.achar_aparelho([{"name": "Echo Dot", "type": "Speaker"}], "na") is None


def test_hostname_vence_outro_computador(monkeypatch):
    monkeypatch.setattr(spotify.socket, "gethostname", lambda: "MEU-PC")
    aparelhos = [{"id": "nb", "name": "Notebook", "type": "Computer", "is_active": True},
                 {"id": "pc", "name": "MEU-PC", "type": "Computer", "is_active": False}]
    assert spotify.Spotify._escolher(aparelhos)["id"] == "pc"


# ------------------------------------------------------------------ 02/10: tocar de verdade e volume


async def test_musica_carregada_e_parada_ganha_outro_play(com_login):
    """"Ele para a que está tocando mas não segue": o PC carrega a faixa e fica parado. O Vision confere e dá play."""
    api = ApiFalsa(dispositivos_depois=0, carrega_parado=True)
    async with _sp(com_login, api) as sp:
        assert await sp.tocar("bohemian rhapsody") == "Bohemian Rhapsody, de Queen"
    plays = [c for c in api.chamadas if c[1] == "/v1/me/player/play"]
    assert plays == [("PUT", "/v1/me/player/play", {"uris": ["spotify:track:1"]}),
                     ("PUT", "/v1/me/player/play", {})]  # o segundo, sem corpo, só continua
    assert api.player["is_playing"]


async def test_se_nao_comecar_avisa_em_vez_de_dizer_tocando(com_login):
    api = ApiFalsa(dispositivos_depois=0, nunca_toca=True)
    async with _sp(com_login, api) as sp:
        with pytest.raises(spotify.ErroSpotify, match="não começou"):
            await sp.tocar("bohemian rhapsody")
    assert len([c for c in api.chamadas if c[1] == "/v1/me/player/play"]) == 2  # um play de novo, não um laço


async def test_tocando_de_primeira_nao_manda_play_de_novo(com_login):
    api = ApiFalsa(dispositivos_depois=0)
    async with _sp(com_login, api) as sp:
        await sp.tocar("foco", "playlist")
    assert len([c for c in api.chamadas if c[1] == "/v1/me/player/play"]) == 1


async def test_volume_do_spotify_le_e_muda(com_login):
    api = ApiFalsa(dispositivos_depois=0, volume=40)
    async with _sp(com_login, api) as sp:
        assert await sp.volume_atual() is None  # nada ativo na conta
        await sp.tocar("bohemian rhapsody")
        assert await sp.volume_atual() == {"volume": 40, "tocando": True, "aparelho": "DESKTOP", "id": "pc1"}
        await sp.mudar_volume(130, "pc1")  # fora da faixa: vira 100
        assert api.volume == 100
    api = ApiFalsa(dispositivos_depois=0, suporta_volume=False)
    async with _sp(com_login, api) as sp:
        await sp.tocar("bohemian rhapsody")
        assert await sp.volume_atual() is None  # aparelho que não deixa mudar volume pela API
