"""Loop de voz de ponta a ponta, sem microfone: WAV sintético entra, áudio sintético sai.

Precisa dos modelos de voz em modelos/ (scripts/baixar_modelos.py); pula se não houver.
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from jarvis import config, tempo
from jarvis.brain.agent import Agente
from jarvis.tools.agenda import Agenda
from jarvis.tools.base import Registro

MODELOS = config.RAIZ / "modelos"
pytestmark = [
    pytest.mark.voz,
    pytest.mark.skipif(not (MODELOS / "piper" / "pt_BR-faber-medium.onnx").exists(), reason="modelos de voz ausentes"),
]


@pytest.fixture(scope="module")
def pecas():
    from jarvis.voice.stt import Transcritor
    from jarvis.voice.tts import Voz

    return {
        "pt": Voz(MODELOS / "piper" / "pt_BR-faber-medium.onnx", deterministico=True),
        "en": Voz(MODELOS / "piper" / "en_US-lessac-medium.onnx", deterministico=True),
        "stt": Transcritor(MODELOS, "parakeet-base-int8"),
    }


def _fala(voz, texto: str) -> np.ndarray:
    from jarvis.voice.stt import reamostrar

    return reamostrar(voz.sintetizar(texto, normalizar=False), voz.taxa, 16000)


def _silencio(s: float) -> np.ndarray:
    return np.zeros(int(16000 * s), dtype=np.float32)


def _chama(pecas):
    """ "Hey Vision" em inglês (a voz pt diz "E vision", que também vale)."""
    return _fala(pecas["en"], "Hey Vision")


def _loop(pecas, agente, audios, tmp_path, **opcoes):
    from jarvis.voice.audio import ArquivoComoMicrofone, SaidaArquivo
    from jarvis.voice.loop import LoopVoz
    from jarvis.voice.wake import DetectorFala, PalavraAtivacao

    pasta = MODELOS / "openwakeword"
    por_modelo = opcoes.get("ativacao_por_texto") is False
    return LoopVoz(
        agente, pecas["pt"], pecas["stt"], ArquivoComoMicrofone(audios), SaidaArquivo(),
        DetectorFala(pasta / "silero_vad.onnx", silencio_fim_ms=800),
        PalavraAtivacao(pasta, limiar=0.5) if por_modelo else None,
        flag_dormindo=tmp_path / "dormindo.flag", escrever=lambda _t: None, bipes=False, **opcoes,
    )


@pytest.fixture
def registro(cfg, host):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    return r


async def test_hey_vision_cumprimenta_e_responde(pecas, registro, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula de Cálculo às 19:30.")])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert {"tipo": "resposta", "texto": "Oi, Felipe. Pode falar."} in eventos  # cumprimentou
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()
    assert laco.historico[0]["ferramentas"] == "agenda_listar"
    assert laco.em_conversa  # continua ouvindo até você mandar desligar
    ouvido = pecas["stt"].transcrever(laco.saida.audio(), laco.saida.taxa).lower()
    assert "pode falar" in ouvido and ("cálcul" in ouvido or "calcul" in ouvido)


async def test_hey_vision_com_o_pedido_junto_responde_sem_cumprimentar(pecas, registro, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula.")])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(0.2),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()
    assert "vision" not in laco.historico[0]["felipe"].lower()  # o nome sai do pedido
    assert not any(e.get("texto") == "Oi, Felipe. Pode falar." for e in eventos)


async def test_sem_hey_vision_nada_acontece_nem_aparece(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.3), _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1),
        _fala(pecas["pt"], "Visita amanhã às três."), _silencio(1),  # "visita" não é o nome
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert laco.historico == [] and laco.saida.trechos == []
    assert eventos == [{"tipo": "estado", "valor": "ocioso"}]  # nem o orbe se mexeu: nada foi para a tela


async def test_conversa_continua_ate_pode_desligar(pecas, registro, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([
        fala("Não consigo gerar esse relatório agora."),
        chama("agenda_listar", data_inicio=hoje),
        fala("Hoje você tem aula às 19:30."),
    ])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Me faz um relatório dos gastos."), _silencio(1.5),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),  # sem repetir o nome
        _fala(pecas["pt"], "Beleza, Vision, pode desligar."), _silencio(1.5),
        _fala(pecas["pt"], "E amanhã, o que eu tenho?"), _silencio(1.5),  # conversa fechada: ignorada
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert [h.get("despedida", False) for h in laco.historico] == [False, False, True]
    assert laco.historico[1]["jarvis"].startswith("Hoje você tem aula")
    assert not laco.em_conversa
    estados = [e["valor"] for e in eventos if e["tipo"] == "estado"]
    assert estados[:4] == ["ocioso", "ouvindo", "falando", "ouvindo"]  # acordou, cumprimentou, ouvindo
    assert estados[-2:] == ["falando", "ocioso"]  # despediu e voltou a esperar


async def test_conversa_fecha_sozinha_depois_do_silencio(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([fala("Tudo certo por aqui.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Tudo bem?"),
        _silencio(4),  # mais que o limite deste teste
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path, silencio_max_s=2.0)
    await laco.rodar()
    assert len(laco.historico) == 1 and not laco.em_conversa


async def test_confirmacao_por_voz_dentro_da_conversa(pecas, registro, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.2),
        _fala(pecas["pt"], "Sim, pode."), _silencio(1.5),
    ], tmp_path)
    await laco.rodar()
    assert laco.historico[0]["jarvis"].endswith("Confirma?")
    assert laco.historico[1]["jarvis"].startswith("Feito. Criei 'Barbeiro'")
    assert any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)


async def test_desligar_com_confirmacao_no_ar_cancela_ela(pecas, registro, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    eventos = []
    agente = Agente(LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")]),
                    registro, None, ao_evento=eventos.append)
    antes = len(servidor_agenda.eventos)
    laco = _loop(pecas, agente, [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.2),
        _fala(pecas["pt"], "Beleza, Vision, pode desligar."), _silencio(1.5),
    ], tmp_path)
    await laco.rodar()
    assert any(e["tipo"] == "pendente_resolvido" and e["resultado"] == "cancelada" for e in eventos)
    assert len(servidor_agenda.eventos) == antes


async def test_escuta_pausada_ignora_hey_vision(pecas, registro, tmp_path):
    (tmp_path / "dormindo.flag").write_text("1")
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.3), _chama(pecas), _silencio(0.3), _fala(pecas["pt"], "Qual é a minha agenda?"), _silencio(2),
    ], tmp_path)
    await laco.rodar()
    assert laco.historico == [] and not laco.em_conversa


async def test_modo_jogo_descarrega_modelo(pecas, registro, tmp_path):
    import asyncio
    import os

    import psutil

    agente = Agente(LLMFalso([]), registro, None)
    laco = _loop(pecas, agente, [_silencio(0.1)], tmp_path)
    eu = psutil.Process(os.getpid()).name()  # finge que o próprio python é o jogo
    tarefa = asyncio.create_task(laco.vigiar_jogos([eu], a_cada_s=0.05))
    await asyncio.sleep(0.5)
    tarefa.cancel()
    assert laco.jogando and agente.llm.descarregado and not laco.escuta_ligada()


async def test_ativacao_por_modelo_continua_funcionando(pecas, registro, tmp_path):
    """`voz.ativacao: modelo`: o openWakeWord com "Hey Jarvis", como antes."""
    laco = _loop(pecas, Agente(LLMFalso([fala("Tudo certo.")]), registro, None), [
        _silencio(0.5), _fala(pecas["en"], "Hey Jarvis"), _silencio(1.5),
        _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
    ], tmp_path, ativacao_por_texto=False)
    await laco.rodar()
    assert len(laco.historico) == 1 and laco.em_conversa


def test_amostras_de_voz_existem():
    amostras = config.RAIZ / "amostras"
    if not amostras.exists():
        pytest.skip("amostras ainda não geradas")
    assert {p.name for p in Path(amostras).glob("voz-*.wav")} >= {"voz-faber.wav", "voz-cadu.wav", "voz-jeff.wav"}


async def test_volume_para_a_tela_e_escuta_pausada(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([fala("Tudo certo.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
        _fala(pecas["pt"], "Beleza, pode desligar."), _silencio(1.5),
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    mic = [e["valor"] for e in eventos if e["tipo"] == "nivel" and e["fonte"] == "mic"]
    assert max(mic) > 0.05 and mic[-1] == 0.0  # volume enquanto ouve, zera ao terminar
    voz = [e for e in eventos if e["tipo"] == "nivel" and e["fonte"] == "voz"]
    assert voz and voz[-1]["valor"] == 0.0  # o orbe para de ondular quando a fala acaba
    (tmp_path / "dormindo.flag").touch()
    laco.reavaliar_estado()
    assert eventos[-1] == {"tipo": "estado", "valor": "dormindo"}


async def test_erro_do_modelo_nao_mata_a_voz(pecas, registro, tmp_path):
    def quebra(_msgs):
        raise ConnectionError("Ollama fora do ar")

    laco = _loop(pecas, Agente(LLMFalso([quebra, fala("Agora sim.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
        _fala(pecas["pt"], "E agora?"), _silencio(1.5),
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert laco.historico[0].get("erro") and laco.historico[1]["jarvis"] == "Agora sim."
    assert any(e == {"tipo": "resposta", "texto": laco.historico[0]["jarvis"]} for e in eventos)
