"""A avaliação de produção (evals/): isolamento do ambiente falso, fidelidade à montagem e relógio sintético.

Nasceu da revisão do PR 60: o ambiente "falso" ainda travava a tela, fechava o Chrome e apertava a tecla de mídia de
verdade, e a avaliação não avançava o relógio entre casos (o cache do prompt ficava sempre quente).
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import socket
import subprocess
import sys
import webbrowser
from datetime import timedelta
from pathlib import Path

import pytest
from fakes import calendario_falso, orbit_falso
from fakes.llm_falso import LLMFalso, chama, fala

from vision import tempo
from vision.memory.store import Memorias

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import ambiente  # noqa: E402
import run  # noqa: E402
from casos import Caso  # noqa: E402
from conftest import EmbedderFalso  # noqa: E402

DISPOSITIVOS = {"tv", "musica", "casa"}  # grupos que a montagem só cria com login ou IP (o ambiente os tem sempre)


class _Proibido:
    """No lugar de qualquer coisa que agiria de verdade (tela, processo, rede): levanta."""

    def __getattr__(self, nome):
        raise AssertionError(f"ação real no PC: {nome}")

    def __call__(self, *a, **k):
        raise AssertionError("ação real no PC")


@pytest.fixture
async def completo(cfg, memorias, tmp_path):
    host = ambiente.host_completo(calendario_falso.criar_servidor(tempo.agora().date()), orbit_falso.criar_servidor())
    async with host:
        amb = ambiente.criar(cfg, host, memorias, tmp_path)
        yield amb
        amb.fechar()


# ---------------------------------------------------------------- isolamento

async def test_ferramentas_de_acao_do_ambiente_nao_agem_no_pc_nem_na_internet(completo, monkeypatch):
    from vision.tools import pc as modulo_pc

    monkeypatch.setattr(ctypes, "windll", _Proibido(), raising=False)
    for onde, nome in [(subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (asyncio, "create_subprocess_exec"),
                       (asyncio, "create_subprocess_shell"), (os, "startfile"), (os, "system"), (webbrowser, "open"),
                       (socket, "getaddrinfo"), (socket, "create_connection"), (socket.socket, "connect"),
                       # o ddgs fala pelo `primp` (módulo compilado, fora do `socket`) e a leitura de volume/COM não passa
                       # por nenhum dos de cima: trocados direto
                       (modulo_pc, "_volume"), (modulo_pc, "_ler_volume")]:
        monkeypatch.setattr(onde, nome, _Proibido(), raising=False)
    import ddgs

    monkeypatch.setattr(ddgs, "DDGS", _Proibido())
    registro, tv = completo.registro, completo.tv
    pedidos = [
        ("programa_abrir", {"nome": "Bloco de Notas"}), ("programa_fechar", {"nome": "Chrome"}),
        ("site_abrir", {"endereco": "github.com"}), ("volume", {"acao": "definir", "nivel": 30}),
        ("midia", {"acao": "tocar_pausar"}), ("pc_travar", {}), ("pc_energia", {"acao": "desligar"}),
        ("pc_cancelar_desligamento", {}), ("comando_rodar", {"comando": "Get-Date", "motivo": "teste"}),
        ("tv_youtube", {"busca": "Blank Space"}), ("tv_volume", {"nivel": 10}), ("tv_abrir_app", {"app": "Netflix"}),
        ("tv_controle", {"acao": "desligar"}), ("tv_tocar_midia", {"url": "https://exemplo.com/video.mp4"}),
        ("luz_apagar", {"luz": "quarto"}), ("luz_acender", {"luz": "sala"}), ("musica_tocar", {"busca": "Queen"}),
        ("musica_controlar", {"acao": "pausar"}), ("timer_criar", {"minutos": 5}),
        ("web_buscar", {"consulta": "motor do fusca"}), ("web_ler", {"url": "https://www.exemplo.com.br/fusca"}),
        ("web_noticias", {"tema": "economia"}), ("clima", {}), ("status_do_sistema", {}),
    ]
    saidas = {}
    for nome, args in pedidos:
        ok, saida = await registro.rodar(nome, args)
        assert "ação real" not in saida, f"{nome} tentou agir de verdade: {saida}"
        assert ok, f"{nome} falhou no ambiente falso: {saida}"
        saidas[nome] = saida
    # As respostas são as prontas do ambiente (um caminho real engolido por `except` não deixaria estes rastros):
    assert "boxer" in saidas["web_buscar"] and "Banco Central" in saidas["web_noticias"]
    assert "Cidade Exemplo" in saidas["clima"] and "23%" in saidas["status_do_sistema"]
    assert ("youtube", "dQw4w9WgXcQ") in tv.feitas
    feito = {x[0] for x in completo.abertos}
    assert {"abrir", "fechar", "site", "midia", "travar", "energia", "cancelar_energia", "comando", "volume"} <= feito
    assert completo.alexa.feitas and completo.spotify.feitas and tv.feitas
    # Por identidade: nada do PC ficou ligado à função de verdade (o padrão da definição do `PC` é capturado na criação)
    pc = registro.ferramentas["programa_abrir"].executar.__self__
    assert pc._abrir is not os.startfile and pc._navegador is not webbrowser.open
    assert pc._volume is not modulo_pc._volume and pc._ler_volume is not modulo_pc._ler_volume


async def test_volume_falso_guarda_o_nivel_e_o_mudo(completo):
    r = completo.registro
    assert (await r.rodar("volume", {"acao": "definir", "nivel": 30})) == (True, "Volume em 30%.")
    assert "0%" in (await r.rodar("volume", {"acao": "definir", "nivel": 0}))[1]  # 0 é 0, não o padrão
    ok, saida = await r.rodar("volume", {"acao": "aumentar"})
    assert ok and "10" in saida  # sobe 10 a partir do nível guardado
    assert (await r.rodar("volume", {"acao": "mudo"}))[0]


async def test_status_do_pc_e_fixo(completo):
    ok, saida = await completo.registro.rodar("status_do_sistema", {})
    assert ok and "23%" in saida and "32 GB" in saida


async def test_timer_do_ambiente_usa_o_relogio_da_avaliacao(completo, monkeypatch):
    import time

    sintetico = tempo.agora().replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(tempo, "agora", lambda: sintetico)  # o que `run.avaliar` faz
    ok, saida = await completo.registro.rodar("timer_criar", {"hora": "15:00"})
    assert ok, saida
    (t,) = completo.timers.listar()
    assert 4.9 * 3600 < t.fim - time.time() < 5.1 * 3600  # 15h menos as 10h do relógio sintético


# ---------------------------------------------------------------- fidelidade à montagem

async def test_ambiente_tem_todas_as_ferramentas_da_montagem(cfg, memorias, tmp_path):
    from vision.montagem import montar

    host = ambiente.host_completo(calendario_falso.criar_servidor(tempo.agora().date()), orbit_falso.criar_servidor())
    async with montar(cfg, llm=LLMFalso([]), host=host, memorias=memorias) as j:
        da_montagem = {n for n, f in j.registro.ferramentas.items() if f.grupo not in DISPOSITIVOS}
        amb = ambiente.criar(cfg, host, memorias, tmp_path)
        do_ambiente = {n for n, f in amb.registro.ferramentas.items() if f.grupo not in DISPOSITIVOS}
        amb.fechar()
    assert da_montagem == do_ambiente, (
        f"montagem sem ambiente: {sorted(da_montagem - do_ambiente)}; ambiente sem montagem: {sorted(do_ambiente - da_montagem)}")


# ---------------------------------------------------------------- relógio sintético

@pytest.mark.parametrize("avanco", [0, 7, 14, 15, 20, 59.5, 120])
def test_relogio_avanca_a_cada_fala_e_nunca_cruza_a_meia_noite(avanco):
    r = run.Relogio(avanco)
    horas = []
    for _ in range(400):
        r.avancar()
        horas.append(r.agora())
    assert all(h.date() == r.base.date() for h in horas)  # qualquer avanço fica no mesmo dia (de 10h às 22h59)
    assert r.base.hour == 10 and r.base.minute == 0
    if avanco:
        assert horas[0] - r.base == timedelta(minutes=avanco) and horas[1] != horas[0]


def test_relogio_recusa_avanco_fora_da_faixa():
    for ruim in (-1, 121):
        with pytest.raises(ValueError):
            run.Relogio(ruim)


def test_relogio_de_varios_modelos_divide_o_mesmo_dia():
    base = run.dia_base()
    assert run.Relogio(7, base).base == run.Relogio(7, base).base == base


async def test_casos_seguidos_nao_caem_no_mesmo_minuto(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "criar_memorias", lambda c: Memorias(c.dados / "memoria.db", EmbedderFalso(), duplicata=0.9))
    llm = LLMFalso([fala("A"), fala("B"), fala("C")])
    relogio = run.Relogio(7)
    caso = Caso("um", "conversa", ["Que dia é hoje?"], lambda ctx: [], confirmacao="sensiveis")
    metricas: list = []
    for _ in range(3):
        r = await run.rodar_caso(cfg, llm, caso, True, relogio, False, metricas, None)
        assert r["passou"]
    # Onde o "AGORA" fica (sistema ou mensagem do usuário) depende do desenho do prompt; a hora é o que importa.
    textos = [" ".join(m["content"] for m in chamada if m.get("content")) for chamada in llm.chamadas]
    horas = [t.split("AGORA: ")[1].split(" (fuso")[0].rsplit(", ", 1)[1] for t in textos]
    assert horas == ["10:07", "10:14", "10:21"]  # um relógio só, avançando entre os casos


async def test_falha_de_preparacao_vira_falha_do_caso_e_nao_do_modelo(cfg, monkeypatch):
    def quebra(_cfg):
        raise ConnectionError("embedder fora do ar")

    monkeypatch.setattr(run, "criar_memorias", quebra)
    caso = Caso("x", "conversa", ["oi"], lambda ctx: [])
    r = await run.rodar_caso(cfg, LLMFalso([]), caso, True, run.Relogio(7), False, [], None)
    assert not r["passou"] and "embedder fora do ar" in r["falhas"][0]
    assert r["erro_ambiente"]  # marcado: fica fora das médias de tempo e aparece à parte no relatório


def test_sobrescrever_aceita_valores_comuns_do_config():
    lido = run.ler_sobrescrever(["modelo.contexto=8192", "modelo.manter_carregado=-1m", "x=5m", "a=todas", "b=true", "c="])
    assert lido == {"modelo.contexto": 8192, "modelo.manter_carregado": "-1m", "x": "5m", "a": "todas", "b": True, "c": ""}


@pytest.mark.parametrize("ruim", ["sem_igual", "=valor", "  =valor"])
def test_sobrescrever_recusa_item_sem_chave_ou_sem_igual(ruim):
    with pytest.raises(ValueError):
        run.ler_sobrescrever([ruim])


def test_relatorio_mostra_erros_do_ambiente_e_nao_os_conta_no_tempo():
    ok = {"id": "a", "categoria": "x", "passou": True, "falhas": [], "falas": ["oi"], "respostas": ["oi"], "ferramentas": [[]],
          "insistiu": False, "rep": 0,
          "tempos": [{"total": 2.0, "primeira": 1.0, "prefill": 0.5, "chamadas_llm": 1}]}
    erro = {**ok, "id": "b", "passou": False, "falhas": ["erro preparando o caso: x"], "erro_ambiente": True,
            "tempos": [{"total": 0.0, "primeira": None, "prefill": 0.0, "chamadas_llm": 0}]}
    av = {"modelo": "m", "pensar": False, "vram": "?", "resultados": [ok, erro], "producao": True, "suite": "todas",
          "avanco_min": 7}
    texto = run.relatorio([av])
    assert "**1/2**" in texto and "Erros do ambiente" in texto
    assert "| 2.0s |" in texto  # o 0,0 s do caso que nem rodou não puxa a média para baixo
    assert "20 memórias neutras" in texto
    assert "sem memórias neutras" in run.relatorio([{**av, "producao": False}])
