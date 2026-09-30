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
        "pt": Voz(MODELOS / "piper" / "pt_BR-faber-medium.onnx"),
        "en": Voz(MODELOS / "piper" / "en_US-lessac-medium.onnx"),
        "stt": Transcritor(MODELOS, "parakeet-base-int8"),
    }


def _fala(voz, texto: str) -> np.ndarray:
    from jarvis.voice.stt import reamostrar

    return reamostrar(voz.sintetizar(texto, normalizar=False), voz.taxa, 16000)


def _silencio(s: float) -> np.ndarray:
    return np.zeros(int(16000 * s), dtype=np.float32)


def _loop(pecas, agente, audios, tmp_path):
    from jarvis.voice.audio import ArquivoComoMicrofone, SaidaArquivo
    from jarvis.voice.loop import LoopVoz
    from jarvis.voice.wake import DetectorFala, PalavraAtivacao

    pasta = MODELOS / "openwakeword"
    return LoopVoz(
        agente, pecas["pt"], pecas["stt"], ArquivoComoMicrofone(audios), SaidaArquivo(),
        DetectorFala(pasta / "silero_vad.onnx", silencio_fim_ms=800),
        PalavraAtivacao(pasta, limiar=0.5),
        flag_dormindo=tmp_path / "dormindo.flag", escrever=lambda _t: None, bipes=False,
    )


@pytest.fixture
def registro(cfg, host):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    return r


async def test_hey_jarvis_pergunta_e_resposta_falada(pecas, registro, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula de Cálculo às 19:30.")])
    agente = Agente(llm, registro, None)
    laco = _loop(pecas, agente, [
        _silencio(0.5), _fala(pecas["en"], "Hey Jarvis"), _silencio(0.4),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path)
    await laco.rodar(limite=1)

    assert "agenda" in laco.historico[0]["felipe"].lower()
    assert laco.historico[0]["ferramentas"] == "agenda_listar"
    # o que saiu no "alto-falante" é a resposta, com a hora falada por extenso
    ouvido = pecas["stt"].transcrever(laco.saida.audio(), laco.saida.taxa).lower()
    assert "cálculo" in ouvido or "calculo" in ouvido
    assert "19" in ouvido


async def test_sem_hey_jarvis_nao_faz_nada(pecas, registro, tmp_path):
    agente = Agente(LLMFalso([]), registro, None)
    laco = _loop(pecas, agente, [_fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1)], tmp_path)
    await laco.rodar(limite=1)
    assert laco.historico == [] and laco.saida.trechos == []


async def test_confirmacao_por_voz_sem_repetir_hey_jarvis(pecas, registro, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    agente = Agente(llm, registro, None)
    laco = _loop(pecas, agente, [
        _silencio(0.5), _fala(pecas["en"], "Hey Jarvis"), _silencio(0.4),
        _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.2),
        _fala(pecas["pt"], "Sim, pode."), _silencio(1.5),
    ], tmp_path)
    await laco.rodar(limite=2)

    assert laco.historico[0]["jarvis"].endswith("Confirma?")
    assert "sim" in laco.historico[1]["felipe"].lower()
    assert laco.historico[1]["jarvis"].startswith("Feito. Criei 'Barbeiro'")
    assert any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)


async def test_dormindo_ignora_hey_jarvis(pecas, registro, tmp_path):
    (tmp_path / "dormindo.flag").write_text("1")
    agente = Agente(LLMFalso([]), registro, None)
    laco = _loop(pecas, agente, [_silencio(0.3), _fala(pecas["en"], "Hey Jarvis"), _silencio(2)], tmp_path)
    await laco.rodar(limite=1)
    assert laco.historico == []


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
    assert laco.jogando and agente.llm.descarregado and not laco.ativacao_ligada()


def test_amostras_de_voz_existem():
    amostras = config.RAIZ / "amostras"
    if not amostras.exists():
        pytest.skip("amostras ainda não geradas")
    assert {p.name for p in Path(amostras).glob("voz-*.wav")} >= {"voz-faber.wav", "voz-cadu.wav", "voz-jeff.wav"}
