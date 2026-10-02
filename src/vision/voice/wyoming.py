"""Serviço Wyoming: a transcrição (Parakeet) e a voz (Piper) do Vision para o Home Assistant e os satélites de voz
dos cômodos (plano docs/futuro/02, caminho A; item 12 do plano 04).

    vision wyoming            # tcp://127.0.0.1:10300

No Home Assistant: Configurações → Dispositivos e serviços → Adicionar → "Wyoming Protocol", com o endereço e a
porta. O pipeline do Assist passa a usar "vision-parakeet" (fala → texto) e "vision-piper" (texto → fala).

SEGURANÇA: o Wyoming não tem senha nem criptografia. Por isso o serviço escuta só no 127.0.0.1. Para outro
aparelho alcançar, é preciso `wyoming.permitir_rede: true` no config, e então só numa rede confiável (de
preferência a rede interna do Docker do HA, nunca exposta). Aqui não há conversa nem ações: só transcrever e falar.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from functools import partial
from typing import Any
from urllib.parse import urlparse

import numpy as np

log = logging.getLogger(__name__)

TAXA_STT = 16000
MAX_AUDIO_S = 30  # fala mais longa que isso é cortada
MAX_TEXTO = 1000
PEDACO_AMOSTRAS = 2048
# Revisão do PR 33: a leitura da biblioteca confia no tamanho que o cliente declara. Aqui cada evento é lido com teto,
# e a conexão cai se passar dele, se ficar parada, ou se já houver conexões demais.
MAX_DADOS = 64 * 1024  # JSON de um evento
MAX_PAYLOAD = 512 * 1024  # áudio de um pedaço (~16 s a 16 kHz; os satélites mandam pedaços de ~30 ms)
OCIOSO_S = 60
MAX_CONEXOES = 8


async def ler_evento(reader: asyncio.StreamReader):
    """Como `wyoming.event.async_read_event`, mas recusando tamanhos acima do teto. None = encerrar a conexão."""
    from wyoming.event import Event

    try:
        linha = await asyncio.wait_for(reader.readline(), OCIOSO_S)
        if not linha:
            return None
        cabecalho = json.loads(linha)
        tamanho_dados = int(cabecalho.get("data_length") or 0)
        tamanho_payload = int(cabecalho.get("payload_length") or 0)
        if not (0 <= tamanho_dados <= MAX_DADOS and 0 <= tamanho_payload <= MAX_PAYLOAD):
            log.warning("Wyoming: evento grande demais; conexão encerrada")
            return None
        dados = cabecalho.get("data") or {}
        if tamanho_dados:
            dados = {**dados, **json.loads(await asyncio.wait_for(reader.readexactly(tamanho_dados), OCIOSO_S))}
        payload = await asyncio.wait_for(reader.readexactly(tamanho_payload), OCIOSO_S) if tamanho_payload else None
        return Event(type=str(cabecalho["type"]), data=dados if isinstance(dados, dict) else {}, payload=payload)
    except (TimeoutError, ValueError, KeyError, TypeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return None


def endereco_seguro(uri: str, permitir_rede: bool) -> str:
    """Só tcp://127.0.0.1 ou ::1, a não ser que a rede tenha sido liberada de propósito."""
    p = urlparse(uri)
    if p.scheme != "tcp" or not p.hostname or not p.port:
        raise ValueError(f"endereço Wyoming inválido: {uri!r} (use tcp://127.0.0.1:10300)")
    try:
        local = ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        local = p.hostname == "localhost"
    if not local and not permitir_rede:
        raise ValueError("o Wyoming não tem senha nem criptografia: para escutar fora do 127.0.0.1, ponha "
                         "wyoming.permitir_rede: true no config (só numa rede confiável)")
    return uri


def info(nome_stt: str, nome_voz: str):
    from wyoming.info import AsrModel, AsrProgram, Attribution, Info, TtsProgram, TtsVoice

    autoria = Attribution(name="Vision", url="https://github.com/NelipeFunes/Projeto-IA-Interativa")
    return Info(
        asr=[AsrProgram(name="vision-parakeet", attribution=autoria, installed=True, description="Parakeet (Vision)",
                        version="1", models=[AsrModel(name=nome_stt, attribution=autoria, installed=True,
                                                      description=nome_stt, version="1", languages=["pt"])])],
        tts=[TtsProgram(name="vision-piper", attribution=autoria, installed=True, description="Piper (Vision)",
                        version="1", voices=[TtsVoice(name=nome_voz, attribution=autoria, installed=True,
                                                      description=nome_voz, version="1", languages=["pt-BR"])])],
    )


def para_int16(audio: np.ndarray) -> bytes:
    return (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype("<i2").tobytes()


def criar_atendente(transcritor: Any, voz: Any, nome_stt: str, nome_voz: str):
    """A classe que o AsyncServer instancia por conexão (cada uma com o seu áudio)."""
    from wyoming.asr import Transcribe, Transcript
    from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
    from wyoming.event import Event
    from wyoming.info import Describe
    from wyoming.server import AsyncEventHandler
    from wyoming.tts import Synthesize

    descricao = info(nome_stt, nome_voz).event()
    limite = MAX_AUDIO_S * TAXA_STT * 2  # bytes de int16 mono
    vez = asyncio.Semaphore(1)  # uma transcrição ou síntese por vez (o Piper não é seguro em várias threads)
    conexoes = [0]

    class Atendente(AsyncEventHandler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.conversor = AudioChunkConverter(rate=TAXA_STT, width=2, channels=1)
            self.audio = bytearray()

        async def run(self) -> None:
            if conexoes[0] >= MAX_CONEXOES:
                log.warning("Wyoming: conexões demais; recusada")
                self.writer.close()
                return
            conexoes[0] += 1
            try:
                while (evento := await ler_evento(self.reader)) is not None:
                    try:
                        if not await self.handle_event(evento):
                            break
                    except (ValueError, KeyError, TypeError) as e:  # evento malformado: encerra só esta conexão
                        log.warning("Wyoming: evento inválido (%s); conexão encerrada", type(e).__name__)
                        break
            finally:
                conexoes[0] -= 1
                self.writer.close()

        async def handle_event(self, event: Event) -> bool:
            if Describe.is_type(event.type):
                await self.write_event(descricao)
            elif Transcribe.is_type(event.type) or AudioStart.is_type(event.type):
                self.audio.clear()
            elif AudioChunk.is_type(event.type):
                if len(self.audio) < limite:
                    pedaco = self.conversor.convert(AudioChunk.from_event(event))
                    self.audio.extend(pedaco.audio[: limite - len(self.audio)])
            elif AudioStop.is_type(event.type):
                pcm = np.frombuffer(bytes(self.audio), dtype="<i2")
                self.audio.clear()
                texto = ""
                if pcm.size:
                    async with vez:
                        texto = await asyncio.to_thread(transcritor.transcrever, pcm, TAXA_STT)
                await self.write_event(Transcript(text=texto).event())
            elif Synthesize.is_type(event.type):
                texto = " ".join(Synthesize.from_event(event).text.split())[:MAX_TEXTO]
                async with vez:
                    audio = await asyncio.to_thread(voz.sintetizar, texto)
                taxa = int(voz.taxa)
                await self.write_event(AudioStart(rate=taxa, width=2, channels=1).event())
                bruto = para_int16(audio)
                passo = PEDACO_AMOSTRAS * 2
                for i in range(0, len(bruto), passo):
                    await self.write_event(AudioChunk(rate=taxa, width=2, channels=1, audio=bruto[i : i + passo]).event())
                await self.write_event(AudioStop().event())
            return True  # mantém a conexão

    return Atendente


async def servir(cfg) -> None:
    from wyoming.server import AsyncServer

    from vision.voice.stt import carregar_transcritor
    from vision.voice.tts import carregar_piper

    uri = endereco_seguro(str(cfg.get("wyoming.endereco", "tcp://127.0.0.1:10300")),
                          bool(cfg.get("wyoming.permitir_rede", False)))
    transcritor = await asyncio.to_thread(carregar_transcritor, cfg)
    voz = await asyncio.to_thread(carregar_piper, cfg)
    atendente = criar_atendente(transcritor, voz, transcritor.nome, str(cfg.get("voz.voz_piper")))
    servidor = AsyncServer.from_uri(uri)
    print(f"Wyoming do Vision em {uri} (fala → texto: {transcritor.nome}; voz: {cfg.get('voz.voz_piper')}). "
          "Ctrl+C para parar.")
    await servidor.run(partial(atendente))
