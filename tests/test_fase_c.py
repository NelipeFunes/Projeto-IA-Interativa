"""Fase C: tela de ajustes, clique da tela que chega tarde, resposta falada quando confirma pela tela, stdin da janela."""

import threading
import time
from datetime import timedelta

import pytest
from fakes.llm_falso import LLMFalso, chama
from starlette.testclient import TestClient

from vision import ajustes, config, tempo
from vision.brain.agent import Agente
from vision.eventos import Barramento
from vision.nucleo import Janela
from vision.server import Controle, criar_app
from vision.tools.agenda import Agenda
from vision.tools.base import Registro

TOKEN = "segredo-de-teste"
ORIGEM = "http://127.0.0.1:8765"


# ------------------------------------------------------------------ confirmação pela tela


@pytest.fixture
def agente(cfg, host):
    def criar(roteiro):
        r = Registro()
        r.adicionar(*Agenda(cfg, host).ferramentas())
        return Agente(LLMFalso(roteiro), r, None)

    return criar


async def test_clique_numa_confirmacao_que_ja_mudou_nao_confirma_a_nova(agente, servidor_agenda):
    """Conferido dentro da trava: a voz resolveu a p1 e pediu a p2 enquanto o clique na p1 esperava."""
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    a = agente([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    antes = len(servidor_agenda.eventos)
    await a.responder("marca barbeiro amanhã às 16h", "voz", "voz")
    atual = a.sessoes[("voz", "voz")].pendente.id
    r = await a.responder("sim", "voz", "voz", pendente_esperada="p-antiga")
    assert "já tinha sido resolvida" in r.texto
    assert a.sessoes[("voz", "voz")].pendente.id == atual  # a pendência atual continua esperando
    assert len(servidor_agenda.eventos) == antes  # nada foi criado


async def test_canal_da_pendente(agente):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    a = agente([chama("agenda_criar", titulo="X", data=amanha)])
    await a.responder("marca X amanhã", "voz", "voz")
    pid = a.sessoes[("voz", "voz")].pendente.id
    assert a.canal_da_pendente(pid) == "voz" and a.canal_da_pendente("p999") is None


# ------------------------------------------------------------------ ajustes


@pytest.fixture
def cfg_ajustes(cfg, tmp_path, monkeypatch):
    pasta = tmp_path / "modelos" / "piper"
    pasta.mkdir(parents=True)
    for nome in ("pt_BR-faber-medium", "pt_BR-cadu-medium", "en_US-lessac-medium"):
        (pasta / f"{nome}.onnx").write_bytes(b"")
    cfg.modelos = tmp_path / "modelos"
    monkeypatch.setattr(ajustes, "microfones", lambda: ["Microfone (HyperX Cloud)", "Microfone (C920)"])
    return cfg


def test_ajustes_mostram_so_vozes_em_portugues_e_o_microfone_atual(cfg_ajustes):
    cfg_ajustes.bruto.setdefault("voz", {})["microfone"] = ["C920", "HyperX"]
    dados = ajustes.ler(cfg_ajustes)
    assert dados["opcoes"]["voz.voz_piper"] == ["pt_BR-cadu-medium", "pt_BR-faber-medium"]
    assert dados["valores"]["voz.microfone"] == "Microfone (C920)"
    assert {c["chave"] for c in dados["campos"]} == set(ajustes.POR_CHAVE)


@pytest.mark.parametrize("dados,erro", [
    ({"modelo.nome": "qwen3.5:9b"}, "desconhecido"),  # só as chaves da lista
    ({"assistente.nome": ""}, "nome"),
    ({"assistente.nome": "a" * 21}, "nome"),
    ({"assistente.nome": "<script>"}, "nome"),
    ({"voz.velocidade_fala": 9}, "entre"),
    ({"voz.velocidade_fala": "1.0"}, "número"),
    ({"voz.velocidade_fala": True}, "número"),
    ({"voz.conversa_silencio_max_s": 5}, "entre"),
    ({"voz.voz_piper": "../../segredo"}, "instalada"),
    ({"voz.voz_piper": "en_US-lessac-medium"}, "instalada"),
    ({"voz.microfone": "Microfone de outro PC"}, "encontrado"),
    ([1, 2], "inválidos"),
])
def test_ajuste_invalido_e_recusado(cfg_ajustes, dados, erro):
    with pytest.raises(ValueError, match=erro):
        ajustes.validar(cfg_ajustes, dados)


def test_ajustes_validos_e_o_que_pede_reinicio(cfg_ajustes):
    cfg_ajustes.bruto.setdefault("voz", {})["microfone"] = ["HyperX", "C920"]
    m = ajustes.validar(cfg_ajustes, {"assistente.nome": "Íris", "voz.velocidade_fala": 1.1,
                                      "voz.voz_piper": "pt_BR-cadu-medium", "voz.microfone": "Microfone (C920)"})
    assert m["voz.microfone"] == ["Microfone (C920)", "HyperX"]  # o escolhido primeiro, os outros de reserva
    assert ajustes.precisa_reiniciar(m, cfg_ajustes)  # nome e microfone só valem ao reiniciar
    assert not ajustes.precisa_reiniciar({"voz.velocidade_fala": 1.1}, cfg_ajustes)


def test_ajustes_salvos_ficam_fora_do_git_e_voltam_ao_carregar(cfg, monkeypatch, tmp_path):
    config.salvar_ajustes(cfg, {"voz.velocidade_fala": 1.2})
    assert cfg.get("voz.velocidade_fala") == 1.2
    arquivo = cfg.dados / config.AJUSTES_LOCAIS
    assert config.ler_ajustes(arquivo) == {"voz.velocidade_fala": 1.2}
    config.salvar_ajustes(cfg, {"assistente.nome": "Íris"})
    assert config.ler_ajustes(arquivo) == {"voz.velocidade_fala": 1.2, "assistente.nome": "Íris"}
    # carregar() lê de <raiz>/data: aponta a raiz para um lugar só do teste
    (tmp_path / "raiz" / "data").mkdir(parents=True)
    (tmp_path / "raiz" / "data" / config.AJUSTES_LOCAIS).write_bytes(arquivo.read_bytes())
    monkeypatch.setattr(config, "RAIZ", tmp_path / "raiz")
    monkeypatch.delenv("VISION_SEM_AJUSTES")
    novo = config.carregar(config.Path(__file__).resolve().parents[1] / "config.yaml")
    assert novo.get("voz.velocidade_fala") == 1.2 and novo.get("assistente.nome") == "Íris"


def test_arquivo_de_ajustes_estragado_vale_o_config(tmp_path):
    ruim = tmp_path / "x.yaml"
    ruim.write_text("{{{ nao e yaml", encoding="utf-8")
    assert config.ler_ajustes(ruim) == {} and config.ler_ajustes(tmp_path / "nao-existe.yaml") == {}


def test_comandos_de_ajustes_pelo_websocket_sao_validados(cfg):
    barramento = Barramento()
    recebidos = []

    async def ler():
        barramento.publicar({"tipo": "ajustes", "valores": {}})

    async def salvar(valores):
        recebidos.append(("salvar", valores))

    async def amostra(voz):
        recebidos.append(("amostra", voz))
        barramento.publicar({"tipo": "fim"})

    async def painel():
        return {"tipo": "painel"}

    controle = Controle(painel=painel, ler_ajustes=ler, salvar_ajustes=salvar, amostra_voz=amostra)
    c = TestClient(criar_app(cfg, token=TOKEN, barramento=barramento, controle=controle))
    with c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
        ws.send_json({"tipo": "ola", "token": TOKEN})
        assert ws.receive_json()["tipo"] == "painel"
        ws.send_json({"tipo": "ajustes"})
        assert ws.receive_json()["tipo"] == "ajustes"
        ws.send_json({"tipo": "salvar_ajustes", "valores": "tudo"})  # não é objeto: ignorado
        ws.send_json({"tipo": "salvar_ajustes", "valores": {"voz.velocidade_fala": 1.1}})
        ws.send_json({"tipo": "amostra_voz", "voz": 3})  # não é texto: ignorado
        ws.send_json({"tipo": "amostra_voz", "voz": "pt_BR-cadu-medium"})
        assert ws.receive_json() == {"tipo": "fim"}
    assert recebidos == [("salvar", {"voz.velocidade_fala": 1.1}), ("amostra", "pt_BR-cadu-medium")]


# ------------------------------------------------------------------ stdin da janela


def test_janela_travada_nao_trava_quem_manda_comando(cfg):
    """Pipe cheio (a janela parou de ler): quem espera é a thread de escrita, não o loop do núcleo."""
    liberar = threading.Event()
    escritos = []

    class StdinTravado:
        def write(self, texto):
            liberar.wait(5)
            escritos.append(texto)

        def flush(self):
            pass

    class ProcFalso:
        stdin = StdinTravado()

        def poll(self):
            return None

    j = Janela(cfg, "http://127.0.0.1:8765/app/", TOKEN)
    j.proc = ProcFalso()  # type: ignore[assignment]
    t = time.perf_counter()
    for _ in range(3):
        j.enviar("bolha")
    assert time.perf_counter() - t < 0.5  # voltou na hora
    liberar.set()
    for _ in range(50):
        if len(escritos) == 3:
            break
        time.sleep(0.05)
    assert escritos == ["bolha\n"] * 3


def test_janela_que_morreu_so_registra(cfg, caplog):
    class ProcQuebrado:
        class stdin:  # noqa: N801
            @staticmethod
            def write(_t):
                raise BrokenPipeError

        def poll(self):
            return None

    j = Janela(cfg, "http://127.0.0.1:8765/app/", TOKEN)
    j.proc = ProcQuebrado()  # type: ignore[assignment]
    j.enviar("mostrar")
    for _ in range(50):
        if "não recebeu" in caplog.text:
            break
        time.sleep(0.05)
    assert "não recebeu 'mostrar'" in caplog.text



def test_arquivo_local_so_muda_as_chaves_da_tela(cfg, monkeypatch, tmp_path):
    """Revisão do PR 8: data/config-local.yaml não vira porta para servidor, modelo ou caminho de arquivo."""
    from vision import ajustes as aj

    # as duas listas andam juntas (mais os interruptores da tela de Conexões, que só aceitam true/false)
    assert config.CHAVES_AJUSTAVEIS == set(aj.POR_CHAVE) | config.CHAVES_LIGA_DESLIGA
    (tmp_path / "raiz" / "data").mkdir(parents=True)
    (tmp_path / "raiz" / "data" / config.AJUSTES_LOCAIS).write_text(
        'servidor.porta: 80\nmodelo.host: "http://outro"\nvoz.voz_piper: "../../x"\nvoz.velocidade_fala: 1.3\n',
        encoding="utf-8")
    monkeypatch.setattr(config, "RAIZ", tmp_path / "raiz")
    monkeypatch.delenv("VISION_SEM_AJUSTES")
    original = config.carregar(config.Path(__file__).resolve().parents[1] / "config.yaml")
    monkeypatch.setenv("VISION_SEM_AJUSTES", "1")
    base = config.carregar(config.Path(__file__).resolve().parents[1] / "config.yaml")
    assert original.get("voz.velocidade_fala") == 1.3
    for chave in ("servidor.porta", "modelo.host", "voz.voz_piper"):
        assert original.get(chave) == base.get(chave)


def test_trocar_de_microfone_nao_empilha_a_lista(cfg_ajustes):
    cfg_ajustes.bruto.setdefault("voz", {})["microfone"] = ["Microfone (C920)", "HyperX", "C920"]
    m = ajustes.validar(cfg_ajustes, {"voz.microfone": "Microfone (HyperX Cloud)"})
    assert m["voz.microfone"] == ["Microfone (HyperX Cloud)", "Microfone (C920)", "C920"]


async def test_no_jogo_o_pre_carregamento_espera_o_jogo_fechar(agente):
    a = agente([])
    await a.descarregar()  # o jogo abriu
    a.llm.carregado = False
    await a.carregar()  # "Hey Vision" ou a subida do núcleo: não volta para a VRAM
    assert not a.llm.carregado
    await a.liberar_modelo()  # o jogo fechou
    assert a.llm.carregado



def test_comandos_de_calibracao_pelo_websocket(cfg):
    barramento = Barramento()
    recebidos = []

    async def calibrar():
        recebidos.append("calibrar")

    async def esquecer(apelido=None):
        recebidos.append(("esquecer", apelido))
        barramento.publicar({"tipo": "fim"})

    async def painel():
        return {"tipo": "painel"}

    controle = Controle(painel=painel, calibrar_ativacao=calibrar, esquecer_apelidos=esquecer)
    c = TestClient(criar_app(cfg, token=TOKEN, barramento=barramento, controle=controle))
    with c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
        ws.send_json({"tipo": "ola", "token": TOKEN})
        assert ws.receive_json()["tipo"] == "painel"
        ws.send_json({"tipo": "calibrar_ativacao"})
        ws.send_json({"tipo": "esquecer_apelidos", "apelido": 5})  # não é texto: ignorado
        ws.send_json({"tipo": "esquecer_apelidos"})
        assert ws.receive_json() == {"tipo": "fim"}
    assert recebidos == ["calibrar", ("esquecer", None)]



async def test_calibracao_so_grava_o_que_foi_sugerido_e_aceito(cfg):
    """Revisão do PR 43: o fim da calibração só SUGERE; gravar é o clique em Aceitar, e só do que foi sugerido."""
    from vision.nucleo import Nucleo
    from vision.voice import comandos

    comandos.definir_apelidos([])
    n = Nucleo(cfg, com_voz=False)
    eventos = []
    n.barramento.publicar = eventos.append
    ouvidos = [{"texto": t, "acordou": False} for t in ("Deliving", "Deliving", "Hey deliving", "", "")]
    await n._fim_calibracao(ouvidos, ["deliving"])
    assert eventos[-1]["sugeridos"] == ["deliving"] and comandos.ler_apelidos(cfg.dados) == []
    await n.aceitar_apelidos(["brasil", "deliving"])  # "brasil" não foi sugerido: fica de fora
    assert comandos.ler_apelidos(cfg.dados) == ["deliving"] and comandos.APELIDOS == {"deliving"}
    await n.aceitar_apelidos(["deliving"])  # a sugestão já foi usada: não aceita de novo
    await n.esquecer_apelidos("deliving")
    assert comandos.ler_apelidos(cfg.dados) == [] and comandos.APELIDOS == set()
    comandos.definir_apelidos([])
