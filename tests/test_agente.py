from datetime import timedelta

import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from vision import tempo
from vision.brain.agent import Agente
from vision.tools.agenda import Agenda
from vision.tools.base import Registro
from vision.tools.memoria import FerramentasMemoria


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
    r = await agente(llm, registro, memorias, tmp_path).responder("oi vision")
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
        chama("guardar_memoria", fato="O Felipe prefere treinar de manhã.", categoria="preferencia"),
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
    assert len(arquivos) == 1 and '"vision": "Oi!"' in arquivos[0].read_text(encoding="utf-8")


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


async def test_acao_que_ninguem_pediu_vira_confirmacao():
    """02/10: "estou cansado" fez o modelo acender as luzes a 40% sozinho. Agora vira "Confirma?"."""
    from fakes.llm_falso import LLMFalso, chama, fala

    from vision.tools.base import Ferramenta, Registro, esquema, numero

    rodou = []

    async def acender(args):
        rodou.append(args)
        return "acesa"

    r = Registro()
    r.adicionar(Ferramenta("luz_acender", "acende", esquema([], brilho=numero("b")), acender, escrita=True,
                           confirmar=False, grupo="casa", descrever=lambda a: _async("Vou acender a luz.")))
    agente = Agente(LLMFalso([chama("luz_acender", brilho=40), fala("Quer que eu acenda?")]), r, None,
                    confirmacao="sensiveis")
    resp = await agente.responder("estou cansado", canal="voz", sessao="v")
    assert rodou == [] and resp.aguardando_confirmacao

    pedido = Agente(LLMFalso([chama("luz_acender", brilho=40), fala("Acendi."), chama("luz_acender", brilho=80),
                              fala("Pronto.")]), r, None, confirmacao="sensiveis")
    await pedido.responder("acende a luz do quarto", canal="voz", sessao="v")
    assert rodou == [{"brilho": 40}]  # pedido de verdade: roda direto, como antes
    await pedido.responder("mais forte", canal="voz", sessao="v")  # continuação do pedido anterior: vale
    assert rodou == [{"brilho": 40}, {"brilho": 80}]


async def _async(valor):
    return valor



@pytest.mark.parametrize("frase", ["pausa a música", "pula essa música", "próxima música", "toca Queen"])
async def test_controle_de_musica_pedido_nao_vira_confirmacao(frase):
    from fakes.llm_falso import LLMFalso, chama, fala

    from vision.tools.base import Ferramenta, Registro, esquema

    rodou = []

    async def controlar(args):
        rodou.append(args)
        return "ok"

    r = Registro()
    r.adicionar(Ferramenta("musica_controlar", "Pausa, continua ou pula a música.", esquema([]), controlar,
                           escrita=True, confirmar=False, grupo="musica"))
    a = Agente(LLMFalso([chama("musica_controlar"), fala("Feito.")]), r, None, confirmacao="sensiveis")
    resp = await a.responder(frase, canal="voz", sessao="v")
    assert rodou and not resp.aguardando_confirmacao


async def test_sem_descrever_a_pergunta_e_falavel():
    from fakes.llm_falso import LLMFalso, chama, fala

    from vision.tools.base import Ferramenta, Registro, esquema, texto

    async def nada(_args):
        return "ok"

    r = Registro()
    r.adicionar(Ferramenta("timer_cancelar", "Cancela um timer ligado. Use quando...", esquema([], qual=texto("n")),
                           nada, escrita=True, confirmar=False, grupo="timer"))
    a = Agente(LLMFalso([chama("timer_cancelar", qual="café"), fala("?")]), r, None, confirmacao="sensiveis")
    resp = await a.responder("estou cansado", canal="voz", sessao="v")
    assert resp.texto == "Vou fazer isto: cancela um timer ligado (café). Confirma?"
