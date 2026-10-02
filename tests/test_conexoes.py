"""Tela de Conexões (vision/conexoes.py): estado sem segredo, validação, .env e o que vale ao reiniciar."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from dotenv import dotenv_values
from starlette.testclient import TestClient

from vision import conexoes, config, spotify
from vision.eventos import Barramento
from vision.server import Controle, criar_app

TOKEN = "t" * 32
ORIGEM = "http://127.0.0.1:8765"
CREDENCIAL = {"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "segredo-x",
                            "redirect_uris": ["http://localhost"]}}


@pytest.fixture
def cfg_conexoes(cfg, tmp_path, monkeypatch):
    """cfg com a raiz (onde fica o .env) numa pasta do teste e sem nenhuma variável de conexão herdada."""
    cfg.raiz = tmp_path / "raiz"
    cfg.raiz.mkdir()
    for nome in conexoes.CHAVES_ENV:
        monkeypatch.delenv(nome, raising=False)
    cfg.bruto.setdefault("mcp", {}).setdefault("google-calendar", {})["env"] = {
        "GOOGLE_OAUTH_CREDENTIALS": str(cfg.dados / "google-oauth.json")}
    yield cfg
    for nome in conexoes.CHAVES_ENV:  # gravar_env mexe no os.environ do processo de teste
        os.environ.pop(nome, None)


def _por_id(cfg) -> dict:
    return {c["id"]: c for c in conexoes.estado(cfg)}


# ------------------------------------------------------------------ .env


@pytest.mark.parametrize("valor", ["simples", "com espaço # e cerquilha", "aspas ' simples", 'aspas " duplas',
                                   "barra \\ invertida", "fim com barra \\", "$HOME e ${X}", "=igual=", "çãõ"])
def test_env_grava_o_valor_exato_que_o_dotenv_le_de_volta(tmp_path, monkeypatch, valor):
    monkeypatch.delenv("ORBIT_PASSWORD", raising=False)
    arquivo = tmp_path / ".env"
    conexoes.gravar_env(arquivo, {"ORBIT_PASSWORD": valor})
    assert dotenv_values(arquivo, interpolate=False)["ORBIT_PASSWORD"] == valor  # como o config lê
    assert os.environ["ORBIT_PASSWORD"] == valor
    os.environ.pop("ORBIT_PASSWORD")


def test_env_troca_so_a_linha_certa_e_mantem_o_resto(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    arquivo = tmp_path / ".env"
    arquivo.write_text("# comentário\n# TAVILY_API_KEY=exemplo comentado\nORBIT_URL=https://x\nTAVILY_API_KEY=\n",
                       encoding="utf-8")
    conexoes.gravar_env(arquivo, {"TAVILY_API_KEY": "tvly-abcdefgh123"})
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert linhas[:3] == ["# comentário", "# TAVILY_API_KEY=exemplo comentado", "ORBIT_URL=https://x"]
    assert dotenv_values(arquivo) == {"ORBIT_URL": "https://x", "TAVILY_API_KEY": "tvly-abcdefgh123"}
    conexoes.gravar_env(arquivo, {"TAVILY_API_KEY": None})  # apagar: a linha fica vazia
    assert dotenv_values(arquivo)["TAVILY_API_KEY"] == "" and "TAVILY_API_KEY" not in os.environ


def test_env_recusa_variavel_de_fora_e_quebra_de_linha(tmp_path):
    with pytest.raises(ValueError):
        conexoes.gravar_env(tmp_path / ".env", {"PATH": "x"})  # a tela não muda qualquer variável
    with pytest.raises(ValueError):
        conexoes.gravar_env(tmp_path / ".env", {"ORBIT_EMAIL": "a@b.com\nPATH=c:\\mal"})  # nem injeta linha
    assert not (tmp_path / ".env").exists()


# ------------------------------------------------------------------ validação


@pytest.mark.parametrize("servico,dados,erro", [
    ("nao-existe", {}, "desconhecido"),
    ("spotify", {"client_id": "curto"}, "32"),
    ("spotify", {"client_id": 123}, "inválidos"),
    ("alexa", {"email": "sem-arroba"}, "e-mail"),
    ("web", {"chave": "sk-outra-coisa-123456"}, "tvly-"),
    ("orbit", {"email": "a@b.com", "senha": ""}, "senha"),
    ("orbit", {"email": "a@b.com", "senha": "x\ny"}, "senha"),
    ("google", {"credencial": "x" * (conexoes.LIMITE_CAMPO + 1)}, "grande"),
])
def test_validar_recusa_o_que_nao_serve(servico, dados, erro):
    with pytest.raises(ValueError, match=erro):
        conexoes.validar(servico, dados)


def test_validar_limpa_o_que_serve():
    assert conexoes.validar("spotify", {"client_id": " " + "a" * 32 + " "}) == {"client_id": "a" * 32}
    assert conexoes.validar("web", {"chave": "tvly-dev-AbC123_xyz"}) == {"chave": "tvly-dev-AbC123_xyz"}
    assert conexoes.validar("orbit", {"email": "a@b.com", "senha": " com espaço "})["senha"] == " com espaço "
    assert conexoes.validar("google", {}) == {} and conexoes.validar("wispr", {}) == {}


# ------------------------------------------------------------------ estado (o que vai para a tela)


def test_estado_nunca_devolve_um_segredo(cfg_conexoes):
    c = cfg_conexoes
    conexoes.gravar_env(c.raiz / ".env", {"TAVILY_API_KEY": "tvly-SEGREDO-123456", "ORBIT_EMAIL": "eu@x.com",
                                          "ORBIT_PASSWORD": "SENHA-SECRETA"})
    conexoes.guardar_credencial_google(c, json.dumps(CREDENCIAL))
    spotify.gravar(c, {"client_id": "a" * 32, "access_token": "ACESSO-SECRETO", "refresh_token": "REFRESH-SECRETO"})
    c.bruto["mcp"].setdefault("orbit", {})["ativo"] = True
    texto = json.dumps(conexoes.estado(c), ensure_ascii=False)
    for segredo in ("SEGREDO-123456", "SENHA-SECRETA", "segredo-x", "ACESSO-SECRETO", "REFRESH-SECRETO"):
        assert segredo not in texto
    estado = _por_id(c)
    assert estado["web"]["situacao"] == "ok" and estado["orbit"]["situacao"] == "ok"
    assert estado["spotify"]["situacao"] == "ok" and estado["spotify"]["campos"][0]["valor"] == "a" * 32


def test_estado_do_google_segue_o_login(cfg_conexoes):
    c = cfg_conexoes
    assert _por_id(c)["google"]["situacao"] == "falta" and "credencial" in _por_id(c)["google"]["detalhe"]
    conexoes.guardar_credencial_google(c, json.dumps(CREDENCIAL))
    assert _por_id(c)["google"]["detalhe"] == "Ainda não conectado."
    from vision import tempo
    from vision.google_login import ARQUIVO_DATA

    (c.dados / ARQUIVO_DATA).write_text(tempo.agora().isoformat(timespec="seconds"), encoding="utf-8")
    g = _por_id(c)["google"]
    assert g["situacao"] == "ok" and "7 dias" in g["detalhe"] and g["acao"] == "Reconectar"
    c.bruto["mcp"]["google-calendar"]["ativo"] = False
    assert _por_id(c)["google"]["situacao"] == "desligado"


@pytest.mark.parametrize("texto,erro", [
    ("não é json", "JSON"),
    (json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s"}}), "computador"),
    (json.dumps({"installed": {"client_id": "x"}}), "client_id"),
])
def test_credencial_do_google_errada_nao_e_gravada(cfg_conexoes, texto, erro):
    with pytest.raises(ValueError, match=erro):
        conexoes.guardar_credencial_google(cfg_conexoes, texto)
    assert not (cfg_conexoes.dados / "google-oauth.json").exists()


# ------------------------------------------------------------------ conectar, desconectar, reiniciar


async def test_conectar_web_e_orbit_grava_no_env_e_desconectar_apaga(cfg_conexoes):
    c, frases = cfg_conexoes, []
    import threading

    assert await conexoes.conectar(c, "web", {"chave": "tvly-abcdefgh123"}, frases.append, threading.Event())
    # O Orbit agora confere o login de verdade (testes com HTTP simulado abaixo); aqui só o desconectar.
    conexoes.gravar_env(c.raiz / ".env", {"ORBIT_EMAIL": "eu@x.com", "ORBIT_PASSWORD": "s3nha"})
    env = dotenv_values(c.raiz / ".env")
    assert env["TAVILY_API_KEY"] == "tvly-abcdefgh123" and env["ORBIT_PASSWORD"] == "s3nha"
    assert frases == ["Chave da Tavily guardada."]
    conexoes.desconectar(c, "web")
    conexoes.desconectar(c, "orbit")
    env = dotenv_values(c.raiz / ".env")
    assert not env["TAVILY_API_KEY"] and not env["ORBIT_EMAIL"] and not env["ORBIT_PASSWORD"]
    assert _por_id(c)["web"]["situacao"] == "falta"


def test_desconectar_spotify_mantem_so_o_client_id(cfg_conexoes):
    c = cfg_conexoes
    spotify.gravar(c, {"client_id": "a" * 32, "access_token": "x", "refresh_token": "y"})
    conexoes.desconectar(c, "spotify")
    assert spotify.ler(c) == {"client_id": "a" * 32} and not spotify.tem_login(c)


def test_desconectar_alexa_so_apaga_a_pasta_dela(cfg_conexoes):
    c = cfg_conexoes
    from vision import alexa

    (alexa.pasta(c)).mkdir(parents=True)
    (alexa.pasta(c) / "conta.json").write_text("{}", encoding="utf-8")
    (c.dados / "memoria.db").write_text("x", encoding="utf-8")
    conexoes.desconectar(c, "alexa")
    assert not alexa.pasta(c).exists() and (c.dados / "memoria.db").exists()


def test_o_que_mudou_desde_o_inicio_pede_reinicio(cfg_conexoes):
    c = cfg_conexoes
    inicio = conexoes.assinatura(c)
    assert conexoes.mudou_desde(c, inicio) == []
    conexoes.gravar_env(c.raiz / ".env", {"TAVILY_API_KEY": "tvly-abcdefgh123"})
    config.salvar_ajustes(c, {"alexa.ativo": False})
    assert conexoes.mudou_desde(c, inicio) == ["Alexa", "Busca na web"]
    # A impressão digital não carrega o segredo.
    assert "tvly-abcdefgh123" not in json.dumps(conexoes.assinatura(c))


def test_interruptor_so_aceita_verdadeiro_ou_falso_do_arquivo_local(tmp_path, monkeypatch):
    (tmp_path / "raiz" / "data").mkdir(parents=True)
    (tmp_path / "raiz" / "data" / config.AJUSTES_LOCAIS).write_text(
        'spotify.ativo: false\nalexa.ativo: "rm -rf"\nmcp.orbit.ativo: true\n', encoding="utf-8")
    monkeypatch.setattr(config, "RAIZ", tmp_path / "raiz")
    monkeypatch.delenv("VISION_SEM_AJUSTES")
    c = config.carregar(config.Path(__file__).resolve().parents[1] / "config.yaml")
    assert c.get("spotify.ativo") is False and c.get("mcp.orbit.ativo") is True
    assert c.get("alexa.ativo") is True  # o valor que não é booleano foi ignorado: vale o config.yaml


def test_comandos_de_conexao_pelo_websocket_sao_validados(cfg):
    barramento = Barramento()
    recebidos = []

    async def ler():
        barramento.publicar({"tipo": "conexoes", "servicos": []})

    async def conectar(servico, dados):
        recebidos.append(("conectar", servico, dados))

    async def desconectar(servico):
        recebidos.append(("desconectar", servico))

    async def ligar(servico, ligado):
        recebidos.append(("ligar", servico, ligado))

    async def cancelar(servico):
        recebidos.append(("cancelar", servico))
        barramento.publicar({"tipo": "fim"})

    async def painel():
        return {"tipo": "painel"}

    controle = Controle(painel=painel, ler_conexoes=ler, conectar=conectar, desconectar=desconectar,
                        ligar_conexao=ligar, cancelar_conexao=cancelar, reiniciar=lambda: recebidos.append("reiniciar"))
    c = TestClient(criar_app(cfg, token=TOKEN, barramento=barramento, controle=controle))
    with c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
        ws.send_json({"tipo": "ola", "token": TOKEN})
        assert ws.receive_json()["tipo"] == "painel"
        ws.send_json({"tipo": "conexoes"})
        assert ws.receive_json()["tipo"] == "conexoes"
        ws.send_json({"tipo": "conectar", "servico": "../etc", "dados": {}})  # serviço que não existe
        ws.send_json({"tipo": "conectar", "servico": "web", "dados": {"chave": 5}})  # valor que não é texto
        ws.send_json({"tipo": "conectar", "servico": "web", "dados": {"x": "y" * (conexoes.LIMITE_CAMPO + 1)}})
        ws.send_json({"tipo": "conectar", "servico": "web", "dados": {"chave": "tvly-abcdefgh123"}})
        ws.send_json({"tipo": "ligar_conexao", "servico": "alexa", "ligado": "sim"})  # não é booleano
        ws.send_json({"tipo": "ligar_conexao", "servico": "alexa", "ligado": False})
        ws.send_json({"tipo": "desconectar", "servico": "spotify"})
        ws.send_json({"tipo": "reiniciar"})
        ws.send_json({"tipo": "cancelar_conexao", "servico": "google"})
        assert ws.receive_json() == {"tipo": "fim"}
    assert recebidos == [("conectar", "web", {"chave": "tvly-abcdefgh123"}), ("ligar", "alexa", False),
                         ("desconectar", "spotify"), "reiniciar", ("cancelar", "google")]


# ------------------------------------------------------------------ o núcleo


def _nucleo(cfg):
    from vision.nucleo import Nucleo

    n = Nucleo(cfg, com_voz=False)
    n.loop = asyncio.get_running_loop()
    n.barramento.ligar(n.loop)
    return n


async def _esperar_fim(n, servico):
    for _ in range(200):
        if servico not in n.logins:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("o login não terminou")


@pytest.mark.parametrize("de_thread", [False, True])
async def test_a_ultima_frase_do_login_e_o_resultado_na_tela(cfg_conexoes, monkeypatch, de_thread):
    """Spotify/Alexa/Wispr avisam no próprio loop; o Google, de uma thread. Nos dois, a última frase fica."""
    n = _nucleo(cfg_conexoes)

    async def conectar_falso(_cfg, _servico, _dados, avisar, _cancelar):
        def login():
            avisar("Abrindo o navegador...")
            avisar("Spotify ligado. Dispositivos agora: PC.")

        if de_thread:
            await asyncio.to_thread(login)
        else:
            login()
        return True

    monkeypatch.setattr(conexoes, "conectar", conectar_falso)
    with n.barramento.assinar() as fila:
        await n.conectar("spotify", {"client_id": "a" * 32})
        await _esperar_fim(n, "spotify")
        await asyncio.sleep(0.05)
        eventos = [fila.get_nowait() for _ in range(fila.qsize())]
    assert n.andamento["spotify"] == {"texto": "Spotify ligado. Dispositivos agora: PC.", "rodando": False, "ok": True}
    ultimo = [e for e in eventos if e["tipo"] == "conexoes"][-1]
    assert ultimo["andamento"]["spotify"]["ok"] is True
    assert ultimo["reiniciar"] == []  # o login falso não gravou nada


async def test_erro_de_validacao_e_cancelar_viram_frase_na_tela(cfg_conexoes, monkeypatch):
    n = _nucleo(cfg_conexoes)
    await n.conectar("web", {"chave": "errada"})
    assert n.andamento["web"]["ok"] is False and "tvly-" in n.andamento["web"]["texto"]
    assert "web" not in n.logins

    parado = asyncio.Event()

    async def demora(_cfg, _servico, _dados, _avisar, cancelar):
        try:
            await asyncio.sleep(60)
        finally:
            parado.set()
        return True

    monkeypatch.setattr(conexoes, "conectar", demora)
    await n.conectar("wispr", {})
    assert n.andamento["wispr"]["rodando"] is True
    await n.conectar("wispr", {})  # o segundo clique não abre outro login
    assert len(n.logins) == 1
    cancelar = n.logins["wispr"][1]
    await n.cancelar_conexao("wispr")
    await _esperar_fim(n, "wispr")
    assert parado.is_set() and cancelar.is_set()
    assert n.andamento["wispr"] == {"texto": "Cancelado.", "rodando": False, "ok": False}


async def test_ligar_e_desligar_vai_para_o_arquivo_local(cfg_conexoes):
    n = _nucleo(cfg_conexoes)
    await n.ligar_conexao("alexa", False)
    assert config.ler_ajustes(cfg_conexoes.dados / config.AJUSTES_LOCAIS) == {"alexa.ativo": False}
    assert n.andamento["alexa"]["texto"] == "Desligado."
    assert conexoes.mudou_desde(cfg_conexoes, n.conexoes_inicio) == ["Alexa"]


def test_reiniciar_sobe_o_novo_sem_herdar_as_variaveis_do_env(cfg_conexoes, monkeypatch):
    """O novo lê o .env de novo: herdando, uma chave apagada na tela voltaria (load_dotenv não sobrescreve)."""
    from vision import nucleo

    chamadas = []
    monkeypatch.setattr(nucleo.subprocess, "Popen", lambda cmd, **kw: chamadas.append((cmd, kw)))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-velha-123456")
    monkeypatch.setenv("OUTRA", "fica")
    nucleo.subir_de_novo(cfg_conexoes)
    (cmd, kw), = chamadas
    assert cmd[-2:] == ["--abrir", "--reiniciando"]
    assert "TAVILY_API_KEY" not in kw["env"] and kw["env"]["OUTRA"] == "fica"


def test_reiniciar_pela_tela_para_o_nucleo(cfg_conexoes):
    n = _nucleo_sem_loop(cfg_conexoes)
    n.parar = asyncio.Event()
    n.reiniciar()
    assert n.reiniciar_ao_sair and n.parar.is_set()


def _nucleo_sem_loop(cfg):
    from vision.nucleo import Nucleo

    return Nucleo(cfg, com_voz=False)


# ------------------------------------------------------------------ revisão do PR 38


def test_env_troca_todas_as_linhas_da_variavel_e_entende_export(tmp_path, monkeypatch):
    """O dotenv usa a última linha: trocar só a 1ª deixaria a chave velha valendo (e no disco)."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    arquivo = tmp_path / ".env"
    arquivo.write_text("TAVILY_API_KEY=tvly-velha-1\nORBIT_URL=x\nexport TAVILY_API_KEY=tvly-velha-2\n", encoding="utf-8")
    conexoes.gravar_env(arquivo, {"TAVILY_API_KEY": "tvly-nova-123456"})
    texto = arquivo.read_text(encoding="utf-8")
    assert "velha" not in texto and texto.count("TAVILY_API_KEY") == 1
    assert dotenv_values(arquivo, interpolate=False) == {"TAVILY_API_KEY": "tvly-nova-123456", "ORBIT_URL": "x"}
    conexoes.gravar_env(arquivo, {"TAVILY_API_KEY": None})
    assert not dotenv_values(arquivo, interpolate=False)["TAVILY_API_KEY"]


@pytest.mark.parametrize("ruim", ["a\x0bb", "a\x0cb", "a\x1cb", "a\x85b", "a b", "a\tb"])
def test_env_e_senha_recusam_caractere_de_controle(tmp_path, ruim):
    with pytest.raises(ValueError):
        conexoes.gravar_env(tmp_path / ".env", {"ORBIT_PASSWORD": ruim})
    with pytest.raises(ValueError, match="senha"):
        conexoes.validar("orbit", {"email": "a@b.com", "senha": ruim})


def test_env_temporario_nao_fica_no_disco_se_a_troca_falhar(tmp_path, monkeypatch):
    """O .env.tmp tem todos os segredos: com o .env preso (antivírus), ele não pode sobrar na raiz."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    arquivo = tmp_path / ".env"

    def preso(self, _destino):
        raise PermissionError("em uso")

    monkeypatch.setattr(type(arquivo), "replace", preso)
    with pytest.raises(PermissionError):
        conexoes.gravar_env(arquivo, {"TAVILY_API_KEY": "tvly-abcdefgh123"})
    assert list(tmp_path.iterdir()) == []


def test_env_tmp_fica_fora_do_git():
    import subprocess

    raiz = config.Path(__file__).resolve().parents[1]
    r = subprocess.run(["git", "check-ignore", ".env.tmp", ".env.local"], cwd=raiz, capture_output=True, text=True)
    assert r.stdout.split() == [".env.tmp", ".env.local"]
    assert subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=raiz).returncode == 1  # este vai


async def test_desconectar_alexa_ou_wispr_pede_reinicio_ate_reiniciar(cfg_conexoes):
    """A sessão viva no núcleo regravaria os tokens: o aviso de reiniciar não some sozinho."""
    from vision import alexa

    n = _nucleo(cfg_conexoes)
    alexa.pasta(cfg_conexoes).mkdir(parents=True)
    await n.desconectar("alexa")
    assert "Reinicie" in n.andamento["alexa"]["texto"]
    with n.barramento.assinar() as fila:
        await n.ler_conexoes()
        assert fila.get_nowait()["reiniciar"] == ["Alexa"]


async def test_cancelar_o_login_do_wispr_fecha_a_conexao(cfg, monkeypatch):
    """Revisão do PR 38: o cancelamento saía de iniciar() antes do finally e a porta 8767 ficava presa."""
    from vision import wispr
    from vision.tools import mcp_host

    fechou = asyncio.Event()

    class ConexaoLenta:
        def __init__(self, *_a, **_k):
            self.cliente, self.erro = None, None

        async def iniciar(self):
            await asyncio.sleep(60)

        async def fechar(self):
            fechou.set()

    monkeypatch.setattr(mcp_host, "ConexaoMCP", ConexaoLenta)
    tarefa = asyncio.ensure_future(wispr.login(cfg, avisar=lambda _t: None))
    await asyncio.sleep(0.05)
    tarefa.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarefa
    assert fechou.is_set()


@pytest.mark.parametrize("cai_ao_encerrar", [False, True])
def test_main_reinicia_mesmo_se_o_encerramento_der_erro(cfg_conexoes, monkeypatch, cai_ao_encerrar):
    from vision import nucleo

    monkeypatch.setattr(nucleo.config, "carregar", lambda: cfg_conexoes)
    monkeypatch.setattr(nucleo, "configurar_log", lambda *_a, **_k: cfg_conexoes.dados / "x.log")
    monkeypatch.setattr(nucleo.inicializacao, "primeira_vez", lambda *_a: False)
    soltou, subiu = [], []
    monkeypatch.setattr(nucleo.InstanciaUnica, "pegar", lambda self: True)
    monkeypatch.setattr(nucleo.InstanciaUnica, "soltar", lambda self: soltou.append(True))
    monkeypatch.setattr(nucleo, "subir_de_novo", lambda _cfg, extra=None: subiu.append(extra))

    async def rodar(self):
        self.reiniciar_ao_sair = True
        if cai_ao_encerrar:
            raise RuntimeError("a janela não fechou direito")

    monkeypatch.setattr(nucleo.Nucleo, "rodar", rodar)
    nucleo.main(["--sem-voz"])
    assert soltou == [True] and subiu == [["--sem-voz"]]  # e o --sem-voz vai junto


def test_main_sem_conseguir_subir_o_novo_avisa_no_log(cfg_conexoes, monkeypatch, caplog):
    from vision import nucleo

    monkeypatch.setattr(nucleo.config, "carregar", lambda: cfg_conexoes)
    monkeypatch.setattr(nucleo, "configurar_log", lambda *_a, **_k: cfg_conexoes.dados / "x.log")
    monkeypatch.setattr(nucleo.inicializacao, "primeira_vez", lambda *_a: False)
    monkeypatch.setattr(nucleo.InstanciaUnica, "pegar", lambda self: True)

    async def rodar(self):
        self.reiniciar_ao_sair = True

    def falha(*_a, **_k):
        raise OSError("bloqueado pelo antivírus")

    monkeypatch.setattr(nucleo.Nucleo, "rodar", rodar)
    monkeypatch.setattr(nucleo.subprocess, "Popen", falha)
    assert nucleo.main([]) == 1
    assert "não consegui subir o núcleo novo" in caplog.text


# ------------------------------------------------------------------ Orbit: login com código por e-mail


def _jwt(dias: float) -> str:
    import base64
    import time

    corpo = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + dias * 86400}).encode()).decode().rstrip("=")
    return f"cabeca.{corpo}.assinatura"


@pytest.fixture
def orbit_http(cfg_conexoes, monkeypatch):
    """O cliente do Orbit de verdade (mcp_servers/orbit) contra HTTP simulado."""
    import respx

    real = conexoes._orbit_api
    raiz = config.Path(__file__).resolve().parents[1]
    monkeypatch.setattr(conexoes, "_orbit_api", lambda _cfg: real(type("C", (), {"raiz": raiz})()))
    monkeypatch.setenv("ORBIT_URL", "https://orbit.teste")
    monkeypatch.setattr(conexoes, "_desafio_orbit", None)
    with respx.mock(base_url="https://orbit.teste") as m:
        yield m


async def test_orbit_login_direto_grava_token_e_mostra_validade(cfg_conexoes, orbit_http):
    orbit_http.post("/auth/login").respond(json={"accessToken": _jwt(6.5)})
    frases = []
    ok = await conexoes.conectar(cfg_conexoes, "orbit", {"email": "eu@x.com", "senha": "s3nha"}, frases.append, None)
    assert ok is True and frases[-1] == "Orbit conectado."
    env = dotenv_values(cfg_conexoes.raiz / ".env", interpolate=False)
    assert env["ORBIT_EMAIL"] == "eu@x.com" and env["ORBIT_PASSWORD"] == "s3nha" and env["ORBIT_TOKEN"].count(".") == 2
    cfg_conexoes.bruto["mcp"].setdefault("orbit", {})["ativo"] = True
    o = _por_id(cfg_conexoes)["orbit"]
    assert o["situacao"] == "ok" and "vence em 7 dias" in o["detalhe"]
    assert env["ORBIT_TOKEN"] not in json.dumps(o)


async def test_orbit_pede_codigo_e_a_tela_pede_o_codigo(cfg_conexoes, orbit_http):
    orbit_http.post("/auth/login").respond(json={"challengeToken": "desafio-secreto", "maskedEmail": "e***@x.com"})
    verificar = orbit_http.post("/auth/code/verify").respond(json={"accessToken": _jwt(7)})
    frases = []
    ok = await conexoes.conectar(cfg_conexoes, "orbit", {"email": "eu@x.com", "senha": "s3nha"}, frases.append, None)
    assert ok is None and "e***@x.com" in frases[-1]
    assert not (cfg_conexoes.raiz / ".env").exists()  # nada gravado antes do código
    o = _por_id(cfg_conexoes)["orbit"]
    assert [c["nome"] for c in o["campos"]] == ["codigo"] and o["acao"] == "Confirmar código"
    texto = json.dumps(conexoes.estado(cfg_conexoes))
    assert "desafio-secreto" not in texto and "s3nha" not in texto
    assert conexoes.validar("orbit", {"codigo": " 123456 "}) == {"codigo": "123456"}
    with pytest.raises(ValueError):
        conexoes.validar("orbit", {"codigo": "12 34"})
    assert await conexoes.conectar(cfg_conexoes, "orbit", {"codigo": "123456"}, frases.append, None) is True
    assert json.loads(verificar.calls[0].request.content) == {"challengeToken": "desafio-secreto", "code": "123456"}
    env = dotenv_values(cfg_conexoes.raiz / ".env", interpolate=False)
    assert env["ORBIT_PASSWORD"] == "s3nha" and conexoes._desafio_orbit is None


async def test_orbit_senha_errada_e_codigo_vencido(cfg_conexoes, orbit_http, monkeypatch):
    orbit_http.post("/auth/login").respond(401, json={"message": "invalid"})
    frases = []
    assert await conexoes.conectar(cfg_conexoes, "orbit", {"email": "eu@x.com", "senha": "x"}, frases.append, None) is False
    assert "recusou o e-mail ou a senha" in frases[-1]
    monkeypatch.setattr(conexoes, "_desafio_orbit", {"desafio": "d", "mascarado": "m", "email": "e", "senha": "s",
                                                     "quando": -10_000.0})
    assert await conexoes.conectar(cfg_conexoes, "orbit", {"codigo": "123456"}, frases.append, None) is False
    assert "venceu" in frases[-1] and conexoes._desafio_orbit is None


async def test_nucleo_mostra_o_passo_do_codigo_sem_cara_de_erro(cfg_conexoes, monkeypatch):
    n = _nucleo(cfg_conexoes)

    async def pede_codigo(_cfg, _servico, _dados, avisar, _cancelar):
        avisar("O Orbit mandou um código para e***@x.com: digite ele aqui.")
        return None

    monkeypatch.setattr(conexoes, "conectar", pede_codigo)
    await n.conectar("orbit", {"email": "eu@x.com", "senha": "s3nha"})
    await _esperar_fim(n, "orbit")
    assert n.andamento["orbit"] == {"texto": "O Orbit mandou um código para e***@x.com: digite ele aqui.",
                                    "rodando": False, "ok": None}
