"""Voz XTTS (01/10): streaming, Piper nas respostas curtas, 1ª vírgula, reserva no modo jogo. Sem modelos:
vozes falsas."""

from __future__ import annotations

import asyncio
import threading
import time

import numpy as np
import pytest
from fakes.llm_falso import LLMFalso

from vision.brain.agent import Agente
from vision.tools.base import Registro
from vision.voice.loop import fatiar


class VozLenta:
    """Tipo Piper: frase inteira de uma vez. Leva `atraso_s` por frase e anota o que sintetizou e quando."""

    taxa = 1000

    def __init__(self, atraso_s: float = 0.05, quebra_em: str | None = None, marca: float = 1.0):
        self.atraso_s = atraso_s
        self.quebra_em = quebra_em
        self.marca = marca
        self.sintetizadas: list[tuple[str, float]] = []

    def sintetizar(self, texto: str, normalizar: bool = True) -> np.ndarray:
        time.sleep(self.atraso_s)
        if texto == self.quebra_em:
            raise RuntimeError("falha simulada")
        self.sintetizadas.append((texto, time.monotonic()))
        return np.full(50, self.marca * len(self.sintetizadas), dtype=np.float32)


class XTTSFalso(VozLenta):
    """Tipo XTTS: três pedaços por frase, com uma reserva (Piper) marcada com valores negativos."""

    taxa = 1000

    def __init__(self, atraso_s: float = 0.0):
        super().__init__(atraso_s)
        self.reserva = VozLenta(0, marca=-1.0)
        self.fechados = 0
        self.chamadas: list[str] = []

    def pedacos(self, texto: str, normalizar: bool = True):
        self.sintetizadas.append((texto, time.monotonic()))
        try:
            for i in range(3):
                time.sleep(self.atraso_s)
                yield np.full(20, 10.0 + i, dtype=np.float32)
        finally:
            self.fechados += 1

    def descansar(self):
        self.chamadas.append("descansar")

    def acordar(self):
        self.chamadas.append("acordar")


class SaidaLenta:
    def __init__(self, duracao_s: float = 0.1):
        self.duracao_s = duracao_s
        self.interromper = threading.Event()
        self.tocadas: list[tuple[float, float, float]] = []
        self.aberturas = 0
        self.fechou_drenando: list[bool] = []

    def abrir(self, _taxa: int):
        self.aberturas += 1
        return self

    def escrever(self, audio: np.ndarray, _taxa: int) -> bool:
        inicio = time.monotonic()
        time.sleep(self.duracao_s)
        self.tocadas.append((float(audio[0]), inicio, time.monotonic()))
        return not self.interromper.is_set()

    def fechar(self, drenar: bool = True) -> None:
        self.fechou_drenando.append(drenar)


def _laco(voz, saida, **opcoes):
    from vision.voice.loop import LoopVoz

    class Entrada:
        def descartar(self):
            pass

    return LoopVoz(Agente(LLMFalso([]), Registro(), None), voz, None, Entrada(), saida, None,
                   escrever=lambda _t: None, **opcoes)


async def _falar(laco, frases):
    fila: asyncio.Queue[str | None] = asyncio.Queue()
    for f in frases:
        fila.put_nowait(f)
    fila.put_nowait(None)
    await laco._falador(fila, [])


async def test_sintetiza_a_proxima_frase_enquanto_a_atual_toca():
    voz, saida = VozLenta(atraso_s=0.05), SaidaLenta(duracao_s=0.15)
    await _falar(_laco(voz, saida), ["Um.", "Dois.", "Três."])
    assert [t[0] for t in saida.tocadas] == [1, 2, 3]  # na ordem
    assert voz.sintetizadas[1][1] < saida.tocadas[0][2]  # a 2ª ficou pronta antes de a 1ª acabar: sem pausa
    assert saida.aberturas == 1 and saida.fechou_drenando == [True]  # um fluxo só, e o fim toca inteiro


async def test_frase_vazia_nao_e_sintetizada():
    voz, saida = VozLenta(atraso_s=0), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida), ["Um.", "  ", "Dois."])
    assert [t for t, _ in voz.sintetizadas] == ["Um.", "Dois."]


async def test_erro_de_sintese_chega_ao_falador():
    voz, saida = VozLenta(atraso_s=0, quebra_em="Dois."), SaidaLenta(duracao_s=0)
    with pytest.raises(RuntimeError, match="falha simulada"):
        await _falar(_laco(voz, saida), ["Um.", "Dois.", "Três."])
    assert [t[0] for t in saida.tocadas] == [1]


async def test_cortado_por_voz_nao_sintetiza_o_resto():
    voz, saida = VozLenta(atraso_s=0.02), SaidaLenta(duracao_s=0.1)
    laco = _laco(voz, saida)

    async def corta():
        await asyncio.sleep(0.05)
        laco.interrompido_por = "fala"

    await asyncio.gather(_falar(laco, ["Um.", "Dois.", "Três.", "Quatro.", "Cinco."]), corta())
    assert len(saida.tocadas) == 1
    assert len(voz.sintetizadas) < 5
    assert saida.fechou_drenando == [False]  # cortado: não espera o buffer tocar


async def test_atalho_no_meio_do_streaming_para_e_solta_a_placa():
    voz, saida = XTTSFalso(atraso_s=0.03), SaidaLenta(duracao_s=0.05)
    laco = _laco(voz, saida, piper_ate_caracteres=0)

    async def atalho():
        await asyncio.sleep(0.06)
        saida.interromper.set()

    await asyncio.gather(_falar(laco, ["Uma frase comprida que vai em pedaços.", "Outra frase."]), atalho())
    assert len(saida.tocadas) < 6
    await asyncio.sleep(0.15)  # a thread vê o corte no próximo pedaço e fecha o gerador
    assert voz.fechados == len(voz.sintetizadas)


async def test_xtts_toca_em_pedacos_num_fluxo_so():
    voz, saida = XTTSFalso(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida, piper_ate_caracteres=0), ["Amanhã às dez você tem barbeiro.", "E mais nada."])
    assert [t[0] for t in saida.tocadas] == [10, 11, 12, 10, 11, 12]
    assert saida.aberturas == 1 and voz.fechados == 2


async def test_resposta_curta_inteira_sai_pelo_piper():
    voz, saida = XTTSFalso(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida), ["Acendi a luz do quarto."])
    assert [t[0] for t in saida.tocadas] == [-1]  # a reserva
    assert voz.sintetizadas == []


async def test_frase_curta_seguida_de_outra_fica_no_xtts():
    """Não troca de voz no meio da resposta: só a resposta inteira curta vai para o Piper."""
    voz, saida = XTTSFalso(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida), ["Pronto, Felipe.", "Amanhã você tem barbeiro às dez."])
    assert all(t[0] >= 10 for t in saida.tocadas)
    assert voz.reserva.sintetizadas == []


async def test_frase_longa_sozinha_fica_no_xtts():
    voz, saida = XTTSFalso(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida), ["Amanhã às dez você tem barbeiro e às três a reunião do projeto."])
    assert voz.reserva.sintetizadas == [] and len(voz.sintetizadas) == 1


def test_primeira_virgula_ja_vira_frase_enquanto_o_resto_chega():
    assert fatiar("Amanhã às dez você tem barbeiro, e às", primeira=True) == (
        ["Amanhã às dez você tem barbeiro,"], "e às")
    # Já falou algo: só ponto final fecha frase.
    assert fatiar("Depois você tem a reunião, e às", primeira=False) == ([], "Depois você tem a reunião, e às")


def test_virgula_cedo_demais_ou_frase_ja_inteira_nao_corta():
    assert fatiar("Pronto, Felipe", primeira=True) == ([], "Pronto, Felipe")  # pedaço curto soa picotado
    # Resposta de atalho chega inteira: não corta (e assim ela vai inteira para o Piper).
    assert fatiar("Tocando Bohemian Rhapsody, de Queen.", primeira=True) == (
        [], "Tocando Bohemian Rhapsody, de Queen.")
    assert fatiar("Custa 1,5 mil reais. Quer", primeira=True) == (["Custa 1,5 mil reais."], "Quer")


async def test_modo_jogo_tira_a_voz_da_placa_e_devolve():
    voz = XTTSFalso()
    laco = _laco(voz, SaidaLenta())
    await laco._voz_na_placa(False)
    await laco._voz_na_placa(True)
    assert voz.chamadas == ["descansar", "acordar"]
    laco.voz = VozLenta()  # o Piper não tem descansar/acordar: nada acontece
    await laco._voz_na_placa(False)


def _xtts_sem_modelo(reserva):
    from vision.voice.tts import VozXTTS

    voz = VozXTTS.__new__(VozXTTS)  # sem carregar o modelo
    voz.reserva, voz.descansando, voz._trava = reserva, True, threading.Lock()
    return voz


class _Reserva:
    taxa = 12000

    def sintetizar(self, texto, normalizar=True):
        return np.ones(12000, dtype=np.float32)  # 1 s


def test_xtts_descansando_fala_com_a_reserva_na_mesma_taxa():
    from vision.voice.tts import VozXTTS

    voz = _xtts_sem_modelo(_Reserva())
    assert abs(voz.sintetizar("Oi.").size - VozXTTS.taxa) <= 2  # reamostrado para 24 kHz
    pedacos = list(voz.pedacos("Oi."))
    assert len(pedacos) == 1 and abs(pedacos[0].size - VozXTTS.taxa) <= 2
    assert voz._trava.acquire(blocking=False)  # o gerador soltou a trava


def _get_com(original, valores):
    def get(chave, padrao=None):
        return valores[chave] if chave in valores else original(chave, padrao)

    return get


class _Piper:
    taxa = 22050


def test_sem_xtts_cai_no_piper(monkeypatch, cfg):
    from vision.voice import tts

    def quebra(*_a, **_k):
        raise RuntimeError("sem placa de vídeo com CUDA para o XTTS")

    monkeypatch.setattr(tts, "carregar_piper", lambda _cfg, _nome=None: _Piper())
    monkeypatch.setattr(tts, "VozXTTS", quebra)
    monkeypatch.setattr(cfg, "get", _get_com(cfg.get, {"voz.motor": "xtts"}))
    assert isinstance(tts.carregar_voz(cfg), _Piper)


def test_motor_piper_nem_tenta_o_xtts(monkeypatch, cfg):
    from vision.voice import tts

    def nao_chame(*_a, **_k):
        raise AssertionError("não devia carregar o XTTS")

    monkeypatch.setattr(tts, "carregar_piper", lambda _cfg, _nome=None: _Piper())
    monkeypatch.setattr(tts, "VozXTTS", nao_chame)
    monkeypatch.setattr(cfg, "get", _get_com(cfg.get, {"voz.motor": "piper"}))
    assert isinstance(tts.carregar_voz(cfg), _Piper)
