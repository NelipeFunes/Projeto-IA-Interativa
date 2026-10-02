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
        if not self.fechou_drenando:  # como o FluxoSaida: fechar de novo não faz nada
            self.fechou_drenando.append(drenar)
            self.fechou_em = time.monotonic()


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


async def test_corte_enquanto_o_modelo_ainda_escreve_para_o_som_na_hora():
    """Revisão do PR 22: cortado por voz entre uma frase e a próxima (o modelo ainda pensando), o fone fecha já,
    sem esperar a resposta acabar."""
    voz, saida = VozLenta(atraso_s=0), SaidaLenta(duracao_s=0)
    laco = _laco(voz, saida)
    fila: asyncio.Queue[str | None] = asyncio.Queue()
    fila.put_nowait("Um.")
    falador = asyncio.create_task(laco._falador(fila, []))
    await asyncio.sleep(0.1)
    laco.interrompido_por = "fala"
    await asyncio.sleep(0.15)
    assert saida.fechou_drenando == [False]  # já fechou, cortando
    fila.put_nowait("Dois.")
    fila.put_nowait(None)
    await falador
    assert len(saida.tocadas) == 1


async def test_fone_que_nao_abre_sem_nada_para_tocar_nao_quebra():
    class SaidaSemFone(SaidaLenta):
        def abrir(self, _taxa):
            raise OSError("dispositivo ocupado")

    laco = _laco(VozLenta(atraso_s=0), SaidaSemFone())
    await _falar(laco, ["  "])  # nada a falar: o erro de abrir não importa
    with pytest.raises(OSError, match="ocupado"):
        await _falar(laco, ["Um."])  # algo a falar: o erro sobe, como antes no `tocar`


class _StreamFalso:
    def __init__(self, falha_no_start=False, **_kw):
        self.escritos: list[int] = []
        self.eventos: list[str] = []
        self.falha_no_start = falha_no_start

    def start(self):
        if self.falha_no_start:
            raise OSError("ocupado")
        self.eventos.append("start")

    def write(self, dados):
        self.escritos.append(len(dados))

    def stop(self):
        self.eventos.append("stop")

    def abort(self):
        self.eventos.append("abort")

    def close(self):
        self.eventos.append("close")


def _fluxo(monkeypatch, **kw):
    import sys
    import types

    from vision.voice.audio import FluxoSaida

    criados: list[_StreamFalso] = []

    def criar(**args):
        criados.append(_StreamFalso(**kw))
        return criados[-1]

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=criar))
    evento = threading.Event()
    return FluxoSaida(None, 1000, evento), evento, criados


def test_fluxo_escreve_em_fatias_e_drena_no_fim(monkeypatch):
    fluxo, _corte, (stream,) = _fluxo(monkeypatch)
    assert fluxo.escrever(np.zeros(120, dtype=np.float32), 1000)
    assert stream.escritos == [50, 50, 20]  # fatias de 50 ms
    assert fluxo.escrever(np.zeros(2000, dtype=np.float32), 2000)  # outra taxa: reamostrado para 1 kHz
    assert sum(stream.escritos) == 120 + 1000
    fluxo.fechar()
    fluxo.fechar(False)  # de novo: nada
    assert stream.eventos == ["start", "stop", "close"]


def test_fluxo_cortado_para_de_escrever_e_aborta(monkeypatch):
    fluxo, corte, (stream,) = _fluxo(monkeypatch)
    corte.set()
    assert not fluxo.escrever(np.zeros(120, dtype=np.float32), 1000)
    assert stream.escritos == []
    fluxo.fechar()  # pediu para drenar, mas foi cortado: corta
    assert stream.eventos == ["start", "abort", "close"]


def test_fluxo_que_nao_inicia_fecha_o_stream(monkeypatch):
    import sys
    import types

    from vision.voice.audio import FluxoSaida

    criados: list[_StreamFalso] = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        OutputStream=lambda **_a: criados.append(_StreamFalso(falha_no_start=True)) or criados[-1]))
    with pytest.raises(OSError):
        FluxoSaida(None, 1000, threading.Event())
    assert criados[0].eventos == ["close"]


def test_acordar_sem_vram_desfaz_e_continua_na_reserva():
    class ModeloSemVRAM:
        def __init__(self):
            self.onde = "cpu"

        def cuda(self):
            self.onde = "metade"
            raise RuntimeError("CUDA out of memory")

        def cpu(self):
            self.onde = "cpu"

    voz = _xtts_sem_modelo(_Reserva())
    voz.modelo = ModeloSemVRAM()
    with pytest.raises(RuntimeError, match="out of memory"):
        voz.acordar()
    assert voz.modelo.onde == "cpu" and voz.descansando  # nada pela metade na placa; fala a reserva
    assert voz._trava.acquire(blocking=False)


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


def test_dividir_respeita_o_limite_do_xtts_e_corta_em_fim_de_frase():
    from vision.voice.tts import LIMITE_XTTS, dividir

    assert dividir("Oi.") == ["Oi."]
    assert dividir("   ") == []
    frase = "Encontrei opções no Imovelweb, na MGF Imóveis e no Pedrão, todas com vaga de garagem. "
    texto = frase * 6
    partes = dividir(texto)
    assert all(len(p) <= LIMITE_XTTS for p in partes)
    assert " ".join(partes) == " ".join(texto.split())  # nada se perde
    assert all(p.endswith(".") for p in partes)  # cortou no fim de frase
    sem_ponto = ", ".join(["casa com garagem e quintal"] * 12)
    assert all(len(p) <= LIMITE_XTTS for p in dividir(sem_ponto))
    assert all(len(p) <= LIMITE_XTTS for p in dividir("x" * 500))  # uma palavra gigante


class _ModeloXTTSFalso:
    """Imita o Xtts: recusa texto longo com divisão ligada (como sem o spaCy) e anota o que recebeu."""

    def __init__(self):
        self.recebidos: list[str] = []

    def _checar(self, texto, enable_text_splitting):
        if enable_text_splitting:
            raise ImportError("enable_text_splitting=True requires Spacy")
        assert len(texto) <= 203
        self.recebidos.append(texto)

    def inference(self, texto, idioma, latente, timbre, speed=1.0, enable_text_splitting=False):
        self._checar(texto, enable_text_splitting)
        return {"wav": np.ones(10, dtype=np.float32)}

    def inference_stream(self, texto, idioma, latente, timbre, stream_chunk_size=12, speed=1.0,
                         enable_text_splitting=False):
        self._checar(texto, enable_text_splitting)
        import torch

        yield torch.ones(10)


def test_xtts_com_resposta_longa_nao_precisa_do_spacy():
    """01/10: uma resposta de busca na web com mais de 200 caracteres derrubava a voz."""
    voz = _xtts_sem_modelo(_Reserva())
    voz.descansando = False
    voz.modelo, voz.idioma, voz.latente, voz.timbre = _ModeloXTTSFalso(), "pt", None, None
    voz.velocidade, voz.pedaco_tokens = 1.0, 12
    longa = "Encontrei casas no Imovelweb, na MGF Imóveis e no Pedrão, todas com garagem. " * 4
    assert voz.sintetizar(longa, normalizar=False).size == 10 * len(voz.modelo.recebidos) > 10
    voz.modelo.recebidos.clear()
    assert len(list(voz.pedacos(longa, normalizar=False))) == len(voz.modelo.recebidos) > 1


class XTTSQueQuebra(XTTSFalso):
    def pedacos(self, texto: str, normalizar: bool = True):
        raise ImportError("falha simulada do XTTS")
        yield  # noqa: B901 - é um gerador


async def test_xtts_que_falha_numa_frase_fala_com_o_piper():
    voz, saida = XTTSQueQuebra(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida, piper_ate_caracteres=0), ["Uma frase qualquer.", "Outra."])
    assert [t[0] for t in saida.tocadas] == [-1, -2]  # a reserva falou as duas, e nada caiu


class XTTSQueQuebraNoMeio(XTTSFalso):
    def pedacos(self, texto: str, normalizar: bool = True):
        self.sintetizadas.append((texto, time.monotonic()))
        yield np.full(20, 10.0, dtype=np.float32)
        raise RuntimeError("CUDA error simulado")


async def test_xtts_que_falha_no_meio_nao_repete_o_comeco_e_o_resto_vai_pelo_piper():
    """Revisão do PR 24: a reserva refazia a frase inteira, e o começo tocava duas vezes."""
    voz, saida = XTTSQueQuebraNoMeio(), SaidaLenta(duracao_s=0)
    await _falar(_laco(voz, saida, piper_ate_caracteres=0), ["Primeira frase.", "Segunda.", "Terceira."])
    assert [t[0] for t in saida.tocadas] == [10, -1, -2]  # o pedaço que já saiu, e as próximas pelo Piper
    assert len(voz.sintetizadas) == 1  # depois da falha, não tenta o XTTS de novo nesta resposta


def test_acabamento_tira_silencio_iguala_volume_e_suaviza_as_bordas():
    from vision.voice.tts import LIMITE_PICO, VOLUME_ALVO_RMS, acabamento

    taxa = 1000
    t = np.arange(400) / taxa
    baixo = np.concatenate([np.zeros(300), 0.02 * np.sin(2 * np.pi * 50 * t), np.zeros(300)]).astype(np.float32)
    alto = (0.9 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    a, b = acabamento(baixo, taxa), acabamento(alto, taxa)
    assert a.size < baixo.size  # o silêncio das pontas saiu
    rms = [float(np.sqrt(np.mean(x**2))) for x in (a, b)]
    assert all(abs(r - VOLUME_ALVO_RMS) < 0.02 for r in rms)  # as duas no mesmo volume
    assert np.max(np.abs(b)) <= LIMITE_PICO + 1e-6
    assert abs(a[0]) < 1e-6 and abs(a[-1]) < 1e-6  # começa e termina em zero: sem estalo
    assert acabamento(np.zeros(100, np.float32), taxa).size == 0


def test_piper_poe_a_pausa_depois_de_cada_frase():
    """Revisão do PR 26: o laço sintetiza uma frase por vez, então a pausa tem que vir no fim de cada uma."""
    from pathlib import Path

    from vision.voice.tts import Voz

    modelo = Path(__file__).resolve().parents[1] / "modelos" / "piper" / "pt_BR-faber-medium.onnx"
    if not modelo.exists():
        pytest.skip("sem o modelo do Piper")
    sem = Voz(modelo, deterministico=True, pausa_s=0.0).sintetizar("Bom dia.")
    com = Voz(modelo, deterministico=True, pausa_s=0.3).sintetizar("Bom dia.")
    taxa = 22050
    assert abs((com.size - sem.size) / taxa - 0.3) < 0.02 and not np.any(com[-int(0.25 * taxa):])
    assert Voz(modelo, deterministico=True, pausa_s=-5).pausa.size == 0  # valor ruim não derruba
