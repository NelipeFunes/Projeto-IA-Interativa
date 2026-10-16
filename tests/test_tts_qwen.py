"""Qwen3-TTS como motor de voz, num processo separado (vision/voice/tts_qwen.py + qwen_worker.py).
Sem GPU e sem modelo: um processo falso (tests/fakes/qwen_trabalhador_falso.py) fala o mesmo protocolo. O processo de
verdade é testado em tests/test_qwen_worker.py, com um módulo falso no lugar do modelo."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from vision.voice.pausas import FIM_S, PAUSA_MAX_S
from vision.voice.tts_qwen import CABECALHO, ErroDaVoz, VozQwen

FALSO = Path(__file__).with_name("fakes") / "qwen_trabalhador_falso.py"


class _Reserva:
    taxa = 12000

    def sintetizar(self, texto, normalizar=True):
        return np.ones(12000, dtype=np.float32)  # 1 s


def _espera(condicao, segundos: float = 15.0) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.05)
    return False


def _e_a_reserva(audio: np.ndarray) -> bool:
    return abs(audio.size - 24000) <= 2  # a reserva (1 s a 12 kHz), reamostrada para 24 kHz


@pytest.fixture
def fabrica(tmp_path):
    criadas: list[VozQwen] = []
    ambiente = {"FAKE_LOG": str(tmp_path / "recebido.jsonl"), "FAKE_MODO": "normal"}

    def criar(modo: str = "normal", **opcoes) -> VozQwen:
        ambiente["FAKE_MODO"] = modo
        (tmp_path / "modelo").mkdir(exist_ok=True)
        (tmp_path / "jarvis.wav").write_bytes(b"RIFF")
        (tmp_path / "jarvis.txt").write_text("texto da gravação", encoding="utf-8")
        opcoes = {"partida_max_s": 30, "prazo_s": 10, "espera_reinicio_s": 0.0, "trabalhador": FALSO,
                  "ambiente_extra": ambiente, **opcoes}
        voz = VozQwen(Path(sys.executable), tmp_path / "modelo", tmp_path / "jarvis.wav", _Reserva(), **opcoes)
        criadas.append(voz)
        return voz

    criar.recebido = lambda: [json.loads(l) for l in (tmp_path / "recebido.jsonl").read_text(encoding="utf-8").splitlines()]
    yield criar
    for voz in criadas:
        voz.fechar()


def test_o_protocolo_do_vision_e_o_do_processo_sao_o_mesmo():
    from vision.voice import qwen_worker

    assert qwen_worker.CABECALHO.format == CABECALHO.format


def test_fala_em_pedacos_com_o_texto_preparado_e_sem_pausa_longa(fabrica):
    voz = fabrica()
    saida = np.concatenate(list(voz.pedacos("Pronto, acendi as luzes do quarto.", normalizar=False)))
    pedidos = [m for m in fabrica.recebido() if m["op"] == "falar"]
    assert [m["texto"] for m in pedidos] == ["Pronto, acendi as luzes do quarto"]  # sem o ponto final; a vírgula fica
    esperado = 0.4 + PAUSA_MAX_S + 0.4 + FIM_S  # começo cortado, pausa limitada, silêncio do fim = FIM_S
    assert abs(saida.size / 24000 - esperado) < 0.05
    assert 0.29 < float(np.abs(saida).max()) <= 0.3 + 1e-4


def test_pode_tirar_as_virgulas_mas_a_da_primeira_palavra_fica(fabrica):
    voz = fabrica(tirar_virgulas=True)
    voz.sintetizar("Não, está tudo certo, pode sair.", normalizar=False)
    assert [m["texto"] for m in fabrica.recebido() if m["op"] == "falar"] == ["Não, está tudo certo pode sair"]


@pytest.mark.parametrize("ganho", [0.5, 1.0, 2.0])
def test_o_limiar_do_compressor_acompanha_o_ganho(ganho, fabrica):
    """Chiado cru de 0,004 nas pausas não pode virar fala só porque o ganho subiu (o compressor vem depois do ganho)."""
    voz = fabrica(ganho=ganho)
    voz._ambiente_extra["FAKE_RUIDO"] = "0.004"
    voz._parar()
    voz._proc, voz._fila = voz._iniciar_processo()  # sobe de novo já com o chiado
    saida = voz.sintetizar("Oi.", normalizar=False)
    assert abs(saida.size / 24000 - (0.4 + PAUSA_MAX_S + 0.4 + FIM_S)) < 0.06


def test_sintetizar_junta_os_pedacos(fabrica):
    voz = fabrica(tirar_virgulas=False, pausa_max_s=None)
    assert voz.sintetizar("Oi.", normalizar=False).size == 2.4 * 24000  # nada cortado: 0,1+0,4+1+0,4+0,5 s
    assert [m["texto"] for m in fabrica.recebido() if m["op"] == "falar"] == ["Oi"]


def test_ganho_e_pico(fabrica):
    voz = fabrica(ganho=2.0)
    saida = voz.sintetizar("Oi.", normalizar=False)
    assert float(np.abs(saida).max()) == pytest.approx(0.6, abs=0.01)
    voz.ganho = 10.0
    assert float(np.abs(voz.sintetizar("Oi.", normalizar=False)).max()) <= 0.98 + 1e-6  # nunca estoura


# ---------- o que o processo recebe ----------

def test_o_processo_nao_recebe_segredos_nem_procura_programa_na_pasta_atual(fabrica, monkeypatch, tmp_path):
    """Revisão do PR 68: ele herdava o ambiente inteiro (senha do Orbit, chave da Tavily) e a pasta atual."""
    for chave in ("ORBIT_PASSWORD", "TAVILY_API_KEY", "ORBIT_TOKEN", "UM_SEGREDO_QUALQUER_PAT"):
        monkeypatch.setenv(chave, "valor-que-nao-pode-vazar")
    pasta = tmp_path / "servidor-voz"
    pasta.mkdir()
    fabrica(cwd=pasta)
    partida = next(m for m in fabrica.recebido() if m["op"] == "partida")
    env = {k.upper() for k in partida["env"]}
    for chave in ("ORBIT_PASSWORD", "TAVILY_API_KEY", "ORBIT_TOKEN", "UM_SEGREDO_QUALQUER_PAT"):
        assert chave not in env
    assert {"PATH", "SYSTEMROOT", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "NODEFAULTCURRENTDIRECTORYINEXEPATH"} <= env
    assert Path(partida["cwd"]).resolve() == pasta.resolve()


def test_a_copia_nivelada_da_gravacao_fica_ao_lado_dela_e_nao_na_pasta_temporaria(fabrica, tmp_path):
    fabrica()
    argv = next(m for m in fabrica.recebido() if m["op"] == "partida")["argv"]
    destino = Path(argv[argv.index("--nivelada-em") + 1])
    assert destino.parent == tmp_path and destino.name.startswith(".")  # junto da gravação (data/voz), sobrescrita a cada partida


def test_o_log_do_processo_e_limitado(fabrica, tmp_path):
    log = tmp_path / "logs" / "qwen3_tts.log"
    log.parent.mkdir()
    log.write_bytes(b"x" * 1_200_000)
    fabrica(log_do_processo=log)
    assert log.with_name("qwen3_tts.log.1").exists()  # girou ao abrir, em vez de crescer para sempre


# ---------- cortar, erros e quedas ----------

def test_cortar_no_meio_cancela_e_o_proximo_pedido_funciona(fabrica):
    voz = fabrica("lento", pausa_max_s=None)
    gerador = voz.pedacos("Uma frase longa.", normalizar=False)
    next(gerador), next(gerador)
    t = time.monotonic()
    gerador.close()  # falou por cima / standby
    assert time.monotonic() - t < 3
    assert voz._trava.acquire(blocking=False)  # soltou a trava
    voz._trava.release()
    assert any(m["op"] == "cancelar" for m in fabrica.recebido())
    assert voz.sintetizar("Outra frase.", normalizar=False).size > 0  # o fluxo não ficou com sobras do pedido cortado


def test_erro_do_pedido_vira_erro_da_voz_e_o_processo_segue_vivo(fabrica):
    voz = fabrica()
    pid = voz._proc.pid
    with pytest.raises(ErroDaVoz, match="falha simulada"):
        voz.sintetizar("quebra tudo", normalizar=False)
    assert voz.sintetizar("Oi.", normalizar=False).size > 0
    assert voz._proc.pid == pid  # não reiniciou à toa


@pytest.mark.parametrize("texto", ["lixo no meio", "tamanho enorme", "audio impar"])
def test_quadro_invalido_derruba_o_fluxo_em_vez_de_reservar_memoria_ou_quebrar(texto, fabrica):
    """Revisão do PR 68: texto cru no stdout virava um cabeçalho com tamanho de 540 MB (a memória era reservada na hora)."""
    voz = fabrica(espera_reinicio_s=60.0)
    with pytest.raises(ErroDaVoz, match="caiu|inválido"):
        voz.sintetizar(texto, normalizar=False)
    assert voz._proc is None  # o processo foi encerrado
    assert _e_a_reserva(voz.sintetizar("Oi.", normalizar=False))  # e a fala seguinte sai pela reserva, sem esperar 30 s


def test_depois_de_uma_queda_a_fala_sai_pela_reserva_enquanto_o_processo_sobe_de_novo(fabrica):
    """Revisão do PR 68: a partida (14 a 22 s) acontecia dentro da fala, com a trava presa: resposta muda."""
    voz = fabrica()
    pid = voz._proc.pid
    voz._ambiente_extra["FAKE_PARTIDA_S"] = "1.5"  # a reinicialização será lenta
    with pytest.raises(ErroDaVoz, match="caiu"):
        voz.sintetizar("morre agora", normalizar=False)
    inicio = time.monotonic()
    saida = voz.sintetizar("Oi.", normalizar=False)
    assert time.monotonic() - inicio < 1.0 and _e_a_reserva(saida)  # não esperou o processo subir
    assert _espera(voz._pronto)  # ele sobe sozinho, em segundo plano
    assert voz.sintetizar("Oi.", normalizar=False).size > 24000 and voz._proc.pid != pid  # a voz volta


def test_depois_de_cair_espera_antes_de_reiniciar(fabrica):
    voz = fabrica(espera_reinicio_s=60.0)
    with pytest.raises(ErroDaVoz, match="caiu"):
        voz.sintetizar("morre agora", normalizar=False)
    assert _e_a_reserva(voz.sintetizar("Oi.", normalizar=False))  # não fica reiniciando a cada frase: a reserva fala
    time.sleep(0.3)
    assert voz._proc is None and not voz._subindo  # e nem tentou subir


def test_processo_que_nao_responde_marca_a_queda_para_nao_reiniciar_na_hora(fabrica):
    voz = fabrica("lento", espera_reinicio_s=60.0)
    voz._prazo_s = 0.0001  # qualquer espera por um quadro estoura
    with pytest.raises(ErroDaVoz, match="sem responder"):
        voz.sintetizar("Uma frase.", normalizar=False)
    assert voz._falhou_em > 0  # marcou: se o processo agora cair de vez, o reinício não é imediato (espera_reinicio_s)


# ---------- modo jogo ----------

def test_modo_jogo_mata_o_processo_fala_com_a_reserva_e_volta(fabrica):
    voz = fabrica()
    processo = voz._proc
    voz.descansar()
    assert processo.poll() is not None and voz._proc is None  # a placa ficou livre de verdade
    assert _e_a_reserva(voz.sintetizar("Oi.", normalizar=False))
    voz.acordar()
    assert voz._pronto() and not voz.descansando
    assert voz.sintetizar("Oi.", normalizar=False).size > 24000


def test_acordar_nao_segura_a_fala_enquanto_o_processo_sobe(fabrica):
    voz = fabrica()
    voz.descansar()
    voz._ambiente_extra["FAKE_PARTIDA_S"] = "1.5"
    acordando = threading.Thread(target=voz.acordar)
    acordando.start()
    time.sleep(0.4)
    inicio = time.monotonic()
    saida = voz.sintetizar("Oi.", normalizar=False)
    assert time.monotonic() - inicio < 1.0 and _e_a_reserva(saida)  # a reserva fala; ninguém espera os ~20 s
    acordando.join(15)
    assert not voz.descansando and voz._pronto()
    assert voz.sintetizar("Oi.", normalizar=False).size > 24000


def test_o_jogo_abrir_enquanto_o_processo_sobe_joga_o_novo_fora(fabrica):
    """O `acordar` ainda subindo quando o jogo abre de novo: o processo novo não pode ficar de pé ocupando a placa."""
    voz = fabrica()
    voz.descansar()
    voz._ambiente_extra["FAKE_PARTIDA_S"] = "1.0"
    acordando = threading.Thread(target=voz.acordar)
    acordando.start()
    time.sleep(0.3)
    voz.descansar()  # o jogo abriu de novo
    acordando.join(15)
    assert voz.descansando and voz._proc is None


def test_acordar_que_falha_continua_descansando(fabrica):
    voz = fabrica()
    voz.descansar()
    voz._ambiente_extra["FAKE_MODO"] = "nao_sobe"
    with pytest.raises(ErroDaVoz, match="não subiu"):
        voz.acordar()
    assert voz.descansando and not voz._subindo  # o laço tenta de novo depois; até lá fala a reserva


def test_processo_que_nao_sobe_levanta_na_criacao(fabrica):
    with pytest.raises(ErroDaVoz, match="não subiu.*sem placa de video"):
        fabrica("nao_sobe")


def test_o_que_falta_levanta_antes_de_subir_o_processo(tmp_path):
    (tmp_path / "jarvis.wav").write_bytes(b"RIFF")
    with pytest.raises(FileNotFoundError, match="transcrição"):  # falta o jarvis.txt
        VozQwen(Path(sys.executable), tmp_path, tmp_path / "jarvis.wav", _Reserva(), trabalhador=FALSO)
    with pytest.raises(FileNotFoundError, match="Python"):
        VozQwen(tmp_path / "nao-existe.exe", tmp_path, tmp_path / "jarvis.wav", _Reserva(), trabalhador=FALSO)


def test_fechar_encerra_o_processo_e_nao_volta(fabrica):
    voz = fabrica()
    processo = voz._proc
    voz.fechar()
    assert processo.poll() is not None and voz._proc is None
    voz.fechar()  # fechar duas vezes não faz mal
    assert _e_a_reserva(voz.sintetizar("Oi.", normalizar=False))  # fechada: só a reserva, e não sobe processo novo
    time.sleep(0.2)
    assert voz._proc is None


def test_o_processo_acaba_sozinho_se_o_vision_morrer(fabrica):
    """O stdin fecha quando o Vision sai (ou cai): o processo não pode ficar órfão ocupando a placa."""
    voz = fabrica()
    processo = voz._proc
    processo.stdin.close()
    assert processo.wait(timeout=10) == 0


# ---------- carregar_voz ----------

def test_carregar_voz_qwen3_sem_referencia_cai_no_piper(monkeypatch, cfg, caplog):
    from vision.voice import tts

    class Piper:
        taxa = 22050

    monkeypatch.setattr(tts, "carregar_piper", lambda _cfg, _nome=None: Piper())
    cfg.bruto.setdefault("voz", {})["motor"] = "qwen3"
    assert isinstance(tts.carregar_voz(cfg), Piper)
    assert "falta voz.referencia" in caplog.text


def test_carregar_voz_qwen3_passa_o_que_o_config_diz(monkeypatch, cfg):
    from vision.voice import tts, tts_qwen

    class Piper:
        taxa = 22050

    recebido: dict = {}

    class Capturada:
        def __init__(self, python, modelo, referencia, reserva, **kw):
            recebido.update(python=python, modelo=modelo, referencia=referencia, reserva=reserva, **kw)

    monkeypatch.setattr(tts, "carregar_piper", lambda _cfg, _nome=None: Piper())
    monkeypatch.setattr(tts_qwen, "VozQwen", Capturada)
    cfg.bruto.setdefault("voz", {}).update(motor="qwen3", referencia="jarvis", qwen3_pausa_max_s=0.2, qwen3_tirar_virgulas=True,
                                          qwen3_ganho="1,5", qwen3_pedaco=4)
    tts.carregar_voz(cfg)
    assert recebido["referencia"] == cfg.dados / "voz" / "jarvis.wav"
    assert recebido["modelo"] == cfg.modelos / "qwen3_tts_0.6b"
    assert recebido["python"] == cfg.raiz / "servidor-voz" / ".venv" / "Scripts" / "python.exe"
    assert (recebido["pausa_max_s"], recebido["tirar_virgulas"], recebido["ganho"], recebido["pedaco"]) == (0.2, True, 1.5, 4)
    assert recebido["log_do_processo"] == cfg.dados / "logs" / "qwen3_tts.log"
    assert recebido["cwd"] in (None, cfg.raiz / "servidor-voz")  # a pasta do ambiente, quando ela existe


def test_descrever_mostra_o_motor(cfg):
    from vision.voice.tts import descrever

    class Fake:
        rotulo, falante = "Qwen3-TTS", "jarvis"

    assert descrever(Fake(), cfg) == "Qwen3-TTS (jarvis)"
