"""Serviço Wyoming com um cliente Wyoming de verdade (a biblioteca oficial), STT e voz falsos."""

from __future__ import annotations

import asyncio
import json
from functools import partial

import numpy as np
import pytest

from vision.voice.wyoming import MAX_AUDIO_S, criar_atendente, endereco_seguro, para_int16


class TranscritorFalso:
    def __init__(self):
        self.recebido = None

    def transcrever(self, pcm, taxa):
        self.recebido = (pcm, taxa)
        return "acende a luz da sala"


class VozFalsa:
    taxa = 22050

    def sintetizar(self, texto, normalizar=True):
        self.texto = texto
        return np.full(5000, 0.5, dtype=np.float32)


@pytest.mark.parametrize("uri", ["tcp://127.0.0.1:10300", "tcp://localhost:10300", "tcp://[::1]:10300"])
def test_local_e_aceito(uri):
    assert endereco_seguro(uri, permitir_rede=False) == uri


@pytest.mark.parametrize("uri", ["tcp://0.0.0.0:10300", "tcp://192.168.0.10:10300"])
def test_rede_so_com_permissao(uri):
    with pytest.raises(ValueError, match="permitir_rede"):
        endereco_seguro(uri, permitir_rede=False)
    assert endereco_seguro(uri, permitir_rede=True) == uri


def test_endereco_invalido():
    with pytest.raises(ValueError, match="inválido"):
        endereco_seguro("http://127.0.0.1:10300", permitir_rede=False)


async def test_descreve_transcreve_e_fala_pelo_protocolo():
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioChunk, AudioStart, AudioStop
    from wyoming.client import AsyncTcpClient
    from wyoming.info import Describe, Info
    from wyoming.server import AsyncServer
    from wyoming.tts import Synthesize

    stt, voz = TranscritorFalso(), VozFalsa()
    servidor = AsyncServer.from_uri("tcp://127.0.0.1:0")
    tarefa = asyncio.create_task(servidor.run(partial(criar_atendente(stt, voz, "parakeet-base-int8",
                                                                      "pt_BR-faber-medium"))))
    for _ in range(100):
        if getattr(servidor, "_server", None) is not None:
            break
        await asyncio.sleep(0.01)
    porta = servidor._server.sockets[0].getsockname()[1]
    try:
        async with AsyncTcpClient("127.0.0.1", porta) as c:
            await c.write_event(Describe().event())
            info = Info.from_event(await c.read_event())
            assert info.asr[0].name == "vision-parakeet" and info.tts[0].voices[0].languages == ["pt-BR"]

            # 1 s de áudio a 48 kHz estéreo: o serviço converte para 16 kHz mono antes de transcrever.
            await c.write_event(Transcribe(language="pt").event())
            await c.write_event(AudioStart(rate=48000, width=2, channels=2).event())
            bruto = np.zeros(48000 * 2, dtype="<i2").tobytes()
            await c.write_event(AudioChunk(rate=48000, width=2, channels=2, audio=bruto).event())
            await c.write_event(AudioStop().event())
            assert Transcript.from_event(await c.read_event()).text == "acende a luz da sala"
            pcm, taxa = stt.recebido
            assert taxa == 16000 and abs(pcm.size - 16000) < 50

            await c.write_event(Synthesize(text="Pronto,   Felipe.").event())
            inicio = AudioStart.from_event(await c.read_event())
            assert inicio.rate == 22050 and voz.texto == "Pronto, Felipe."
            total = 0
            while not AudioStop.is_type((ev := await c.read_event()).type):
                total += len(AudioChunk.from_event(ev).audio)
            assert total == 5000 * 2
    finally:
        tarefa.cancel()


def test_int16_e_limite():
    assert para_int16(np.array([2.0, -2.0, 0.0], np.float32)) == np.array([32767, -32767, 0], "<i2").tobytes()
    assert MAX_AUDIO_S == 30


async def test_evento_grande_demais_ou_quebrado_encerra_a_conexao():
    from vision.voice.wyoming import MAX_PAYLOAD, ler_evento

    def leitor(bruto: bytes) -> asyncio.StreamReader:
        r = asyncio.StreamReader()
        r.feed_data(bruto)
        r.feed_eof()
        return r

    gigante = json.dumps({"type": "audio-chunk", "payload_length": 4_000_000_000}).encode() + b"\n"
    assert await ler_evento(leitor(gigante)) is None
    assert await ler_evento(leitor(b"isso nao e json\n")) is None
    ok = json.dumps({"type": "audio-chunk", "data": {"rate": 16000}, "payload_length": 4}).encode() + b"\n" + b"abcd"
    ev = await ler_evento(leitor(ok))
    assert ev.type == "audio-chunk" and ev.payload == b"abcd" and ev.data["rate"] == 16000
    curto = json.dumps({"type": "audio-chunk", "payload_length": MAX_PAYLOAD}).encode() + b"\n" + b"ab"
    assert await ler_evento(leitor(curto)) is None  # prometeu mais do que mandou
