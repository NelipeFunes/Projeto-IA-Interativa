"""Diagnóstico do PC: relatório, alertas e o vigia que não repete o mesmo alerta."""

from __future__ import annotations

import pytest

from vision import sistema
from vision.sistema import Estado, Placa, Vigia, alertas, relatorio
from vision.tools.sistema import pede_status


def _estado(gpu_c=50, mem=60.0, disco=70.0, gpus=True):
    placas = [Placa("RTX", 30, gpu_c, 5530, 8192)] if gpus else []
    return Estado(10.0, mem, 19.6, 32.0, 120.0, disco, 3 * 86400 + 2 * 3600, placas)


def test_relatorio_normal():
    r = relatorio(_estado())
    assert r.startswith("Processador em 10%; memória em 60% (19,6 de 32 GB); placa de vídeo em 30%, 50°C, 5,4 de 8 GB")
    assert "120 GB livres no disco" in r and "Ligado há 3 dias e 2 horas." in r and r.endswith("Tudo dentro do normal.")
    assert "placa" not in relatorio(_estado(gpus=False))


def test_alertas():
    a = alertas(_estado(gpu_c=88, mem=97.0, disco=96.0))
    assert len(a) == 3 and "88°C" in a[0]
    assert "Atenção: a placa de vídeo está a 88°C." in relatorio(_estado(gpu_c=88))


def test_vigia_avisa_uma_vez_por_hora():
    agora = [0.0]
    v = Vigia(intervalo_s=3600, relogio=lambda: agora[0])
    assert v.novos(_estado(gpu_c=90)) == ["a placa de vídeo está a 90°C."]
    agora[0] = 600
    assert v.novos(_estado(gpu_c=91)) == []  # o mesmo alerta, cedo demais
    assert v.novos(_estado(gpu_c=91, mem=99.0)) == ["a memória está em 99%."]  # outro alerta sai na hora
    agora[0] = 3700
    assert v.novos(_estado(gpu_c=91)) == ["a placa de vídeo está a 91°C."]


def test_leitura_real_nao_quebra():
    e = sistema.ler_estado()
    assert 0 <= e.cpu_pct <= 100 and e.memoria_total_gb > 0 and e.disco_livre_gb >= 0


@pytest.mark.parametrize("frase,sim", [("status do sistema", True), ("Vision, como está o PC?", True),
                                       ("diagnóstico do computador", True), ("status do pedido", False),
                                       ("como está o tempo?", False)])
def test_frases(frase, sim):
    assert pede_status(frase) is sim
