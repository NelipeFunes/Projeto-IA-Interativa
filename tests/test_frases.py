"""`vision.frases`: a limpeza de texto de comando de voz que morava em `tools/pc.py` e estava copiada em 8 pontos."""

from __future__ import annotations

import pytest

from vision.frases import frase_limpa, normalizar


@pytest.mark.parametrize("entrada,esperado", [
    ("Aumenta o Volume!", "aumenta o volume"),
    ("  Põe   a Música  ", "poe a musica"),
    ("toca queen.", "toca queen."),            # normalizar mantém o ponto (nomes como "3.5" e "v1.2")
    ("volume +10", "volume +10"),
    ("canal 5, por favor?", "canal 5 por favor"),
    ("ÇÃO é ação", "cao e acao"),
    ("", ""),
])
def test_normalizar(entrada, esperado):
    assert normalizar(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("toca queen.", "toca queen"),             # frase_limpa tira também o ponto: casa os comandos curtos
    ("Volume 30!", "volume 30"),
    ("Desliga a TV, por favor.", "desliga a tv por favor"),
    ("  tudo   junto?? ", "tudo junto"),
    ("volume +10", "volume +10"),             # o + e o - ficam (volume +10, canal -1)
    ("versão 3.5", "versao 3 5"),              # o ponto vira espaço, até no meio de um número
    ("boa noite; vision: ok", "boa noite vision ok"),
])
def test_frase_limpa(entrada, esperado):
    assert frase_limpa(entrada) == esperado


def test_entrada_que_nao_e_texto_passa_por_str():
    assert normalizar(None) == "none" and normalizar(30) == "30"


def test_pc_ainda_exporta_normalizar_para_quem_importava_de_la():
    from vision.tools import pc

    assert pc.normalizar is normalizar
