"""Núcleo (Fase B): eventos do agente para a tela, barramento, agenda no formato do painel, início com o Windows."""

import asyncio
import sys
from datetime import timedelta

import numpy as np
import pytest
from fakes.llm_falso import LLMFalso, chama, fala

from jarvis import inicializacao, tempo
from jarvis.brain.agent import Agente
from jarvis.eventos import Barramento
from jarvis.tools.agenda import Agenda
from jarvis.tools.base import ComDados, Registro
from jarvis.voice.loop import envelope, nivel_do_bloco

# ------------------------------------------------------------------ o agente conta o que faz


@pytest.fixture
def agente_com_eventos(cfg, host):
    def criar(roteiro):
        r = Registro()
        r.adicionar(*Agenda(cfg, host).ferramentas())
        eventos = []
        return Agente(LLMFalso(roteiro), r, None, ao_evento=eventos.append), eventos

    return criar


def _tipos(eventos):
    return [e["tipo"] for e in eventos if e["tipo"] != "resposta_parcial"]


async def test_consulta_da_agenda_de_hoje_vira_eventos_para_a_tela(agente_com_eventos):
    hoje = tempo.agora().date().isoformat()
    agente, ev = agente_com_eventos([chama("agenda_listar", data_inicio=hoje), fala("Hoje tem aula.")])
    await agente.responder("o que tenho hoje?", "voz", "voz", ao_texto=lambda _t: None)
    assert _tipos(ev) == ["fala_usuario", "ferramenta_inicio", "ferramenta_fim", "resposta"]
    fim = next(e for e in ev if e["tipo"] == "ferramenta_fim")
    assert fim["ok"] and isinstance(fim["dados"], list) and fim["dados"]
    assert all({"id", "titulo", "inicio"} <= set(x) for x in fim["dados"])
    assert any(e["tipo"] == "resposta_parcial" for e in ev)  # com ao_texto, a resposta chega aos poucos


async def test_outro_dia_nao_substitui_o_painel_de_hoje(agente_com_eventos):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    agente, ev = agente_com_eventos([chama("agenda_listar", data_inicio=amanha), fala("Amanhã: nada.")])
    await agente.responder("e amanhã?", "texto", "t")
    assert next(e for e in ev if e["tipo"] == "ferramenta_fim")["dados"] is None


async def test_criar_hoje_confirmado_pela_tela(agente_com_eventos, servidor_agenda):
    hoje = tempo.agora().date().isoformat()
    agente, ev = agente_com_eventos([chama("agenda_criar", titulo="Barbeiro", data=hoje, hora_inicio="16:00")])
    r = await agente.responder("marca barbeiro hoje 16h", "voz", "voz")
    assert r.aguardando_confirmacao
    pendente = next(e for e in ev if e["tipo"] == "pendente")["pendente"]
    assert pendente["ferramenta"] == "agenda_criar" and pendente["id"].startswith("p")
    assert pendente["evento"] == {"id": pendente["id"], "titulo": "Barbeiro", "inicio": "16:00", "fim": "17:00"}

    ev.clear()
    r = await agente.resolver_pendente(pendente["id"], True)  # o botão Confirmar da tela
    assert r is not None and r.texto.startswith("Feito. Criei 'Barbeiro'")
    assert _tipos(ev) == ["fala_usuario", "ferramenta_fim", "pendente_resolvido", "resposta"]
    resolvido = next(e for e in ev if e["tipo"] == "pendente_resolvido")
    assert resolvido["resultado"] == "executada"
    assert resolvido["evento"]["id"] != pendente["id"] and resolvido["evento"]["inicio"] == "16:00"  # id real
    assert any(e["summary"] == "Barbeiro" for e in servidor_agenda.eventos)


async def test_cancelar_pela_tela_e_pendencia_que_nao_existe(agente_com_eventos, servidor_agenda):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    agente, ev = agente_com_eventos([chama("agenda_criar", titulo="Dentista", data=amanha, hora_inicio="9:00")])
    antes = len(servidor_agenda.eventos)
    await agente.responder("marca dentista amanhã 9h", "texto", "tela")
    pendente = next(e for e in ev if e["tipo"] == "pendente")["pendente"]
    assert "evento" not in pendente  # amanhã: o painel é de hoje, então sem cartão voando
    assert await agente.resolver_pendente("p999", True) is None
    await agente.resolver_pendente(pendente["id"], False)
    assert next(e for e in ev if e["tipo"] == "pendente_resolvido")["resultado"] == "cancelada"
    assert len(servidor_agenda.eventos) == antes  # nada criado


async def test_mudar_de_assunto_descarta_o_cartao(agente_com_eventos):
    amanha = (tempo.agora().date() + timedelta(days=1)).isoformat()
    agente, ev = agente_com_eventos([chama("agenda_criar", titulo="X", data=amanha), fala("Beleza.")])
    await agente.responder("marca X amanhã", "texto", "t")
    await agente.responder("deixa, me fala uma piada", "texto", "t")
    assert [e["resultado"] for e in ev if e["tipo"] == "pendente_resolvido"] == ["cancelada"]


async def test_tela_com_defeito_nao_derruba_a_conversa(cfg, host):
    def quebra(_ev):
        raise RuntimeError("tela quebrada")

    agente = Agente(LLMFalso([fala("Oi!")]), Registro(), None, ao_evento=quebra)
    assert (await agente.responder("oi")).texto == "Oi!"


# ------------------------------------------------------------------ barramento


async def test_barramento_entrega_lembra_e_nao_trava_com_tela_lenta():
    b = Barramento(tamanho_fila=3)
    vistos = []
    b.ouvir(vistos.append)
    b.ouvir(lambda _e: 1 / 0)  # ouvinte com defeito não impede os outros
    with b.assinar() as fila:
        for i in range(5):
            b.publicar({"tipo": "nivel", "valor": i})
        assert [fila.get_nowait()["valor"] for _ in range(3)] == [2, 3, 4]  # os mais velhos caíram
    assert b.assinantes == 0 and len(vistos) == 5
    b.publicar({"tipo": "estado", "valor": "ouvindo"})
    b.publicar({"tipo": "pendente", "pendente": {"id": "p1"}})
    assert set(b.ultimos) == {"estado", "pendente"}
    b.publicar({"tipo": "pendente_resolvido", "id": "p1", "resultado": "executada"})
    assert set(b.ultimos) == {"estado"}  # quem conectar depois não vê cartão velho


async def test_barramento_de_outra_thread():
    b = Barramento()
    b.ligar(asyncio.get_running_loop())
    with b.assinar() as fila:
        await asyncio.to_thread(b.publicar_de_thread, {"tipo": "estado", "valor": "falando"})
        assert (await asyncio.wait_for(fila.get(), 1))["valor"] == "falando"


# ------------------------------------------------------------------ formatos


def test_agenda_no_formato_do_painel(cfg):
    ag = Agenda(cfg, host=None)
    hoje = tempo.agora().date()
    ev = {"id": "a", "summary": "Aula", "start": {"dateTime": f"{hoje}T19:00:00-03:00"},
          "end": {"dateTime": f"{hoje}T20:30:00-03:00"}, "location": "Sala 3"}
    assert ag.para_tela(ev) == {"id": "a", "titulo": "Aula", "inicio": "19:00", "fim": "20:30", "local": "Sala 3"}
    assert ag.para_tela({**ev, "start": {"dateTime": f"{hoje + timedelta(days=1)}T19:00:00-03:00"}}) is None
    dia = ag.para_tela({"id": "f", "summary": "Feriado", "start": {"date": hoje.isoformat()}, "calendarId": "x#holiday@y"})
    assert dia == {"id": "f", "titulo": "Feriado", "inicio": "00:00", "diaInteiro": True, "feriado": True}


def test_comdados_e_um_texto_com_dados_junto():
    c = ComDados("Evento criado.", {"id": "x"})
    assert c == "Evento criado." and "criado" in c and c.dados == {"id": "x"}


def test_volumes_para_o_orbe():
    assert nivel_do_bloco(np.zeros(1280, np.int16)) == 0.0
    alto = (np.sin(np.linspace(0, 200, 1280)) * 12000).astype(np.int16)
    assert 0.3 < nivel_do_bloco(alto) <= 1.0
    env = envelope(np.concatenate([np.zeros(22050, np.float32), np.ones(22050, np.float32) * 0.5]), 22050)
    assert env.max() == pytest.approx(1.0) and env[0] == 0.0
    assert len(env) == pytest.approx(30, abs=1)  # 2 s a ~15 por segundo
    assert envelope(np.zeros(0, np.float32), 22050).size == 0


# ------------------------------------------------------------------ início com o Windows


def test_atalho_de_inicializacao_sem_montar_comando_com_texto(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    chamadas = []

    def falso_run(cmd, env, **kw):
        chamadas.append((cmd, env))
        inicializacao.atalho().write_text("lnk", encoding="utf-8")

    monkeypatch.setattr(inicializacao.subprocess, "run", falso_run)
    projeto = tmp_path / "pasta com ' aspas; e $(coisas)"
    marcador = tmp_path / "data" / "inicializacao.txt"
    assert inicializacao.primeira_vez(marcador, projeto) is True
    cmd, env = chamadas[0]
    assert inicializacao.ativo()
    assert str(projeto) not in " ".join(cmd)  # o caminho só vai por variável de ambiente
    assert env["VISION_PASTA"] == str(projeto) and env["VISION_LNK"].endswith("Vision.lnk")
    assert inicializacao.primeira_vez(marcador, projeto) is False  # só na primeira vez
    inicializacao.desligar()
    assert not inicializacao.ativo()
    assert inicializacao.primeira_vez(marcador, projeto) is False  # desligado pela bandeja: continua desligado


@pytest.mark.skipif(sys.platform != "win32", reason="mutex do Windows")
def test_so_um_nucleo_por_vez():
    from jarvis.nucleo import InstanciaUnica

    nome = "Local\\VisionTeste-7f3a"
    primeiro = InstanciaUnica(nome)
    assert primeiro.pegar() is True
    assert InstanciaUnica(nome).pegar() is False
