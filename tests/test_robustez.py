"""Correções vindas da avaliação de 30/09: apelidos de parâmetros e correção depois de 'Confirma?'."""

from datetime import timedelta

from fakes.llm_falso import LLMFalso, chama

from jarvis import tempo
from jarvis.brain.agent import Agente
from jarvis.tools.agenda import Agenda
from jarvis.tools.base import Registro


def test_apelidos_de_parametros(cfg, host):
    ferramentas = {f.nome: f for f in Agenda(cfg, host).ferramentas()}
    assert ferramentas["agenda_apagar"].normalizar_args({"event_id": "x"}) == {"evento_id": "x"}
    assert ferramentas["agenda_criar"].normalizar_args(
        {"title": "A", "date": "2026-10-01", "start_time": "10:00", "location": "casa"}
    ) == {"titulo": "A", "data": "2026-10-01", "hora_inicio": "10:00", "local": "casa"}
    assert ferramentas["agenda_buscar"].normalizar_args({"query": "prova"}) == {"texto": "prova"}
    assert ferramentas["agenda_listar"].normalizar_args({"data_inicial": "2026-10-01"}) == {"data_inicio": "2026-10-01"}
    assert ferramentas["agenda_listar"].normalizar_args({"lixo": 1}) == {}


async def test_apagar_com_event_id_vira_confirmacao(cfg, host):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    a = Agente(LLMFalso([chama("agenda_apagar", event_id="ev_dentista")]), r, None)
    resp = await a.responder("cancela o dentista")
    assert resp.aguardando_confirmacao and resp.texto.startswith("Vou apagar 'Dentista'")


async def test_anunciou_sem_fazer_leva_puxao(cfg, host, servidor_agenda):
    from fakes.llm_falso import fala

    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        fala("Vou colocar na sua agenda academia amanhã das 7h às 8h."),  # vício visto no 9B
        chama("agenda_criar", titulo="Academia", data=amanha, hora_inicio="07:00", hora_fim="08:00"),
    ])
    resp = await Agente(llm, r, None).responder("coloca academia amanhã das 7 às 8")
    assert resp.insistiu and resp.aguardando_confirmacao
    assert resp.texto == "Vou criar 'Academia' amanhã das 07:00 às 08:00. Confirma?"
    puxao = llm.chamadas[1][-1]["content"]
    assert "não chamou nenhuma ferramenta" in puxao


def test_detecta_anuncio():
    from jarvis.brain.intencao import anunciou_sem_fazer

    assert anunciou_sem_fazer("Vou verificar sua agenda da semana que vem.")
    assert anunciou_sem_fazer("Certo, Felipe. Já cancelo o dentista de amanhã às 14h.")
    assert anunciou_sem_fazer("Vou criar 'Barbeiro' sáb 03/10 das 11:00 às 12:00. Confirma?")
    assert anunciou_sem_fazer("Estou buscando seus gastos do mês.")
    assert not anunciou_sem_fazer("Hoje você tem aula de Cálculo às 19h30.")
    assert not anunciou_sem_fazer("Já verifiquei: amanhã você só tem o dentista às 14h.")
    assert not anunciou_sem_fazer("Quer que eu marque um lembrete?")
    assert not anunciou_sem_fazer("Por que o livro de matemática ficou triste? Porque tinha muitos problemas.")


async def test_correcao_avisa_que_nada_foi_criado(cfg, host):
    r = Registro()
    r.adicionar(*Agenda(cfg, host).ferramentas())
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    llm = LLMFalso([
        chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="10:00"),
        chama("agenda_criar", titulo="Barbeiro", data=amanha, hora_inicio="11:00"),
    ])
    a = Agente(llm, r, None)
    await a.responder("marca barbeiro amanhã às 10")
    await a.responder("não, às 11")
    notas = [m["content"] for m in llm.chamadas[1] if m["role"] == "system"][1:]
    assert any("NÃO foi executada" in n for n in notas)
    assert a.sessao("texto", "padrao").nota is None  # a nota vale só para uma volta
