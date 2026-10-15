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
MIN_FERRAMENTAS = 45


@pytest.fixture
async def completo(cfg, memorias, tmp_path):
    host = ambiente.host_completo(calendario_falso.criar_servidor(tempo.agora().date()), orbit_falso.criar_servidor())
    async with host:
        yield ambiente.criar(cfg, host, memorias, tmp_path)


async def test_prompt_de_producao_cabe_no_orcamento(completo):
    registro = completo.registro
    assert len(registro.ferramentas) >= MIN_FERRAMENTAS  # o ambiente de avaliação tem o conjunto completo
    esquemas = len(json.dumps(registro.para_ollama(), ensure_ascii=False))
    sistema = len(prompt.montar("Felipe", "voz", datetime.now(tempo.FUSO), "", "", set(registro.ferramentas)))
    assert esquemas <= TETO_ESQUEMAS, f"esquemas das ferramentas: {esquemas} caracteres (teto {TETO_ESQUEMAS})"
    assert sistema <= TETO_SISTEMA, f"prompt de sistema: {sistema} caracteres (teto {TETO_SISTEMA})"
