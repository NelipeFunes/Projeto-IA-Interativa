"""Qwen3-TTS como motor de voz, num processo separado (vision/voice/tts_qwen.py + qwen_worker.py).
Sem GPU e sem modelo: um processo falso (tests/fakes/qwen_trabalhador_falso.py) fala o mesmo protocolo."""

from __future__ import annotations

import json
import sys
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


@pytest.fixture
def fabrica(tmp_path, monkeypatch):
    criadas: list[VozQwen] = []
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "recebido.jsonl"))

    def criar(modo: str = "normal", **opcoes) -> VozQwen:
        monkeypatch.setenv("FAKE_MODO", modo)
        (tmp_path / "modelo").mkdir(exist_ok=True)
        (tmp_path / "jarvis.wav").write_bytes(b"RIFF")
        (tmp_path / "jarvis.txt").write_text("texto da gravação", encoding="utf-8")
        opcoes = {"partida_max_s": 30, "prazo_s": 10, "espera_reinicio_s": 0.0, "trabalhador": FALSO, **opcoes}
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


def test_a_gravacao_de_referencia_e_nivelada_antes_de_clonar(tmp_path):
    """O Qwen3-TTS fala no volume da gravação (RMS 0,02 dava voz baixa): o processo nivela a gravação ao carregar."""
    import soundfile as sf

    from vision.voice.qwen_worker import NIVEL_DA_REFERENCIA, PICO_DA_REFERENCIA, nivelar_referencia

    t = np.arange(24000) / 24000
    sf.write(tmp_path / "baixa.wav", (0.03 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000)
    assert nivelar_referencia(str(tmp_path / "baixa.wav"), str(tmp_path / "nivelada.wav"))
    audio, taxa = sf.read(tmp_path / "nivelada.wav", dtype="float32")
    assert taxa == 24000 and float(np.sqrt(np.mean(audio**2))) == pytest.approx(NIVEL_DA_REFERENCIA, rel=0.05)
    # um pico enorme (estalo) não pode estourar: o teto de pico vale mais que o nível
    crua = (0.01 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    crua[100] = 0.9
    sf.write(tmp_path / "pico.wav", crua, 24000)
    assert nivelar_referencia(str(tmp_path / "pico.wav"), str(tmp_path / "pico-n.wav"))
    assert float(np.abs(sf.read(tmp_path / "pico-n.wav", dtype="float32")[0]).max()) <= PICO_DA_REFERENCIA + 1e-3
    # silêncio não tem o que nivelar: devolve False e o processo usa a gravação como veio
    sf.write(tmp_path / "mudo.wav", np.zeros(24000, dtype=np.float32), 24000)
    assert not nivelar_referencia(str(tmp_path / "mudo.wav"), str(tmp_path / "mudo-n.wav"))


def test_fala_em_pedacos_com_o_texto_preparado_e_sem_pausa_longa(fabrica):
    voz = fabrica()
    saida = np.concatenate(list(voz.pedacos("Pronto, acendi as luzes do quarto.", normalizar=False)))
    pedidos = [m for m in fabrica.recebido() if m["op"] == "falar"]
    assert [m["texto"] for m in pedidos] == ["Pronto acendi as luzes do quarto"]  # sem ponto final e sem vírgula
    esperado = 0.4 + PAUSA_MAX_S + 0.4 + FIM_S  # começo cortado, pausa limitada, silêncio do fim = FIM_S
    assert abs(saida.size / 24000 - esperado) < 0.05
    assert 0.29 < float(np.abs(saida).max()) <= 0.3 + 1e-4


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


def test_processo_que_cai_no_meio_da_fala_levanta_e_volta_no_proximo_pedido(fabrica):
    voz = fabrica()
    pid = voz._proc.pid
    with pytest.raises(ErroDaVoz, match="caiu"):
        voz.sintetizar("morre agora", normalizar=False)
    assert voz.sintetizar("Oi.", normalizar=False).size > 0  # reiniciou sozinho
    assert voz._proc.pid != pid


def test_depois_de_cair_espera_antes_de_reiniciar(fabrica):
    voz = fabrica(espera_reinicio_s=60.0)
    with pytest.raises(ErroDaVoz, match="caiu"):
        voz.sintetizar("morre agora", normalizar=False)
    with pytest.raises(ErroDaVoz, match="há pouco"):  # não fica reiniciando a cada frase: a reserva fala
        voz.sintetizar("Oi.", normalizar=False)


def test_modo_jogo_mata_o_processo_fala_com_a_reserva_e_volta(fabrica):
    voz = fabrica()
    processo = voz._proc
    voz.descansar()
    assert processo.poll() is not None and voz._proc is None  # a placa ficou livre de verdade
    saida = voz.sintetizar("Oi.", normalizar=False)
    assert abs(saida.size - 24000) <= 2  # a reserva, reamostrada para 24 kHz
    voz.acordar()
    assert voz._proc is not None and voz._proc.poll() is None and not voz.descansando
    assert voz.sintetizar("Oi.", normalizar=False).size > 24000


def test_acordar_que_falha_continua_descansando(fabrica, monkeypatch):
    voz = fabrica()
    voz.descansar()
    monkeypatch.setenv("FAKE_MODO", "nao_sobe")
    with pytest.raises(ErroDaVoz, match="não subiu"):
        voz.acordar()
    assert voz.descansando  # o laço tenta de novo depois; até lá fala a reserva


def test_processo_que_nao_sobe_levanta_na_criacao(fabrica):
    with pytest.raises(ErroDaVoz, match="não subiu.*sem placa de video"):
        fabrica("nao_sobe")


def test_o_que_falta_levanta_antes_de_subir_o_processo(tmp_path):
    (tmp_path / "jarvis.wav").write_bytes(b"RIFF")
    with pytest.raises(FileNotFoundError, match="transcrição"):  # falta o jarvis.txt
        VozQwen(Path(sys.executable), tmp_path, tmp_path / "jarvis.wav", _Reserva(), trabalhador=FALSO)
    with pytest.raises(FileNotFoundError, match="Python"):
        VozQwen(tmp_path / "nao-existe.exe", tmp_path, tmp_path / "jarvis.wav", _Reserva(), trabalhador=FALSO)


def test_fechar_encerra_o_processo(fabrica):
    voz = fabrica()
    processo = voz._proc
    voz.fechar()
    assert processo.poll() is not None and voz._proc is None
    voz.fechar()  # fechar duas vezes não faz mal


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
    cfg.bruto.setdefault("voz", {}).update(motor="qwen3", referencia="jarvis", qwen3_pausa_max_s=0.2, qwen3_tirar_virgulas=False,
                                          qwen3_ganho="1,5", qwen3_pedaco=4)
    tts.carregar_voz(cfg)
    assert recebido["referencia"] == cfg.dados / "voz" / "jarvis.wav"
    assert recebido["modelo"] == cfg.modelos / "qwen3_tts_0.6b"
    assert recebido["python"] == cfg.raiz / "servidor-voz" / ".venv" / "Scripts" / "python.exe"
    assert (recebido["pausa_max_s"], recebido["tirar_virgulas"], recebido["ganho"], recebido["pedaco"]) == (0.2, False, 1.5, 4)
    assert recebido["log_do_processo"] == cfg.dados / "logs" / "qwen3_tts.log"


def test_descrever_mostra_o_motor(cfg):
    from vision.voice.tts import descrever

    class Fake:
        rotulo, falante = "Qwen3-TTS", "jarvis"

    assert descrever(Fake(), cfg) == "Qwen3-TTS (jarvis)"
