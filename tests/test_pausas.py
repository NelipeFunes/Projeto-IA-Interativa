"""Ritmo da fala do XTTS: o texto que ele recebe e o compressor de pausas (vision/voice/pausas.py)."""

import numpy as np
import pytest

from vision.voice.pausas import FIM_S, PAUSA_MAX_S, QUADRO_S, CompressorDePausas, preparar_texto

TAXA = 24000


def _tom(segundos: float, amplitude: float = 0.2) -> np.ndarray:
    t = np.arange(int(TAXA * segundos)) / TAXA
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _mudo(segundos: float) -> np.ndarray:
    return np.zeros(int(TAXA * segundos), dtype=np.float32)


def _comprimir(sinal: np.ndarray, pedaco: int, **kw) -> np.ndarray:
    c = CompressorDePausas(TAXA, **kw)
    partes = [c.processar(sinal[i:i + pedaco]) for i in range(0, len(sinal), pedaco)]
    partes.append(c.terminar())
    return np.concatenate(partes)


# ---------- texto ----------

def test_tira_so_o_ponto_final():
    assert preparar_texto("Pronto, acendi as luzes do quarto.") == "Pronto, acendi as luzes do quarto"
    assert preparar_texto("Pronto.  ") == "Pronto"
    assert preparar_texto("Confirma?") == "Confirma?"
    assert preparar_texto("Feito!") == "Feito!"
    assert preparar_texto("Espera...") == "Espera..."  # reticências são entonação, não ponto final
    assert preparar_texto("Bom dia. Tudo bem.") == "Bom dia. Tudo bem"  # só o do fim
    assert preparar_texto("") == ""
    assert preparar_texto("  muitos   espaços  ") == "muitos espaços"


def test_virgulas_viram_nada_mas_numeros_ficam():
    assert preparar_texto("Vou criar, amanhã: às 9;", tirar_virgulas=True) == "Vou criar amanhã às 9"
    assert preparar_texto("São 7:30, com 1,5 litro.", tirar_virgulas=True) == "São 7:30 com 1,5 litro"
    assert preparar_texto("Pronto, acendi.", tirar_virgulas=False) == "Pronto, acendi"  # sem o pedido, mantém


# ---------- compressor ----------

def test_corta_o_silencio_do_comeco_e_limita_cada_pausa():
    sinal = np.concatenate([_mudo(0.3), _tom(0.5), _mudo(0.6), _tom(0.4), _mudo(0.5)])
    saida = _comprimir(sinal, pedaco=4800)
    esperado = 0.5 + PAUSA_MAX_S + 0.4 + FIM_S  # começo cortado, pausa do meio limitada, silêncio do fim = FIM_S
    assert abs(len(saida) / TAXA - esperado) <= 3 * QUADRO_S


def test_pausa_curta_fica_como_esta():
    sinal = np.concatenate([_tom(0.4), _mudo(0.1), _tom(0.4)])
    saida = _comprimir(sinal, pedaco=4800)
    assert abs(len(saida) - len(sinal) - int(TAXA * FIM_S)) <= 2 * int(TAXA * QUADRO_S)  # só ganha o silêncio do fim


@pytest.mark.parametrize("pedaco", [1, 97, 240, 1111, 4800, 24000])
def test_o_resultado_nao_depende_do_tamanho_dos_pedacos(pedaco):
    sinal = np.concatenate([_mudo(0.2), _tom(0.3), _mudo(0.7), _tom(0.3), _mudo(0.4)])
    referencia = _comprimir(sinal, pedaco=len(sinal))
    assert np.allclose(_comprimir(sinal, pedaco=pedaco), referencia)


def test_so_silencio_nao_toca_nada():
    assert _comprimir(_mudo(1.0), pedaco=4800).size == 0
    assert CompressorDePausas(TAXA).processar(np.zeros(0, dtype=np.float32)).size == 0


def test_entrada_suave_depois_da_pausa_cortada():
    sinal = np.concatenate([_tom(0.3), _mudo(0.8), _tom(0.3)])
    saida = _comprimir(sinal, pedaco=4800)
    quadro = int(TAXA * QUADRO_S)
    retomada = int(TAXA * (0.3 + PAUSA_MAX_S))  # onde a 2ª fala começa depois do corte
    assert abs(saida[retomada]) < 0.05  # começa quase em zero (rampa), em vez de um degrau de 0,2
    assert np.abs(saida[retomada + quadro: retomada + 3 * quadro]).max() > 0.1  # e logo segue na fala


def test_pedaco_menor_que_um_quadro_nao_se_perde():
    c = CompressorDePausas(TAXA)
    assert c.processar(np.full(10, 0.3, dtype=np.float32)).size == 0  # guardado até fechar um quadro
    assert c.terminar().size == 10 + int(TAXA * FIM_S)  # e entregue no fim, porque é fala (mais o silêncio do fim)
    c = CompressorDePausas(TAXA)
    c.processar(np.zeros(10, dtype=np.float32))
    assert c.terminar().size == 0  # silêncio solto não toca


def test_a_primeira_fala_tambem_entra_suave():
    saida = _comprimir(np.concatenate([_mudo(0.2), _tom(0.4)]), pedaco=4800)
    assert abs(saida[0]) < 0.01 and np.abs(saida[int(TAXA * 0.02):]).max() > 0.15  # sem estalo no começo


def test_o_silencio_do_fim_completa_fim_s_sem_passar_dele():
    for mudo_no_fim in (0.0, 0.05, 0.1, 0.5):
        sinal = np.concatenate([_tom(0.3), _mudo(mudo_no_fim)]) if mudo_no_fim else _tom(0.3)
        saida = _comprimir(sinal, pedaco=4800)
        assert abs(len(saida) / TAXA - (0.3 + FIM_S)) <= 3 * QUADRO_S, mudo_no_fim


def test_pausa_max_configuravel():
    sinal = np.concatenate([_tom(0.3), _mudo(1.0), _tom(0.3)])
    curta = _comprimir(sinal, pedaco=4800, pausa_max_s=0.05)
    longa = _comprimir(sinal, pedaco=4800, pausa_max_s=0.5)
    assert len(longa) > len(curta) + int(TAXA * 0.3)
    assert abs(len(longa) / TAXA - (0.3 + 0.5 + 0.3 + FIM_S)) <= 3 * QUADRO_S
