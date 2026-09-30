from datetime import timedelta

import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from jarvis import tempo
from jarvis.brain.agent import Agente
from jarvis.tools.agenda import Agenda
from jarvis.tools.base import Registro
from jarvis.tools.memoria import FerramentasMemoria


@pytest.fixture
def registro(cfg, host, memorias):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    r.adicionar(*FerramentasMemoria(memorias).ferramentas())
    return r


def agente(llm, registro, memorias, tmp_path):
    # O embedder falso (bag-of-words) dá similaridades mais baixas que o real; limiar menor aqui.
    return Agente(
        llm, registro, memorias, pasta_conversas=tmp_path / "conv", perfil=tmp_path / "perfil.md",
        similaridade_minima=0.2,
    )


async def test_conversa_sem_ferramenta(registro, memorias, tmp_path):
    llm = LLMFalso([fala("Oi, Felipe!")])
    r = await agente(llm, registro, memorias, tmp_path).responder("oi jarvis")
    assert r.texto == "Oi, Felipe!" and not r.ferramentas and not r.insistiu


async def test_insiste_quando_inventa_agenda(registro, memorias, tmp_path):
    hoje = tempo.agora().date().isoformat()
    llm = LLMFalso([
        fala("Sua agenda está vazia!"),              # inventou
        chama("agenda_listar", data_inicio=hoje),    # depois do puxão de orelha
        fala("Hoje você tem aula de Cálculo às 19h30."),
    ])
    r = await agente(llm, registro, memorias, tmp_path).responder("qual minha agenda de hoje?")
    assert r.insistiu
    assert r.ferramentas[0]["nome"] == "agenda_listar" and r.ferramentas[0]["ok"]
    assert "Cálculo" in r.texto
    # a mensagem de insistência não fica no histórico da 3ª chamada
    assert not any("ATENÇÃO" in m.get("content", "") for m in llm.chamadas[2])


async def test_escrita_pede_confirmacao_e_executa_no_sim(registro, memorias, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    a = agente(llm, registro, memorias, tmp_path)
    falado: list[str] = []
    r = await a.responder("marca barbeiro amanhã às 16h", ao_texto=falado.append)
    assert r.aguardando_confirmacao
    assert r.texto == "Vou criar 'Barbeiro' amanhã das 16:00 às 17:00. Confirma?"
    assert falado == [r.texto]
    assert not any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)  # ainda não criou

    r2 = await a.responder("pode sim")
    assert r2.texto == "Feito. Criei 'Barbeiro' amanhã das 16:00 às 17:00."
    assert any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)
    assert len(llm.chamadas) == 1  # a confirmação não gastou modelo


async def test_escrita_cancelada_no_nao(registro, memorias, tmp_path, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00")])
    a = agente(llm, registro, memorias, tmp_path)
    await a.responder("marca barbeiro amanhã às 16h")
    r = await a.responder("não")
    assert r.texto == "Beleza, cancelei."
    assert not any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)


async def test_correcao_depois_da_confirmacao_volta_ao_modelo(registro, memorias, tmp_path):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="16:00"),
        chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="17:00"),
    ])
    a = agente(llm, registro, memorias, tmp_path)
    await a.responder("marca barbeiro amanhã às 16h")
    r = await a.responder("não, às 17h")
    assert r.texto == "Vou criar 'Barbeiro' amanhã das 17:00 às 18:00. Confirma?"
    # o histórico que o modelo viu tem a pergunta anterior
    assert any("Confirma?" in (m.get("content") or "") for m in llm.chamadas[1])


async def test_lembrar_nao_pede_confirmacao(registro, memorias, tmp_path):
    llm = LLMFalso([
        chama("lembrar", fato="O Felipe prefere treinar de manhã.", categoria="preferencia"),
        fala("Anotado!"),
    ])
    r = await agente(llm, registro, memorias, tmp_path).responder("lembra que eu prefiro treinar de manhã")
    assert r.texto == "Anotado!" and not r.aguardando_confirmacao
    assert memorias.total() == 1


async def test_memoria_entra_no_prompt(registro, memorias, tmp_path):
    await memorias.lembrar("O Felipe tem uma cachorra chamada Luna.")
    llm = LLMFalso([fala("A Luna!")])
    await agente(llm, registro, memorias, tmp_path).responder("qual o nome da minha cachorra Luna?")
    sistema = llm.chamadas[0][0]["content"]
    assert "cachorra chamada Luna" in sistema


async def test_log_de_conversa(registro, memorias, tmp_path):
    llm = LLMFalso([fala("Oi!")])
    await agente(llm, registro, memorias, tmp_path).responder("oi")
    arquivos = list((tmp_path / "conv").glob("*.jsonl"))
    assert len(arquivos) == 1 and '"jarvis": "Oi!"' in arquivos[0].read_text(encoding="utf-8")


async def test_prazo_estourado_guarda_resposta(registro, memorias, tmp_path):
    import asyncio

    async def lento(_msgs):
        await asyncio.sleep(0.3)
        return fala("Resposta demorada.")

    class LLMLento(LLMFalso):
        async def conversar(self, mensagens, ferramentas, ao_texto=None):
            await asyncio.sleep(0.3)
            return fala("Resposta demorada.")

    a = agente(LLMLento([]), registro, memorias, tmp_path)
    r1 = await a.responder_com_prazo("oi", "alexa", "s1", prazo_s=0.05)
    assert "Me pergunta de novo" in r1.texto
    await asyncio.sleep(0.4)
    r2 = await a.responder_com_prazo("oi de novo", "alexa", "s1", prazo_s=0.05)
    assert r2.texto == "Resposta demorada."
