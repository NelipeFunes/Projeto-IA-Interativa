"""Orçamento do prompt: o que o modelo recebe a cada pedido (esquemas das ferramentas + prompt de sistema).

Em 14/10 o prompt de produção media 7.800 tokens (53 ferramentas): preparar isso do zero custa ~1,8 s a cada pedido
(3,6 mil tokens/s no Qwen3.5 4B na RTX 5060 Ti). Cada ferramenta nova ou regra nova engorda todo pedido, por isso o
tamanho tem teto: passou, ou o texto encolhe (descrições mais curtas, regras duplicadas) ou o teto sobe de propósito.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fakes import calendario_falso, orbit_falso

from vision import tempo
from vision.brain import prompt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import ambiente  # noqa: E402

TETO_ESQUEMAS = 25_000  # caracteres do JSON das ferramentas (hoje ~21.700)
TETO_SISTEMA = 6_000    # caracteres do prompt de sistema com todas as regras (hoje ~4.600)
TETO_CONTEXTO = 1_600   # caracteres da hora + 3 memórias no teto do prompt (fato 300 + assunto 120; hoje ~1.460 no pior caso)
FERRAMENTAS = 53        # o conjunto completo de produção: ferramenta nova => sobe aqui de propósito (e no teto, se preciso)


@pytest.fixture
async def completo(cfg, memorias, tmp_path):
    host = ambiente.host_completo(calendario_falso.criar_servidor(tempo.agora().date()), orbit_falso.criar_servidor())
    async with host:
        yield ambiente.criar(cfg, host, memorias, tmp_path)


async def test_prompt_de_producao_cabe_no_orcamento(completo):
    registro = completo.registro
    assert len(registro.ferramentas) == FERRAMENTAS  # o ambiente de avaliação tem o conjunto completo
    esquemas = len(json.dumps(registro.para_ollama(), ensure_ascii=False))
    agora = datetime.now(tempo.FUSO)
    sistema = len(prompt.montar("Felipe", agora, "", set(registro.ferramentas), confirmacao="sensiveis"))  # modo de produção
    from vision.memory.store import Memoria, Memorias

    pior = [Memoria(i, "a" * 900, "geral", "x", vale_para="b" * 400) for i in range(3)]  # produção: top_k=3; o prompt corta
    contexto = len(prompt.contexto_do_pedido(agora, Memorias.formatar(pior)))
    assert esquemas <= TETO_ESQUEMAS, f"esquemas das ferramentas: {esquemas} caracteres (teto {TETO_ESQUEMAS})"
    assert sistema <= TETO_SISTEMA, f"prompt de sistema: {sistema} caracteres (teto {TETO_SISTEMA})"
    assert contexto <= TETO_CONTEXTO, f"contexto do pedido: {contexto} caracteres (teto {TETO_CONTEXTO})"


async def test_pior_pedido_cabe_no_contexto_do_modelo(completo, cfg):
    """Ferramentas + sistema + histórico no teto + contexto do pedido + uma busca e uma leitura de página NO MESMO pedido
    + a resposta têm de caber em `modelo.contexto` (senão o Ollama corta o começo do prompt, que é o sistema)."""
    from vision.brain.agent import HISTORICO_MAX_CARACTERES, LIMITE_NO_HISTORICO

    registro = completo.registro
    esquemas = len(json.dumps(registro.para_ollama(), ensure_ascii=False))
    agora = datetime.now(tempo.FUSO)
    sistema = len(prompt.montar("Felipe", agora, "", set(registro.ferramentas), confirmacao="sensiveis"))
    contexto = TETO_CONTEXTO
    busca_e_leitura = 3_600 + 2_600   # a busca real da Tavily (~3.500) e uma página lida (web_ler devolve até ~2.500)
    caracteres = esquemas + sistema + HISTORICO_MAX_CARACTERES + contexto + busca_e_leitura
    tokens = caracteres / 3.3 + 300   # 3,3 caracteres por token (medido em 14/10: 3,36) e 300 para a resposta
    assert tokens <= int(cfg.get("modelo.contexto")), f"{tokens:.0f} tokens no pior pedido; contexto {cfg.get('modelo.contexto')}"
    assert LIMITE_NO_HISTORICO["web_buscar"] <= HISTORICO_MAX_CARACTERES // 3
