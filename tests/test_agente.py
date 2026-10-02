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


async def test_parar_corta_o_modelo_e_nao_roda_a_ferramenta_seguinte():
    """Falar por cima: o modelo para de escrever, e o que ele pediria depois não roda (pendência de 01/10)."""
    from fakes.llm_falso import LLMFalso, chama, fala

    from vision.tools.base import Ferramenta, Registro, esquema

    rodou = []

    async def acender(_args):
        rodou.append("luz")
        return "acesa"

    r = Registro()
    r.adicionar(Ferramenta("luz_acender", "acende", esquema([]), acender, escrita=True, confirmar=False))
    cortado = [False]

    def depois_de_cortar(_msgs):
        cortado[0] = True  # o Felipe falou enquanto o modelo pensava a 1ª volta
        return chama("luz_acender")

    agente = Agente(LLMFalso([depois_de_cortar, fala("Acendi.")]), r, None, confirmacao="nenhuma")
    resp = await agente.responder("acende a luz", canal="voz", sessao="v", ao_texto=lambda _t: None,
                                  parar=lambda: cortado[0])
    assert resp.interrompido and rodou == []

    class LLMQueTransmite:
        modelo = "falso"

        def __init__(self):
            self.voltas = 0

        async def conversar(self, mensagens, ferramentas, ao_texto=None):
            from vision.brain.llm import RespostaLLM

            self.voltas += 1
            if self.voltas == 1:
                return chama("luz_acender")
            for frase in ("Acendi a luz. ", "Também posso ", "fazer outras coisas."):
                ao_texto(frase)
            return RespostaLLM("".join(["Acendi a luz. ", "Também posso ", "fazer outras coisas."]))

    falado = []
    agente2 = Agente(LLMQueTransmite(), r, None, confirmacao="nenhuma")
    resp2 = await agente2.responder("acende a luz", canal="voz", sessao="v", ao_texto=falado.append,
                                    parar=lambda: len(falado) >= 1)
    assert resp2.interrompido and falado == ["Acendi a luz. "] and rodou == ["luz"]
    assert agente2.sessoes[("voz", "v")].turnos[-1][-1]["content"] == "Acendi a luz. [interrompido]"


async def test_corte_mantem_no_historico_o_que_ja_rodou_e_nao_deixa_pendencia_escondida():
    """Revisão do PR 31: a ferramenta da volta 0 fica no histórico; um "Confirma?" nunca é cortado no meio."""
    from fakes.llm_falso import chama

    from vision.brain.llm import RespostaLLM
    from vision.tools.base import Ferramenta, Registro, esquema

    rodou = []

    async def acender(_args):
        rodou.append("luz")
        return "acesa"

    async def apagar(_args):
        return "apagado"

    async def descrever(_args):
        return "Vou apagar o evento."

    r = Registro()
    r.adicionar(Ferramenta("luz_acender", "acende", esquema([]), acender, escrita=True, confirmar=False,
                           grupo="casa"))
    r.adicionar(Ferramenta("agenda_apagar", "apaga", esquema([]), apagar, escrita=True, sensivel=True,
                           grupo="agenda", descrever=descrever))

    class Transmite:
        modelo = "falso"

        def __init__(self, primeira):
            self.voltas, self.primeira = 0, primeira

        async def conversar(self, mensagens, ferramentas, ao_texto=None):
            self.voltas += 1
            if self.voltas == 1:
                return self.primeira
            ao_texto("Acendi. ")
            ao_texto("E mais uma coisa.")
            return RespostaLLM("Acendi. E mais uma coisa.")

    falado = []
    a = Agente(Transmite(chama("luz_acender")), r, None, confirmacao="sensiveis")
    resp = await a.responder("acende a luz", canal="voz", sessao="v", ao_texto=falado.append,
                             parar=lambda: len(falado) >= 1)
    turno = a.sessoes[("voz", "v")].turnos[-1]
    assert resp.interrompido and [u["nome"] for u in resp.ferramentas] == ["luz_acender"]
    assert any(m.get("role") == "tool" for m in turno) and turno[-1]["content"] == "Acendi. [interrompido]"

    # Pedido que vira "Confirma?": a fala da pergunta não é cortada pelo agente; quem descarta a pendência é o
    # laço de voz (aguardando_confirmacao + interrompido_por), como desde o PR 17.
    cortado = [False]
    b = Agente(Transmite(chama("agenda_apagar")), r, None, confirmacao="sensiveis")

    def marca(_t):
        cortado[0] = True

    resp2 = await b.responder("apaga a reunião de amanhã", canal="voz", sessao="v", ao_texto=marca,
                              parar=lambda: cortado[0])
    assert resp2.aguardando_confirmacao and not resp2.interrompido


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


async def test_standby_digitado_nao_vira_suspender_o_pc():
    """02/10: "entra em standby" na janela fazia o modelo chamar pc_energia (suspender). Despedida nunca é ação."""
    llm = LLMFalso([])  # se o modelo for chamado, o LLMFalso sem respostas quebra o teste
    a = Agente(llm, Registro(), None)
    for frase in ("entra em standby", "Não, não, pode entrar em modo standby."):
        r = await a.responder(frase, "texto", "tela")
        assert r.texto == "Certo, fico em standby. É só me chamar." and r.ferramentas == []
