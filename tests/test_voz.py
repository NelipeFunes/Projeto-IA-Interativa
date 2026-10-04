"""Loop de voz de ponta a ponta, sem microfone: WAV sintético entra, áudio sintético sai.

Precisa dos modelos de voz em modelos/ (scripts/baixar_modelos.py); pula se não houver.
"""

from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from vision import config, tempo
from vision.brain.agent import Agente
from vision.tools.agenda import Agenda
from vision.tools.base import Registro

MODELOS = config.RAIZ / "modelos"
pytestmark = [
    pytest.mark.voz,
    pytest.mark.skipif(not (MODELOS / "piper" / "pt_BR-faber-medium.onnx").exists(), reason="modelos de voz ausentes"),
]


@pytest.fixture(scope="module")
def pecas():
    from vision.voice.stt import Transcritor
    from vision.voice.tts import Voz

    return {
        "pt": Voz(MODELOS / "piper" / "pt_BR-faber-medium.onnx", deterministico=True),
        "en": Voz(MODELOS / "piper" / "en_US-lessac-medium.onnx", deterministico=True),
        "stt": Transcritor(MODELOS, "parakeet-base-int8"),
    }


def _fala(voz, texto: str) -> np.ndarray:
    from vision.voice.stt import reamostrar

    return reamostrar(voz.sintetizar(texto, normalizar=False), voz.taxa, 16000)


def _silencio(s: float) -> np.ndarray:
    return np.zeros(int(16000 * s), dtype=np.float32)


def _chama(pecas):
    """ "Hey Vision" em inglês (a voz pt diz "E vision", que também vale)."""
    return _fala(pecas["en"], "Hey Vision")


def _loop(pecas, agente, audios, tmp_path, **opcoes):
    from vision.voice.audio import ArquivoComoMicrofone, SaidaArquivo
    from vision.voice.loop import LoopVoz
    from vision.voice.wake import DetectorFala, PalavraAtivacao

    pasta = MODELOS / "openwakeword"
    por_modelo = opcoes.get("ativacao_por_texto") is False
    return LoopVoz(
        agente, pecas["pt"], pecas["stt"], ArquivoComoMicrofone(audios), opcoes.pop("saida", None) or SaidaArquivo(),
        DetectorFala(pasta / "silero_vad.onnx", silencio_fim_ms=800),
        PalavraAtivacao(pasta, limiar=0.5) if por_modelo else opcoes.pop("ativacao", None),
        flag_dormindo=tmp_path / "dormindo.flag", escrever=lambda _t: None, bipes=opcoes.pop("bipes", False), **opcoes,
    )


@pytest.fixture
def registro(cfg, host):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    return r


async def test_hey_vision_da_bipe_e_responde(pecas, registro, tmp_path):
    """Pedido de 01/10: "Hey Vision" sozinho não fala "Oi, Felipe"; só dá o bipe e já ouve o pedido."""
    from vision.voice.audio import bipe

    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula de Cálculo às 19:30.")])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path, bipes=True)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert not any(e.get("tipo") == "resposta" and "Felipe" in e.get("texto", "") for e in eventos)
    assert np.array_equal(laco.saida.trechos[0], bipe(subindo=True))  # a 1ª coisa que tocou foi o bipe
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()
    assert laco.historico[0]["ferramentas"] == "agenda_listar"
    assert laco.em_conversa  # continua ouvindo até você mandar desligar
    ouvido = pecas["stt"].transcrever(laco.saida.audio(), laco.saida.taxa).lower()
    assert "pode falar" not in ouvido and ("cálcul" in ouvido or "calcul" in ouvido)


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


async def test_conversa_continua_ate_o_standby(pecas, registro, tmp_path):
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
        _fala(pecas["en"], "Vision standby"), _silencio(1.5),
        _fala(pecas["pt"], "E amanhã, o que eu tenho?"), _silencio(1.5),  # conversa fechada: ignorada
    ], tmp_path)
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert [h.get("despedida", False) for h in laco.historico] == [False, False, True]
    assert laco.historico[1]["vision"].startswith("Hoje você tem aula")
    assert not laco.em_conversa
    estados = [e["valor"] for e in eventos if e["tipo"] == "estado"]
    assert estados[:3] == ["ocioso", "ouvindo", "pensando"]  # acordou com bipe, sem falar nada, e já ouviu
    assert estados[-2:] == ["pensando", "ocioso"]  # sem falar nada: bipe e volta a esperar


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
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert laco.historico[1]["vision"].startswith("Feito. Criei 'Barbeiro'")
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
        _fala(pecas["en"], "Vision standby"), _silencio(1.5),
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
    ], tmp_path, ativacao_por_texto=False, bipes=True)
    await laco.rodar()
    assert len(laco.historico) == 1 and laco.em_conversa
    from vision.voice.audio import bipe

    # Revisão do PR 16: acordar pelo modelo dava dois bipes seguidos (o da saudação e o do laço).
    assert sum(np.array_equal(t, bipe(subindo=True)) for t in laco.saida.trechos) == 1


def test_amostras_de_voz_existem():
    amostras = config.RAIZ / "amostras"
    if not amostras.exists():
        pytest.skip("amostras ainda não geradas")
    assert {p.name for p in Path(amostras).glob("voz-*.wav")} >= {"voz-faber.wav", "voz-cadu.wav", "voz-jeff.wav"}


async def test_volume_para_a_tela_e_escuta_pausada(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([fala("Tudo certo.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
        _fala(pecas["en"], "Vision standby"), _silencio(1.5),
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
    assert laco.historico[0].get("erro") and laco.historico[1]["vision"] == "Agora sim."
    assert any(e == {"tipo": "resposta", "texto": laco.historico[0]["vision"]} for e in eventos)


@pytest.mark.parametrize("como", ["bandeja", "jogo"])
async def test_pausar_no_meio_da_conversa_encerra_ela(pecas, registro, tmp_path, como):
    """Revisão do PR 3: com a conversa aberta, pausar a escuta ou abrir o jogo era ignorado."""
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),  # depois da pausa: não vai a ninguém
    ], tmp_path)
    eventos = []

    def ao_evento(ev):
        eventos.append(ev)
        if ev == {"tipo": "estado", "valor": "ouvindo"}:  # conversa aberta: pausa agora
            if como == "bandeja":
                (tmp_path / "dormindo.flag").touch()
            else:
                laco.jogando = True

    laco.ao_evento = ao_evento
    await laco.rodar()  # o LLM falso não tem roteiro: se a fala chegasse ao modelo, quebraria
    assert laco.historico == [] and not laco.em_conversa
    assert eventos[-1]["valor"] == ("dormindo" if como == "bandeja" else "jogo")


async def test_confirmacao_que_demorou_nao_vale(pecas, registro, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00"), fala("Certo.")])
    antes = len(servidor_agenda.eventos)
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.2),
        _fala(pecas["pt"], "Sim, pode."), _silencio(1.5),  # "tarde demais" com prazo zero
    ], tmp_path, prazo_confirmacao_s=0.0)
    await laco.rodar()
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert len(servidor_agenda.eventos) == antes  # o "sim" atrasado não executou nada


async def test_hey_vision_pode_desligar_com_a_conversa_fechada_nao_faz_nada(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(0.2), _fala(pecas["en"], "Vision standby"), _silencio(1.5),
    ], tmp_path)
    await laco.rodar()
    assert not laco.em_conversa and laco.saida.trechos == []


async def test_hey_vision_sim_nao_confirma_pendencia_de_antes_da_conversa(pecas, registro, tmp_path, servidor_agenda):
    """2ª revisão do PR 3: no jogo o atalho não abre conversa; um "Hey Vision, sim" depois não pode confirmar."""
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00"), fala("Certo.")])
    antes = len(servidor_agenda.eventos)
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.5),
        _chama(pecas), _silencio(0.2), _fala(pecas["pt"], "Sim, pode criar."), _silencio(1.5),
    ], tmp_path)
    laco.jogando = True
    laco.apertou_atalho()

    responder = laco._responder

    async def responder_e_sair_do_jogo(texto, t_stt):
        await responder(texto, t_stt)
        laco.jogando = False  # o jogo fechou logo depois do "Confirma?"

    laco._responder = responder_e_sair_do_jogo
    await laco.rodar()
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert len(laco.historico) == 2 and laco.em_conversa  # o "Hey Vision, sim" chegou e abriu a conversa
    assert len(servidor_agenda.eventos) == antes  # mas não confirmou nada


async def test_hey_jarvis_sim_nao_confirma_pendencia_de_antes(pecas, registro, tmp_path, servidor_agenda):
    """Revisão do PR 4: no modo `modelo`, acordar também descarta a pendência de antes da conversa."""
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00"), fala("Certo.")])
    antes = len(servidor_agenda.eventos)
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."), _silencio(1.5),
        _fala(pecas["en"], "Hey Jarvis"), _silencio(1.5), _fala(pecas["pt"], "Sim, pode criar."), _silencio(1.5),
    ], tmp_path, ativacao_por_texto=False)
    laco.jogando = True
    laco.apertou_atalho()
    responder = laco._responder

    async def responder_e_sair_do_jogo(texto, t_stt):
        await responder(texto, t_stt)
        laco.jogando = False

    laco._responder = responder_e_sair_do_jogo
    await laco.rodar()
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert len(laco.historico) == 2 and laco.em_conversa  # acordou pelo modelo e ouviu o "sim"
    assert len(servidor_agenda.eventos) == antes  # mas não confirmou nada


async def test_stt_que_falha_na_fala_inteira_mantem_o_pedido_do_comeco(pecas, registro, tmp_path):
    """Revisão do PR 4: se só a 2ª transcrição de "Hey Vision, <pedido>" falha, o começo já ouvido vale."""
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [], tmp_path)
    chamadas, pedidos = [], []

    class SttQueFalhaNaSegunda:
        def transcrever(self, pcm, taxa):
            chamadas.append(pcm.size)
            if len(chamadas) == 2:
                raise IndexError("falha simulada do Parakeet")
            return "Hey Vision, qual é a minha agenda"

    async def responder(texto, t_stt):
        pedidos.append(texto)

    laco.stt, laco._responder = SttQueFalhaNaSegunda(), responder
    await laco._candidato(_silencio(4.0))  # mais longa que o trecho de 2,5 s: transcreve duas vezes
    assert len(chamadas) == 2 and laco.em_conversa
    assert pedidos == ["qual é a minha agenda"]  # o pedido do começo, e não só a saudação


async def test_despedida_configurada_fala_e_depois_bipa(pecas, registro, tmp_path):
    from vision.voice.audio import bipe_desligar

    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["en"], "Vision standby"),
        _silencio(1.5),
    ], tmp_path, despedida="Até mais.")
    laco.bipes = True
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert {"tipo": "resposta", "texto": "Até mais."} in eventos
    assert np.array_equal(laco.saida.trechos[-1], bipe_desligar())


async def test_standby_so_da_o_bipe_sem_falar(pecas, registro, tmp_path):
    from vision.voice.audio import bipe_desligar

    laco = _loop(pecas, Agente(LLMFalso([fala("Tudo certo.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
        _fala(pecas["en"], "Vision standby"), _silencio(1.5),
    ], tmp_path)
    laco.bipes = True
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert not laco.em_conversa and laco.historico[-1]["despedida"]
    assert np.array_equal(laco.saida.trechos[-1], bipe_desligar())  # o último som é o bipe de desligar
    # O próprio laço (fora do modelo) não falou nada: nem saudação nem despedida.
    assert [e["texto"] for e in eventos if e["tipo"] == "resposta"] == []
    assert laco.historico[-1]["vision"] == ""


async def test_resposta_confirmada_pela_tela_e_falada_pelo_laco(pecas, registro, tmp_path):
    """Fase C: pedido por voz, confirmado no botão da tela: a resposta sai no alto-falante, pelo próprio laço."""
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [_silencio(1.0)], tmp_path)
    laco.pedir_fala("Feito. Criei o barbeiro para amanhã.")
    await laco.rodar()
    assert "amanh" in pecas["stt"].transcrever(laco.saida.audio(), laco.saida.taxa).lower()
    assert laco.historico == []  # não virou fala sua


async def test_com_a_escuta_pausada_a_resposta_da_tela_nao_e_falada(pecas, registro, tmp_path):
    (tmp_path / "dormindo.flag").touch()
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [_silencio(1.0)], tmp_path)
    laco.pedir_fala("Feito.")
    await laco.rodar()
    assert laco.saida.trechos == []


async def test_fala_de_fora_que_falha_nao_derruba_a_escuta(pecas, registro, tmp_path):
    """Revisão do PR 8: um erro ao falar a resposta da tela (ou a amostra) não pode matar o laço."""
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula.")])
    laco = _loop(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(0.2), _fala(pecas["pt"], "Qual é a minha agenda de hoje?"),
        _silencio(1.5),
    ], tmp_path)

    class VozQuebrada:
        taxa = 22050

        def sintetizar(self, _texto):
            raise RuntimeError("falha simulada do Piper")

    laco.pedir_fala("Feito.", VozQuebrada())
    await laco.rodar()
    assert len(laco.historico) == 1  # continuou ouvindo e respondeu depois da falha
    assert not isinstance(laco.voz, VozQuebrada)


LONGA = "Amanhã o dia começa às sete. Depois há uma reunião curta. À noite não há nenhum compromisso marcado."


def _vigia():
    from vision.voice.wake import DetectorFala

    return DetectorFala(MODELOS / "openwakeword" / "silero_vad.onnx", 500, 60)


def _loop_que_escuta_falando(pecas, registro, tmp_path, interrupcao, depois=()):
    from vision.voice.audio import SaidaArquivo

    return _loop(pecas, Agente(LLMFalso([fala(LONGA), *depois]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Como é meu dia?"), _silencio(1.0),
        interrupcao, _silencio(2.0),
    ], tmp_path, saida=SaidaArquivo(tempo_real=True), vigia=_vigia())


async def test_falar_por_cima_corta_e_vira_o_proximo_pedido(pecas, registro, tmp_path):
    """Pedido de 01/10, como o modo de voz do ChatGPT: você fala, ele para e ouve o que você disse."""
    laco = _loop_que_escuta_falando(pecas, registro, tmp_path, _fala(pecas["pt"], "Tudo bem com você?"),
                                    depois=[fala("Tudo ótimo por aqui.")])
    await laco.rodar()
    assert laco.historico[0]["interrompido"] == "fala"
    assert len(laco.saida.trechos) == 2  # a 1ª frase dele (cortada) e a resposta nova; as outras nem tocaram
    assert "tudo" in laco.historico[1]["felipe"].lower()  # nada do que você disse se perdeu
    assert laco.historico[1]["vision"] == "Tudo ótimo por aqui." and laco.em_conversa


async def test_para_de_falar_so_para(pecas, registro, tmp_path):
    laco = _loop_que_escuta_falando(pecas, registro, tmp_path, _fala(pecas["pt"], "Para de falar."))
    await laco.rodar()  # o LLM falso não tem 2ª resposta: se "para de falar" fosse ao modelo, viraria erro
    assert laco.historico[0]["interrompido"] == "fala" and laco.historico[1].get("parou")
    assert len(laco.saida.trechos) == 1 and laco.em_conversa


async def test_standby_por_cima_corta_e_fecha(pecas, registro, tmp_path):
    laco = _loop_que_escuta_falando(pecas, registro, tmp_path, _fala(pecas["en"], "Vision standby"))
    await laco.rodar()
    assert laco.historico[0]["interrompido"] == "fala" and laco.historico[1].get("despedida")
    assert not laco.em_conversa


async def test_tosse_curta_nao_corta(pecas, registro, tmp_path):
    """Menos que `interromper_apos_s` de voz (aqui 0,3 s de fala) não corta."""
    hum = _fala(pecas["pt"], "Tudo bem com você?")[: int(16000 * 0.3)]
    laco = _loop_que_escuta_falando(pecas, registro, tmp_path, hum)
    await laco.rodar()
    assert laco.historico[0]["interrompido"] is None and len(laco.saida.trechos) == 3


async def test_cortar_o_confirma_cancela_a_pendencia(pecas, registro, tmp_path, servidor_agenda):
    """Revisão do PR 17: cortado antes do "Confirma?" inteiro, um "sim" depois não pode valer."""
    from vision.voice.audio import SaidaArquivo

    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    agente = Agente(LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")]),
                    registro, None)
    laco = _loop(pecas, agente, [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Marca barbeiro amanhã às quatro."),
        _silencio(1.0), _fala(pecas["pt"], "Para de falar."), _silencio(2.0),
    ], tmp_path, saida=SaidaArquivo(tempo_real=True), vigia=_vigia())
    await laco.rodar()
    assert laco.historico[0]["interrompido"] == "fala"
    assert agente.sessao("voz", "voz").pendente is None and laco.pergunta_em is None


async def test_falar_por_cima_de_um_lembrete_so_para_e_nao_abre_conversa(pecas, registro, tmp_path):
    """Revisão do PR 18: com a conversa fechada, voz por cima (até a TV) só para a fala; sem "Hey Vision", nada
    vai ao modelo."""
    from vision.voice.audio import SaidaArquivo

    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.3), _fala(pecas["pt"], "Tudo bem com você?"), _silencio(2.0),
    ], tmp_path, saida=SaidaArquivo(tempo_real=True), vigia=_vigia())
    laco.pedir_fala(LONGA)
    await laco.rodar()
    assert len(laco.saida.trechos) == 1 and laco.interrompido_por == "fala"
    assert not laco.em_conversa and laco.historico == []


async def test_erro_na_voz_numa_resposta_nao_derruba_a_escuta(pecas, registro, tmp_path):
    """01/10: um erro do XTTS numa resposta longa derrubou o laço de voz até reiniciar o Vision."""

    class VozQueQuebra:
        def __init__(self, voz):
            self.voz, self.taxa = voz, voz.taxa

        def sintetizar(self, texto, normalizar=True):
            if "Quebra" in texto:
                raise ImportError("falha simulada da voz")
            return self.voz.sintetizar(texto, normalizar)

    laco = _loop(pecas, Agente(LLMFalso([fala("Quebra aqui."), fala("Agora sim.")]), registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5),
        _fala(pecas["pt"], "Tudo bem?"), _silencio(1.5),
        _fala(pecas["pt"], "E agora?"), _silencio(1.5),
    ], tmp_path)
    laco.voz = VozQueQuebra(pecas["pt"])
    await laco.rodar()
    assert laco.historico[-1]["vision"] == "Agora sim."  # a 2ª pergunta ainda foi ouvida e respondida


@pytest.mark.parametrize("ligado", [False, True])
async def test_registrar_ativacao_so_escreve_o_que_ouviu_quando_ligado(pecas, registro, tmp_path, ligado):
    """voz.registrar_ativacao (diagnóstico de 02/10): desligado, a fala que não é com ele não vai a lugar nenhum."""
    linhas = []
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.5), _fala(pecas["pt"], "Bom dia, tudo bem com você?"), _silencio(1.5),
        _chama(pecas), _silencio(1.5),
    ], tmp_path, registrar_ativacao=ligado)
    laco.escrever = linhas.append
    await laco.rodar()
    assert laco.em_conversa  # o "Hey Vision" acordou nos dois casos
    ativacao = [x for x in linhas if x.startswith("(ativação)")]
    if not ligado:
        assert ativacao == [] and not any("bom dia" in x.lower() for x in linhas)
    else:
        assert len(ativacao) == 2 and "não acordou" in ativacao[0] and ativacao[1].endswith("acordou")



async def test_calibracao_ouve_cada_vez_e_devolve_o_que_entendeu(pecas, registro, tmp_path):
    """Calibração (02/10): bipe, uma fala, o começo transcrito; quem não falou vira "" (e conta como não ouvido)."""
    from vision.voice import comandos

    comandos.definir_apelidos([])
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.3), _chama(pecas), _silencio(1.2), _chama(pecas), _silencio(7.0),
    ], tmp_path)
    resultado = {}

    def ao_fim(ouvidos, aprendidos):
        resultado.update(ouvidos=ouvidos, aprendidos=aprendidos)

    assert laco.pedir_calibracao(3, ao_fim) is None
    assert laco.pedir_calibracao(3, ao_fim) == "A calibração já está rodando."
    eventos = []
    laco.ao_evento = eventos.append
    await laco.rodar()
    assert [o["acordou"] for o in resultado["ouvidos"]] == [True, True, False]
    assert resultado["ouvidos"][2]["texto"] == "" and resultado["aprendidos"] == []
    etapas = [e["etapa"] for e in eventos if e.get("tipo") == "calibracao"]
    assert etapas == [1, 2, 3]
    assert not laco.em_conversa and laco.historico == []  # nada foi tratado como pedido


async def test_calibracao_recusa_com_a_escuta_pausada(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [_silencio(0.5)], tmp_path)
    (tmp_path / "dormindo.flag").write_text("1", encoding="utf-8")
    assert "pausada" in laco.pedir_calibracao(5, lambda *_a: None)



async def test_pausar_no_meio_cancela_a_calibracao(pecas, registro, tmp_path):
    """Revisão do PR 43: pausa é pausa, mesmo no meio da calibração."""
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.3), _chama(pecas), _silencio(1.2), _chama(pecas), _silencio(2.0),
    ], tmp_path)
    fim = []
    eventos = []
    laco.ao_evento = eventos.append
    assert laco.pedir_calibracao(3, lambda o, a: fim.append(o)) is None

    class SttQuePausa:  # depois da 1ª vez, alguém pausa a escuta na bandeja
        def transcrever(self, _pcm, _taxa=None):
            (tmp_path / "dormindo.flag").write_text("1", encoding="utf-8")
            return "Hey Vision"

    laco.stt = SttQuePausa()  # próprio do teste: o STT de `pecas` é compartilhado
    await laco.rodar()
    assert fim == [] and not laco.calibrando
    assert any("cancelada" in (e.get("erro") or "") for e in eventos if e.get("tipo") == "calibracao")


async def test_calibracao_so_com_ativacao_por_transcricao(pecas, registro, tmp_path):
    laco = _loop(pecas, Agente(LLMFalso([]), registro, None), [_silencio(0.5)], tmp_path, ativacao_por_texto=False)
    assert "transcrição" in laco.pedir_calibracao(5, lambda *_a: None)


# ------------------------------------------------------------------ "ambos": modelo + transcrição (03/10)


class ModeloFalso:
    """No lugar do openWakeWord: "reconhece o nome" uma vez, quando o microfone passa de `em_s` segundos de áudio."""

    def __init__(self, em_s: float | None):
        self.em_s = em_s
        self.entrada = None
        self.ultimo_score = 0.0
        self.disparos = 0

    def ouvir(self, _bloco) -> bool:
        if self.em_s is None or self.disparos or self.entrada._pos < self.em_s * 16000:
            return False
        self.disparos += 1
        self.ultimo_score = 0.9
        return True

    def zerar(self) -> None:
        pass


def _com_modelo(pecas, agente, audios, tmp_path, em_s, **opcoes):
    modelo = ModeloFalso(em_s)
    laco = _loop(pecas, agente, audios, tmp_path, ativacao=modelo, **opcoes)
    modelo.entrada = laco.entrada
    return laco, modelo


async def test_ambos_modelo_reconhece_no_meio_da_fala_e_o_pedido_e_o_que_vem_depois(pecas, registro, tmp_path):
    """O STT escreve o seu "Vision" como "Deliving" (não acorda pelo texto), mas o modelo reconhece o som: o que vem
    depois do nome é o pedido, sem o "Deliving"."""
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula.")])
    chamado = _fala(pecas["en"], "Hey Deliving")
    fim_do_nome = 0.5 + chamado.size / 16000 - 0.05
    laco, modelo = _com_modelo(pecas, Agente(llm, registro, None), [
        _silencio(0.5), chamado, _silencio(0.3), _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path, fim_do_nome, bipes=True)
    await laco.rodar()
    assert modelo.disparos == 1 and laco.em_conversa
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()
    assert "deliv" not in laco.historico[0]["felipe"].lower()


async def test_ambos_sem_o_modelo_pegar_a_transcricao_ainda_acorda(pecas, registro, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula.")])
    laco, modelo = _com_modelo(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(0.2), _fala(pecas["pt"], "Qual é a minha agenda de hoje?"),
        _silencio(1.5),
    ], tmp_path, None)
    await laco.rodar()
    assert modelo.disparos == 0
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()


async def test_ambos_modelo_no_silencio_abre_a_conversa_com_bipe(pecas, registro, tmp_path):
    from vision.voice.audio import bipe

    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([chama("agenda_listar", data_inicio=hoje), fala("Hoje você tem aula.")])
    laco, modelo = _com_modelo(pecas, Agente(llm, registro, None), [
        _silencio(0.6), _silencio(0.6), _fala(pecas["pt"], "Qual é a minha agenda de hoje?"), _silencio(1.5),
    ], tmp_path, 0.4, bipes=True)
    await laco.rodar()
    assert modelo.disparos == 1 and np.array_equal(laco.saida.trechos[0], bipe(subindo=True))
    assert len(laco.historico) == 1 and "agenda" in laco.historico[0]["felipe"].lower()


async def test_ambos_com_a_escuta_pausada_o_modelo_nao_acorda(pecas, registro, tmp_path):
    laco, modelo = _com_modelo(pecas, Agente(LLMFalso([]), registro, None), [
        _silencio(0.5), _fala(pecas["en"], "Hey Deliving"), _silencio(1.0),
    ], tmp_path, 0.7)
    (tmp_path / "dormindo.flag").write_text("1", encoding="utf-8")
    await laco.rodar()
    assert modelo.disparos == 0 and not laco.em_conversa and laco.historico == []


def test_carregar_ativacao_sem_o_modelo_fica_so_a_transcricao(cfg, tmp_path):
    from vision.voice.loop import carregar_ativacao

    avisos = []
    cfg.modelos = tmp_path  # sem modelos/openwakeword/hey_vision.onnx
    cfg.bruto.setdefault("voz", {}).update({"ativacao": "ambos", "palavra_ativacao": "hey_vision"})
    assert carregar_ativacao(cfg, avisos.append) == (None, True)
    assert "fica só a transcrição" in avisos[0]
    cfg.bruto["voz"]["ativacao"] = "modelo"
    assert carregar_ativacao(cfg, avisos.append) == (None, False) and "atalho" in avisos[1]
    cfg.bruto["voz"]["ativacao"] = "transcricao"
    assert carregar_ativacao(cfg, avisos.append) == (None, True) and len(avisos) == 2


@pytest.mark.skipif(not (MODELOS / "openwakeword" / "hey_jarvis_v0.1.onnx").exists(), reason="sem openWakeWord")
def test_verificador_da_voz_e_carregado_quando_existe(tmp_path):
    """O .pkl ao lado do .onnx (scripts/ativacao/gravar_minha_voz.py) liga o verificador; sem ele, só o modelo."""
    import pickle
    import shutil

    from openwakeword.custom_verifier_model import train_verifier_model

    from vision.voice.wake import SUFIXO_VERIFICADOR, PalavraAtivacao

    pasta = tmp_path / "openwakeword"
    shutil.copytree(MODELOS / "openwakeword", pasta, ignore=shutil.ignore_patterns("*.tflite"))
    assert not PalavraAtivacao(pasta, "hey_jarvis").com_verificador
    rnd = np.random.default_rng(0)
    verificador = train_verifier_model(rnd.normal(size=(20, 16, 96)), np.array([1, 0] * 10))
    with (pasta / f"hey_jarvis_v0.1{SUFIXO_VERIFICADOR}").open("wb") as f:
        pickle.dump(verificador, f)
    ativacao = PalavraAtivacao(pasta, "hey_jarvis")
    assert ativacao.com_verificador and ativacao.nome == "hey_jarvis_v0.1"
    assert ativacao.ouvir(np.zeros(1280, np.int16)) is False


async def test_ambos_acordar_pelo_modelo_nao_confirma_pendencia_de_antes(pecas, registro, tmp_path, servidor_agenda):
    """Revisão do PR 45: como no "Hey Vision" pelo texto, acordar pelo modelo descarta a pendência de antes."""
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00"), fala("Certo.")])
    antes = len(servidor_agenda.eventos)
    pedido = _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde.")
    chamado = _fala(pecas["en"], "Hey Deliving")
    fim_do_nome = (0.5 + pedido.size / 16000 + 1.5) + chamado.size / 16000 - 0.05
    laco, modelo = _com_modelo(pecas, Agente(llm, registro, None), [
        _silencio(0.5), pedido, _silencio(1.5), chamado, _silencio(0.3), _fala(pecas["pt"], "Sim, pode criar."),
        _silencio(1.5),
    ], tmp_path, fim_do_nome)
    laco.jogando = True  # o pedido de antes veio pelo atalho, no modo jogo (sem abrir conversa)
    laco.apertou_atalho()
    responder = laco._responder

    async def responder_e_sair_do_jogo(texto, t_stt):
        await responder(texto, t_stt)
        laco.jogando = False

    laco._responder = responder_e_sair_do_jogo
    await laco.rodar()
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert modelo.disparos == 1 and laco.em_conversa
    assert len(servidor_agenda.eventos) == antes  # o "sim" depois do nome não confirmou a pendência de antes


async def test_ambos_com_a_conversa_aberta_o_modelo_nem_ouve_e_o_sim_confirma(pecas, registro, tmp_path,
                                                                                servidor_agenda):
    """Revisão do PR 45: com a conversa aberta, tudo vai para a conversa; o modelo não é consultado e não cancela a
    confirmação que você está respondendo."""
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    antes = len(servidor_agenda.eventos)
    laco, modelo = _com_modelo(pecas, Agente(llm, registro, None), [
        _silencio(0.5), _chama(pecas), _silencio(1.5), _fala(pecas["pt"], "Marca barbeiro amanhã às quatro da tarde."),
        _silencio(1.2), _fala(pecas["pt"], "Sim, pode."), _silencio(1.5),
    ], tmp_path, 3.0)  # "dispararia" no meio da conversa, se fosse consultado
    await laco.rodar()
    assert modelo.disparos == 0
    assert laco.historico[0]["vision"].endswith("Confirma?")
    assert laco.historico[1]["vision"].startswith("Feito. Criei 'Barbeiro'")
    assert len(servidor_agenda.eventos) == antes + 1
