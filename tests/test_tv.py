"""TV Samsung (vision/tv.py e vision/tools/tv.py), com a TV falsa: nada aqui fala com a TV de verdade."""

import asyncio
import logging

import httpx
import pytest
import respx
from fakes.llm_falso import LLMFalso, chama, fala

from vision import conexoes, config
from vision import tv as modulo_tv
from vision.arquivos import gravar_json
from vision.brain.agent import Agente
from vision.timers import Timers
from vision.tools.base import ErroFerramenta, Registro
from vision.tools.timer import Temporizador, fazer_acao
from vision.tools.tv import (
    ACOES_DE_TIMER,
    TV,
    comando_de_tv,
    escolher_video,
    id_do_youtube,
)

IP = "192.168.0.50"
SESSAO = {"token": "0123456789abcdef0123456789abcdef", "session_id": "7"}


class TVFalsa:
    """Grava o que pediram; `ligada=False` faz tudo responder como TV desligada."""

    def __init__(self, ligada=True, volume=7, sessao=SESSAO):
        self.ligada = ligada
        self.vol = volume
        self.mutada = False
        self.sessao = sessao
        self.feitas: list = []
        self.apps = {"YouTube": "stopped", "Netflix": "stopped"}

    def _ligada(self):
        if not self.ligada:
            raise modulo_tv.TVInacessivel("A TV não responde: deve estar desligada ou fora da rede.")

    async def acessivel(self):
        return self.ligada

    async def teclas(self, teclas):
        self._ligada()
        if self.sessao is None:
            raise modulo_tv.SemPareamento("A TV ainda não foi pareada.")
        self.feitas.append(("teclas", list(teclas)))

    async def volume(self):
        self._ligada()
        return self.vol

    async def definir_volume(self, n):
        self._ligada()
        self.vol = n
        self.feitas.append(("volume", n))
        return n

    async def mudo(self):
        return self.mutada

    async def definir_mudo(self, ligado):
        self._ligada()
        self.mutada = ligado
        self.feitas.append(("mudo", ligado))

    async def estado_app(self, nome):
        self._ligada()
        return self.apps.get(nome)

    async def abrir_app(self, nome):
        self._ligada()
        self.feitas.append(("app", nome))

    async def youtube(self, video_id):
        self._ligada()
        self.feitas.append(("youtube", video_id))
        return True

    async def tocar_midia(self, url):
        self._ligada()
        self.feitas.append(("midia", url))


# ------------------------------------------------------------------ endereço e sessão


@pytest.mark.parametrize("valor, esperado", [
    ("192.168.0.50", "192.168.0.50"), (" 10.0.0.5 ", "10.0.0.5"), ("172.20.1.1", "172.20.1.1"),
    ("8.8.8.8", None), ("127.0.0.1", None), ("169.254.1.1", None), ("0.0.0.0", None), ("tv.local", None),
    ("192.168.0.50:8000", None), ("http://192.168.0.50", None), ("fd00::1", None), ("", None),
])
def test_so_aceita_ip_da_rede_local(valor, esperado):
    assert modulo_tv.ip_valido(valor) == esperado


def test_ip_de_fora_no_config_local_e_ignorado():
    assert config._ip_local("192.168.0.50") and not config._ip_local("8.8.8.8")
    assert not config._ip_local("example.com") and not config._ip_local(None)


def test_tv_samsung_recusa_host_que_nao_e_da_rede_local():
    with pytest.raises(ValueError):
        modulo_tv.TVSamsung("8.8.8.8", None)


def test_sessao_estragada_ou_ausente_e_sem_pareamento(cfg):
    assert modulo_tv.ler_sessao(cfg) is None
    gravar_json(modulo_tv.arquivo_sessao(cfg), {"token": "curto", "session_id": "1"})
    assert not modulo_tv.tem_pareamento(cfg)
    gravar_json(modulo_tv.arquivo_sessao(cfg), SESSAO)
    assert modulo_tv.ler_sessao(cfg) == SESSAO and not modulo_tv.arquivo_sessao(cfg).with_suffix(".tmp").exists()


def test_o_log_da_lib_que_mostra_o_token_fica_em_warning():
    assert logging.getLogger("samsungtvws").getEffectiveLevel() >= logging.WARNING


# ------------------------------------------------------------------ ferramentas


async def test_volume_sobe_e_desce_pelo_valor_exato_e_respeita_o_maximo():
    t = TVFalsa(volume=7)
    tv = TV(t, volume_maximo=12)
    assert await tv.controle({"acao": "volume_mais", "vezes": 3}) == "Volume da TV em 10."
    assert "máximo" in await tv.controle({"acao": "volume_mais", "vezes": 9})
    assert t.vol == 12
    assert await tv.controle({"acao": "volume_menos", "vezes": 20}) == "Volume da TV em 0."
    with pytest.raises(ErroFerramenta, match="no máximo em 12"):
        await tv.volume({"nivel": 80})
    assert await tv.volume({"nivel": 6}) == "Volume da TV em 6." and t.vol == 6


async def test_teclas_fixas_e_vezes_so_onde_faz_sentido():
    t = TVFalsa()
    tv = TV(t)
    await tv.controle({"acao": "desligar", "vezes": 5})
    await tv.controle({"acao": "baixo", "vezes": 3})
    await tv.controle({"acao": "mudo"})
    assert t.feitas == [("teclas", ["KEY_POWER"]), ("teclas", ["KEY_DOWN"] * 3), ("mudo", True)]
    for ruim in ("KEY_POWER", "abre o navegador", ""):
        with pytest.raises(ErroFerramenta, match="desconhecida"):
            await tv.controle({"acao": ruim})
    with pytest.raises(ErroFerramenta, match="1 a 20"):
        await tv.controle({"acao": "cima", "vezes": 500})


async def test_ligar_explica_que_nao_da_pela_rede():
    with pytest.raises(ErroFerramenta, match="ligue pelo controle"):
        await TV(TVFalsa()).controle({"acao": "ligar"})


async def test_canal_por_numero():
    t = TVFalsa()
    assert await TV(t).canal({"numero": "13"}) == "Canal 13."
    assert t.feitas == [("teclas", ["KEY_1", "KEY_3", "KEY_ENTER"])]
    with pytest.raises(ErroFerramenta):
        await TV(t).canal({"numero": "um"})


async def test_tv_desligada_responde_claro_e_sugere_o_controle():
    tv = TV(TVFalsa(ligada=False))
    with pytest.raises(ErroFerramenta, match="desligada ou fora da rede.*controle"):
        await tv.controle({"acao": "volume_mais"})
    assert await tv.status({}) == "A TV está desligada ou fora da rede."


async def test_sem_pareamento_as_teclas_avisam_mas_volume_funciona():
    tv = TV(TVFalsa(sessao=None))
    with pytest.raises(ErroFerramenta, match="pareada"):
        await tv.controle({"acao": "ok"})
    assert await tv.controle({"acao": "volume_menos"}) == "Volume da TV em 6."


async def test_status_e_apps_so_da_lista():
    t = TVFalsa()
    t.apps["YouTube"] = "running"
    tv = TV(t, apps=["YouTube", "Netflix", "../../x"])
    assert tv.apps == ["YouTube", "Netflix"]
    assert await tv.status({}) == "A TV está ligada, volume 7, aberto: YouTube."
    assert await tv.abrir_app({"app": "netflix"}) == "Abri o Netflix na TV."
    with pytest.raises(ErroFerramenta, match="não está na lista"):
        await tv.abrir_app({"app": "http://evil"})


# ------------------------------------------------------------------ YouTube

# O que o DuckDuckGo devolveu para "blank space taylor swift" em 05/10 (encurtado).
ACHADOS = [
    {"title": "Taylor Swift Blank Space Music Video", "content": "https://www.tiktok.com/@x/video/7393941191684869382",
     "publisher": "TikTok", "duration": "0:20", "statistics": {"viewCount": 501400}},
    {"title": "Taylor Swift - Blank Space", "content": "https://www.youtube.com/watch?v=e-ORhEE9VVg",
     "publisher": "YouTube", "duration": "4:33", "statistics": {"viewCount": 3854974770}},
    {"title": "Blank Space (Lyric Video)", "content": "https://www.youtube.com/watch?v=gir8BEqAutk",
     "publisher": "YouTube", "duration": "3:57", "statistics": {"viewCount": 28201561}},
    {"title": "Eras Tour pt2", "content": "https://www.youtube.com/shorts/gHBJ4GHpFLI", "publisher": "YouTube",
     "duration": "1:01", "statistics": {"viewCount": 9_999_999_999}},
]


@pytest.mark.parametrize("url, esperado", [
    ("https://youtu.be/dQw4w9WgXcQ?si=abc123", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=e-ORhEE9VVg&t=3", "e-ORhEE9VVg"),
    ("https://m.youtube.com/shorts/gHBJ4GHpFLI", "gHBJ4GHpFLI"),
    ("https://youtube.com.evil.com/watch?v=e-ORhEE9VVg", None),
    ("https://www.youtube.com/watch?v=curto", None), ("blank space", None),
])
def test_id_do_youtube(url, esperado):
    assert id_do_youtube(url) == esperado


def test_escolhe_o_clipe_oficial_e_nao_o_short_nem_o_tiktok():
    assert escolher_video(ACHADOS)["id"] == "e-ORhEE9VVg"
    assert escolher_video(ACHADOS[:1]) is None


async def test_tocar_no_youtube_busca_e_toca_o_mais_certo():
    t = TVFalsa()
    buscas = []
    tv = TV(t, buscar=lambda c: buscas.append(c) or ACHADOS)
    resposta = await tv.youtube({"busca": "blank space"})
    assert t.feitas == [("youtube", "e-ORhEE9VVg")] and buscas == ["blank space"]
    assert "Taylor Swift - Blank Space" in resposta
    await tv.youtube({"busca": "https://youtu.be/dQw4w9WgXcQ?si=x"})  # link: sem buscar
    assert t.feitas[-1] == ("youtube", "dQw4w9WgXcQ") and len(buscas) == 1
    with pytest.raises(ErroFerramenta, match="Não achei"):
        await TV(t, buscar=lambda c: []).youtube({"busca": "xyz"})


# ------------------------------------------------------------------ frases curtas


@pytest.mark.parametrize("frase, esperado", [
    ("Vision, desliga a TV.", ("tv_controle", {"acao": "desligar"})),
    ("muta a televisão", ("tv_controle", {"acao": "mudo"})),
    ("aumenta o volume da TV em 3", ("tv_controle", {"acao": "volume_mais", "vezes": 3})),
    ("abaixa o som da tv", ("tv_controle", {"acao": "volume_menos", "vezes": 1})),
    ("coloca o volume da TV no 10", ("tv_volume", {"nivel": 10})),
    ("Vision, tocar blank space no youtube da TV", ("tv_youtube", {"busca": "blank space"})),
    ("desliga a TV em 30 minutos", None), ("não desliga a TV", None), ("desliga", None),
    ("desliga a luz", None), ("toca blank space no youtube", None),
])
def test_frases_curtas_de_tv(frase, esperado):
    assert comando_de_tv(frase) == esperado


async def test_atalho_executa_sem_o_modelo():
    t = TVFalsa()
    nome, _args, resposta, ok = await TV(t).atalho("desliga a tv")
    assert (nome, ok) == ("tv_controle", True) and t.feitas == [("teclas", ["KEY_POWER"])]
    _, _, resposta, ok = await TV(TVFalsa(ligada=False)).atalho("desliga a tv")
    assert not ok and "desligada" in resposta


# ------------------------------------------------------------------ confirmação


async def test_tocar_link_pergunta_sempre():
    t = TVFalsa()
    r = Registro()
    r.adicionar(*TV(t).ferramentas())
    url = "http://192.168.0.2/video.mp4"
    agente = Agente(LLMFalso([chama("tv_tocar_midia", url=url), fala("Ok.")]), r, None, confirmacao="nenhuma")
    resp = await agente.responder("toca esse link na tv", "texto", "t")
    assert resp.aguardando_confirmacao and url in resp.texto and t.feitas == []
    await agente.responder("sim", "texto", "t")
    assert t.feitas == [("midia", url)]
    with pytest.raises(ErroFerramenta, match="http"):
        await TV(t).tocar_midia({"url": "file:///C:/segredo.txt"})


async def test_controle_da_tv_roda_direto_mas_pede_confirmacao_depois_de_texto_de_fora():
    r = Registro()
    t = TVFalsa()
    r.adicionar(*TV(t).ferramentas())
    fs = {f.nome: f for f in r.ferramentas.values()}
    for nome in ("tv_controle", "tv_volume", "tv_canal", "tv_abrir_app", "tv_youtube"):
        assert fs[nome].escrita and not fs[nome].confirmar and fs[nome].confirmar_se_externo
    agente = Agente(LLMFalso([chama("tv_controle", acao="desligar"), fala("Desliguei.")]), r, None,
                    confirmacao="todas")
    resp = await agente.responder("desliga a tv", "texto", "t")
    assert not resp.aguardando_confirmacao and t.feitas == [("teclas", ["KEY_POWER"])]


# ------------------------------------------------------------------ timer que desliga a TV


async def test_timer_desliga_a_tv_no_fim(tmp_path):
    t = TVFalsa()
    r = Registro()
    r.adicionar(*TV(t).ferramentas())
    timers = Timers(tmp_path / "timers.json")
    acoes = {k: v[2] for k, v in ACOES_DE_TIMER.items()}
    temporizador = Temporizador(timers, acoes=acoes)
    assert "ao_acabar" in temporizador.ferramentas()[0].parametros["properties"]
    assert "ao_acabar" not in Temporizador(timers).ferramentas()[0].parametros["properties"]
    disparados = []

    async def disparar(timer):
        await fazer_acao(timer, r, ACOES_DE_TIMER)
        disparados.append(timer.aviso())

    timers.ao_disparar = disparar
    timers.iniciar()
    resposta = await temporizador.criar({"segundos": 0.05, "ao_acabar": "desligar_tv"})
    assert "desligar a TV" in resposta
    with pytest.raises(ErroFerramenta, match="só sei"):
        await temporizador.criar({"segundos": 1, "ao_acabar": "apagar_tudo"})
    for _ in range(50):
        if disparados:
            break
        await asyncio.sleep(0.05)
    timers.fechar()
    assert t.feitas == [("teclas", ["KEY_POWER"])] and disparados == ["Desliguei a TV."]


async def test_timer_com_a_tv_desligada_avisa_que_nao_conseguiu(tmp_path):
    r = Registro()
    r.adicionar(*TV(TVFalsa(ligada=False)).ferramentas())
    timers = Timers(tmp_path / "timers.json")
    timers.iniciar()
    timer = timers.criar(60, "1 minuto", acao="desligar_tv")
    await fazer_acao(timer, r, ACOES_DE_TIMER)
    timers.fechar()
    assert timer.aviso().startswith("Timer acabou, mas não consegui desligar a TV")


# ------------------------------------------------------------------ TVSamsung (HTTP com respx)


@pytest.fixture
def tv_real(monkeypatch):
    t = modulo_tv.TVSamsung(IP, SESSAO, folga_perfis_s=0)
    teclas = []

    async def enviar(lista, _intervalo):
        teclas.append(list(lista))

    async def acessivel():
        return True

    monkeypatch.setattr(t, "_enviar_teclas", enviar)
    monkeypatch.setattr(t, "acessivel", acessivel)
    t.teclas_enviadas = teclas
    return t


@respx.mock
async def test_volume_pelo_upnp(tv_real):
    rota = respx.post(f"http://{IP}:9197/upnp/control/RenderingControl1").mock(side_effect=[
        httpx.Response(200, text="<s:Body><CurrentVolume>7</CurrentVolume></s:Body>"), httpx.Response(200, text="")])
    assert await tv_real.volume() == 7
    assert await tv_real.definir_volume(300) == 100
    corpo = rota.calls[1].request.content.decode()
    assert "<DesiredVolume>100</DesiredVolume>" in corpo
    assert rota.calls[1].request.headers["SOAPACTION"].endswith('#SetVolume"')
    await tv_real.fechar()


@respx.mock
async def test_youtube_fechado_espera_subir_e_aperta_ok(tv_real):
    estados = iter(["stopped", "stopped", "running"])
    respx.get(f"http://{IP}:8080/ws/apps/YouTube").mock(
        side_effect=lambda _r: httpx.Response(200, text=f"<service><state>{next(estados)}</state></service>"))
    abrir = respx.post(f"http://{IP}:8080/ws/apps/YouTube").mock(return_value=httpx.Response(201))
    assert await tv_real.youtube("e-ORhEE9VVg") is True
    assert abrir.calls[0].request.content == b"v=e-ORhEE9VVg"
    assert tv_real.teclas_enviadas == [["KEY_ENTER"]]
    await tv_real.fechar()


@respx.mock
async def test_youtube_aberto_nao_aperta_ok(tv_real):
    respx.get(f"http://{IP}:8080/ws/apps/YouTube").mock(
        return_value=httpx.Response(200, text="<service><state>running</state></service>"))
    respx.post(f"http://{IP}:8080/ws/apps/YouTube").mock(return_value=httpx.Response(201))
    assert await tv_real.youtube("e-ORhEE9VVg") is False and tv_real.teclas_enviadas == []
    with pytest.raises(ValueError):
        await tv_real.youtube("../../x")
    await tv_real.fechar()


@respx.mock
async def test_nunca_fecha_app_pelo_dial(tv_real):
    """Fechar e abrir em sequência reiniciou a TV no teste de 05/10."""
    fechar = respx.delete(url__regex=r".*").mock(return_value=httpx.Response(200))
    respx.get(f"http://{IP}:8080/ws/apps/Netflix").mock(
        return_value=httpx.Response(200, text="<service><state>running</state></service>"))
    respx.post(f"http://{IP}:8080/ws/apps/Netflix").mock(return_value=httpx.Response(201))
    await tv_real.abrir_app("Netflix")
    assert not fechar.called
    await tv_real.fechar()


async def test_tv_desligada_responde_rapido(monkeypatch):
    async def recusa(*_a, **_k):
        raise OSError("sem rota")

    monkeypatch.setattr(asyncio, "open_connection", recusa)
    t = modulo_tv.TVSamsung(IP, SESSAO)
    with pytest.raises(modulo_tv.TVInacessivel, match="desligada"):
        await asyncio.wait_for(t.teclas(["KEY_POWER"]), 2)
    await t.fechar()


@respx.mock
async def test_link_de_midia_vai_escapado_no_xml(tv_real):
    rota = respx.post(f"http://{IP}:9197/upnp/control/AVTransport1").mock(return_value=httpx.Response(200))
    await tv_real.tocar_midia("http://192.168.0.2/a.mp4?x=1&y=<2>")
    assert "x=1&amp;y=&lt;2&gt;" in rota.calls[0].request.content.decode()
    await tv_real.fechar()


# ------------------------------------------------------------------ pareamento


class AutenticadorFalso:
    def __init__(self, host, *, web_session, port, timeout):
        self.host = host

    async def start_pairing(self):
        pass

    async def try_pin(self, pin):
        return SESSAO["token"] if pin == "4321" else None

    async def get_session_id_and_close(self):
        return SESSAO["session_id"]


@pytest.fixture
def autenticador(monkeypatch):
    from samsungtvws.encrypted import authenticator

    monkeypatch.setattr(authenticator, "SamsungTVEncryptedWSAsyncAuthenticator", AutenticadorFalso)


async def test_pareamento_grava_a_sessao_sem_mostrar_o_token(cfg, autenticador, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    pins = iter(["0000", "4321"])
    avisos = []
    assert await modulo_tv.parear_interativo(cfg, IP, ler=lambda _p: next(pins), avisar=avisos.append) == 0
    assert modulo_tv.ler_sessao(cfg) == SESSAO and cfg.get("tv.ip") == IP
    assert "PIN recusado. Tente de novo." in avisos
    tudo = caplog.text + capsys.readouterr().out + " ".join(avisos)
    assert SESSAO["token"] not in tudo


async def test_parear_com_ip_de_fora_nem_tenta(cfg, autenticador):
    avisos = []
    assert await modulo_tv.parear_interativo(cfg, "8.8.8.8", avisar=avisos.append) == 1
    assert "rede local" in avisos[0] and modulo_tv.ler_sessao(cfg) is None


# ------------------------------------------------------------------ Conexões


def test_conexoes_valida_ip_e_pin():
    assert conexoes.validar("tv", {"ip": " 192.168.0.50 "}) == {"ip": "192.168.0.50"}
    assert conexoes.validar("tv", {"pin": "4321"}) == {"pin": "4321"}
    for dados in ({"ip": "8.8.8.8"}, {"ip": "tv.evil.com"}, {"pin": "12a4"}, {"pin": "12345"}):
        with pytest.raises(ValueError):
            conexoes.validar("tv", dados)


async def test_conexoes_pareia_em_dois_passos_e_desconecta(cfg, autenticador):
    cartao = lambda: next(c for c in conexoes.estado(cfg) if c["id"] == "tv")
    assert cartao()["situacao"] == "falta"
    avisos = []
    assert await conexoes.conectar(cfg, "tv", {"ip": IP}, avisos.append, None) is None
    assert cartao()["acao"] == "Confirmar PIN" and cfg.get("tv.ip") == IP
    assert await conexoes.conectar(cfg, "tv", {"pin": "9999"}, avisos.append, None) is None
    assert await conexoes.conectar(cfg, "tv", {"pin": "4321"}, avisos.append, None) is True
    c = cartao()
    assert c["situacao"] == "ok" and c["campos"][0]["valor"] == IP
    assert SESSAO["token"] not in str(c)
    antes = conexoes.assinatura(cfg)["tv"]
    assert "apagado" in conexoes.desconectar(cfg, "tv")
    assert not modulo_tv.tem_pareamento(cfg) and conexoes.assinatura(cfg)["tv"] != antes
