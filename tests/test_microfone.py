"""O microfone se vigia (02/10: o HyperX ficou mudo e o "Hey Vision" nunca mais era ouvido até reiniciar)."""

from __future__ import annotations

import numpy as np
import pytest

from vision.voice import audio
from vision.voice.audio import (
    CONFERIR_PREFERIDO_S,
    SEM_AUDIO_S,
    TROCAR_APOS_SILENCIO_S,
    VigiaMicrofone,
    silencio_digital,
)

MUDO = np.zeros(1280, np.int16)  # headset sem fio mudo/desligado: zero puro
SALA = (np.random.default_rng(0).normal(0, 20, 1280)).astype(np.int16)  # sala quieta: ~-64 dBFS, não é zero


def test_silencio_digital_e_so_o_zero_puro():
    assert silencio_digital(MUDO) and silencio_digital(np.ones(1280, np.int16))  # ±1 de ruído ainda é mudo
    assert not silencio_digital(SALA)
    assert silencio_digital(np.zeros(0, np.int16))


def test_vigia_troca_depois_de_1_min_mudo_e_nao_antes():
    v = VigiaMicrofone(0.0, varios=True, no_preferido=True)
    t = 0.0
    while t < TROCAR_APOS_SILENCIO_S - 1:
        assert v.bloco(MUDO, t) is None
        t += 0.08
    v.bloco(SALA, t)  # um pouco de som zera a contagem: é gente quieta, não microfone mudo
    assert v.bloco(MUDO, t + 0.08) is None  # a contagem recomeça aqui
    assert v.bloco(MUDO, t + 0.08 + TROCAR_APOS_SILENCIO_S - 1) is None
    assert v.bloco(MUDO, t + 0.08 + TROCAR_APOS_SILENCIO_S) == "trocar"
    assert v.bloco(MUDO, t + 0.16 + TROCAR_APOS_SILENCIO_S) is None  # não tenta a cada bloco


def test_vigia_no_reserva_confere_o_preferido_de_tempos_em_tempos():
    v = VigiaMicrofone(0.0, varios=True, no_preferido=False)
    assert v.bloco(SALA, CONFERIR_PREFERIDO_S - 1) is None
    assert v.bloco(SALA, CONFERIR_PREFERIDO_S) == "conferir"
    assert v.bloco(SALA, CONFERIR_PREFERIDO_S + 1) is None
    no_preferido = VigiaMicrofone(0.0, varios=True, no_preferido=True)
    assert no_preferido.bloco(SALA, 10 * CONFERIR_PREFERIDO_S) is None


def test_vigia_com_um_microfone_so_nao_troca():
    v = VigiaMicrofone(0.0, varios=False, no_preferido=True)
    assert v.bloco(MUDO, 0.0) is None and v.bloco(MUDO, 10 * TROCAR_APOS_SILENCIO_S) is None


# ------------------------------------------------------------------ o Microfone com aparelhos falsos


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def aparelhos(monkeypatch):
    """Dois microfones: 0 = HyperX (preferido), 1 = webcam. `som[disp]` diz se ele tem som agora."""
    estado = {"som": {0: False, 1: True}, "abertos": []}
    nomes = {0: "Microfone (HyperX Cloud)", 1: "Microfone (Webcam C920)"}

    def achar(lista, entrada):
        for n in lista:
            for d, nome in nomes.items():
                if n.lower() in nome.lower():
                    return d, nome
        return 1, nomes[1]

    monkeypatch.setattr(audio, "achar_dispositivo", achar)
    monkeypatch.setattr(audio, "_amostra", lambda d, segundos=0.4: SALA if estado["som"][d] else MUDO)
    def abrir(self):  # como o de verdade: o fluxo novo tem SEM_AUDIO_S para começar a entregar
        estado["abertos"].append(self.dispositivo)
        self._recebido_em = self._relogio()

    monkeypatch.setattr(audio.Microfone, "_abrir", abrir)
    monkeypatch.setattr(audio.Microfone, "_fechar", lambda self: None)
    return estado


def _mic(cfg, relogio):
    cfg.bruto.setdefault("voz", {})["microfone"] = ["HyperX", "C920"]
    m = audio.Microfone(cfg, relogio=relogio)
    trocas = []
    m.ao_trocar = trocas.append
    m.__enter__()
    return m, trocas


async def _ler(m, relogio, bloco, segundos, estado=None):
    """Põe blocos na fila como o driver faria e lê pelo `blocos()`, andando o relógio."""
    gerador = m.blocos()
    fim = relogio.t + segundos
    while relogio.t < fim:
        m._fila.put_nowait(bloco)
        m._recebido_em = relogio.t
        await gerador.__anext__()
        relogio.t += 0.08
    await gerador.aclose()


async def test_headset_mudo_passa_para_a_webcam_e_volta_quando_ele_volta(cfg, aparelhos):
    relogio = Relogio()
    aparelhos["som"][0] = True  # ao iniciar, o HyperX tinha som
    m, trocas = _mic(cfg, relogio)
    assert m.dispositivo == 0
    aparelhos["som"][0] = False  # ficou mudo (botão de mudo ou headset desligado)
    await _ler(m, relogio, MUDO, TROCAR_APOS_SILENCIO_S + 1)
    assert m.dispositivo == 1 and "mudo ou desligado" in trocas[-1] and "C920" in trocas[-1]
    await _ler(m, relogio, SALA, CONFERIR_PREFERIDO_S + 1)
    assert m.dispositivo == 1  # o HyperX ainda está mudo: fica na webcam
    aparelhos["som"][0] = True
    await _ler(m, relogio, SALA, CONFERIR_PREFERIDO_S + 1)
    assert m.dispositivo == 0 and "voltou" in trocas[-1]
    assert aparelhos["abertos"] == [0, 1, 0]


async def test_com_todos_mudos_nao_fica_trocando(cfg, aparelhos):
    relogio = Relogio()
    aparelhos["som"] = {0: True, 1: False}
    m, trocas = _mic(cfg, relogio)
    aparelhos["som"][0] = False
    await _ler(m, relogio, MUDO, 3 * TROCAR_APOS_SILENCIO_S)
    # escolher_microfone sem ninguém com som cai no padrão (a webcam, aqui); dali, sem outro com som, fica.
    assert len(aparelhos["abertos"]) <= 2 and len(trocas) <= 1


async def test_fluxo_que_parou_de_entregar_e_reaberto(cfg, aparelhos):
    relogio = Relogio()
    aparelhos["som"][0] = True
    m, trocas = _mic(cfg, relogio)
    gerador = m.blocos()
    relogio.t = SEM_AUDIO_S + 0.1  # o driver não entregou nada nesse tempo (aparelho sumiu)
    import asyncio

    proximo = asyncio.ensure_future(gerador.__anext__())
    await asyncio.sleep(0.05)
    assert "parou de mandar áudio" in trocas[-1] and aparelhos["abertos"] == [0, 0]
    await asyncio.sleep(0.05)
    assert aparelhos["abertos"] == [0, 0]  # reaberto, ele espera o fluxo novo (não reabre de novo na hora)
    m._fila.put_nowait(SALA)  # chega áudio de novo
    await proximo
    await gerador.aclose()


async def test_depois_de_ele_falar_muito_tempo_nao_reabre_a_toa(cfg, aparelhos):
    """Enquanto o Vision fala ninguém lê os blocos, e depois eles são descartados: não é fluxo parado."""
    relogio = Relogio()
    aparelhos["som"][0] = True
    m, trocas = _mic(cfg, relogio)
    relogio.t = 30.0
    m._recebido_em = relogio.t  # o driver continuou entregando (o callback marca a hora)
    m.descartar()
    gerador = m.blocos()
    m._fila.put_nowait(SALA)
    await gerador.__anext__()
    await gerador.aclose()
    assert trocas == [] and aparelhos["abertos"] == [0]
