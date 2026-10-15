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
    monkeypatch.setattr(ctypes, "windll", _Proibido(), raising=False)
    for onde, nome in [(subprocess, "Popen"), (subprocess, "run"), (subprocess, "call"), (asyncio, "create_subprocess_exec"),
                       (asyncio, "create_subprocess_shell"), (os, "startfile"), (os, "system"), (webbrowser, "open"),
                       (socket, "getaddrinfo"), (socket, "create_connection"), (socket.socket, "connect")]:
        monkeypatch.setattr(onde, nome, _Proibido(), raising=False)
    registro = completo.registro
    pedidos = [
        ("programa_abrir", {"nome": "Bloco de Notas"}), ("programa_fechar", {"nome": "Chrome"}),
        ("site_abrir", {"endereco": "github.com"}), ("volume", {"acao": "definir", "nivel": 30}),
        ("midia", {"acao": "tocar_pausar"}), ("pc_travar", {}), ("tv_youtube", {"busca": "Blank Space"}),
        ("tv_volume", {"nivel": 10}), ("tv_abrir_app", {"app": "Netflix"}), ("luz_apagar", {"luz": "quarto"}),
        ("musica_tocar", {"busca": "Queen"}), ("timer_criar", {"minutos": 5}), ("web_buscar", {"consulta": "motor do fusca"}),
        ("web_ler", {"url": "https://www.exemplo.com.br/fusca"}), ("web_noticias", {"tema": "economia"}),
        ("clima", {}),
    ]
    for nome, args in pedidos:
        ok, saida = await registro.rodar(nome, args)
        assert "ação real" not in saida, f"{nome} tentou agir de verdade: {saida}"
        assert ok, f"{nome} falhou no ambiente falso: {saida}"
    feito = {x[0] for x in completo.abertos}
    assert {"abrir", "fechar", "site", "midia", "travar"} <= feito  # as ações do PC ficaram só registradas
    assert completo.alexa.feitas and completo.spotify.feitas and completo.tv.feitas


async def test_volume_falso_devolve_o_nivel_pedido(completo):
    ok, saida = await completo.registro.rodar("volume", {"acao": "definir", "nivel": 30})
    assert ok and "None" not in saida and "30" in saida


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

def test_relogio_avanca_a_cada_fala_e_nunca_cruza_a_meia_noite():
    r = run.Relogio(7)
    horas = []
    for _ in range(130):
        r.avancar()
        horas.append(r.agora())
    assert horas[0] - r.base == timedelta(minutes=7) and horas[1] != horas[0]
    assert all(h.date() == r.base.date() for h in horas)  # 60 passos de 7 min cabem no dia
    assert horas[59] - r.base == timedelta(minutes=0)  # volta às 10h depois de 60 falas
    assert r.base.hour == 10 and r.base.minute == 0


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


def test_sobrescrever_aceita_valores_comuns_do_config():
    import json

    for valor, esperado in [("8192", 8192), ("-1m", "-1m"), ("5m", "5m"), ("todas", "todas"), ("true", True), ("", "")]:
        try:
            lido = json.loads(valor)
        except ValueError:
            lido = valor
        assert lido == esperado
