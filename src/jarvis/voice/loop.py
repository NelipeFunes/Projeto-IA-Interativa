"""Loop de voz: 'Hey Jarvis' (ou atalho) → grava até você parar → transcreve → pensa → fala frase a frase.

Depois que o Jarvis faz uma pergunta ("Confirma?"), ele já escuta a resposta sem precisar de "Hey Jarvis".
O atalho durante a fala interrompe o Jarvis e começa a ouvir.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.brain.agent import Agente
from jarvis.voice.audio import TAXA, bipe
from jarvis.voice.wake import DetectorFala, PalavraAtivacao

FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


class LoopVoz:
    def __init__(
        self,
        agente: Agente,
        voz: Any,
        transcritor: Any,
        entrada: Any,
        saida: Any,
        detector: DetectorFala,
        ativacao: PalavraAtivacao | None = None,
        *,
        flag_dormindo: Path | None = None,
        escrever: Callable[[str], None] = print,
        bipes: bool = True,
    ):
        self.agente = agente
        self.voz = voz
        self.stt = transcritor
        self.entrada = entrada
        self.saida = saida
        self.detector = detector
        self.ativacao = ativacao
        self.flag_dormindo = flag_dormindo
        self.escrever = escrever
        self.bipes = bipes
        self.jogando = False
        self.acionar = asyncio.Event()
        self.seguir_ouvindo = False
        self.estado = "ocioso"
        self.historico: list[dict[str, Any]] = []  # para testes e para o relatório

    # ------------------------------------------------------------------ controles externos

    def apertou_atalho(self) -> None:
        """Chamado pela thread do teclado (via call_soon_threadsafe)."""
        self.saida.interromper.set()
        self.acionar.set()

    def ativacao_ligada(self) -> bool:
        if self.ativacao is None or self.jogando:
            return False
        return not (self.flag_dormindo and self.flag_dormindo.exists())

    # ------------------------------------------------------------------ loop

    async def rodar(self, limite: int | None = None) -> None:
        gravado: list[np.ndarray] = []
        feitas = 0
        async for bloco in self.entrada.blocos():
            if self.estado == "ocioso":
                disparou = False
                if self.acionar.is_set():
                    self.acionar.clear()
                    disparou = True
                elif self.seguir_ouvindo:
                    self.seguir_ouvindo = False
                    disparou = True
                elif self.ativacao_ligada() and self.ativacao.ouvir(bloco):
                    disparou = True
                if disparou:
                    await self._bipe(subindo=True)
                    self.detector.zerar()
                    gravado = []
                    self.estado = "gravando"
                continue

            gravado.append(bloco)
            if not self.detector.bloco(bloco):
                continue
            self.estado = "pensando"
            falou = self.detector.falou
            await self._processar(np.concatenate(gravado) if falou else np.zeros(0, np.int16))
            feitas += 1
            self.entrada.descartar()
            if self.ativacao is not None:
                self.ativacao.zerar()
            self.estado = "ocioso"
            if limite is not None and feitas >= limite:
                return

    async def _processar(self, pcm: np.ndarray) -> None:
        if pcm.size == 0:
            self.escrever("(não ouvi nada)")
            self.historico.append({"felipe": "", "jarvis": ""})
            return
        t0 = time.perf_counter()
        texto = await asyncio.to_thread(self.stt.transcrever, pcm, TAXA)
        t_stt = time.perf_counter() - t0
        if not texto.strip():
            self.escrever("(não entendi o áudio)")
            self.historico.append({"felipe": "", "jarvis": ""})
            return
        self.escrever(f"Você: {texto}")
        await self._bipe(subindo=False)

        fila: asyncio.Queue[str | None] = asyncio.Queue()
        pendente = [""]
        primeira_fala: list[float] = []

        def ao_texto(parte: str) -> None:
            pendente[0] += parte
            pedacos = FIM_DE_FRASE.split(pendente[0])
            for frase in pedacos[:-1]:
                fila.put_nowait(frase)
            pendente[0] = pedacos[-1]

        falador = asyncio.create_task(self._falador(fila, primeira_fala))
        t1 = time.perf_counter()
        try:
            r = await self.agente.responder(texto, canal="voz", sessao="voz", ao_texto=ao_texto)
        finally:
            if pendente[0].strip():
                fila.put_nowait(pendente[0])
            fila.put_nowait(None)
            await falador
        usadas = ", ".join(f["nome"] for f in r.ferramentas)
        ate_falar = (primeira_fala[0] - t1) if primeira_fala else 0.0
        self.escrever(f"Jarvis: {r.texto}")
        self.escrever(f"  [ouvir {t_stt:.1f}s · pensar {r.segundos:.1f}s · 1ª palavra {ate_falar:.1f}s"
                      f"{' · ' + usadas if usadas else ''}]")
        self.historico.append({"felipe": texto, "jarvis": r.texto, "ferramentas": usadas,
                               "stt_s": t_stt, "pensar_s": r.segundos, "ate_falar_s": ate_falar})
        self.seguir_ouvindo = r.aguardando_confirmacao or r.texto.rstrip().endswith("?")

    async def _falador(self, fila: asyncio.Queue[str | None], primeira_fala: list[float]) -> None:
        self.saida.interromper.clear()
        interrompido = False
        while (frase := await fila.get()) is not None:
            if interrompido or not frase.strip():
                continue
            audio = await asyncio.to_thread(self.voz.sintetizar, frase)
            if not primeira_fala:
                primeira_fala.append(time.perf_counter())
            if not await asyncio.to_thread(self.saida.tocar, audio, self.voz.taxa):
                interrompido = True

    async def _bipe(self, subindo: bool) -> None:
        if self.bipes:
            await asyncio.to_thread(self.saida.tocar, bipe(subindo), 22050)

    # ------------------------------------------------------------------ modo jogo

    async def vigiar_jogos(self, processos: list[str], a_cada_s: float) -> None:
        import psutil

        alvos = {p.lower() for p in processos}

        def rodando() -> bool:
            return any((p.info.get("name") or "").lower() in alvos for p in psutil.process_iter(["name"]))

        while True:
            agora = await asyncio.to_thread(rodando)
            if agora and not self.jogando:
                self.jogando = True
                await self.agente.descarregar()
                self.escrever("[modo jogo] modelo fora da VRAM; 'Hey Jarvis' pausado, o atalho continua valendo.")
            elif not agora and self.jogando:
                self.jogando = False
                self.escrever("[modo jogo] fim do jogo; 'Hey Jarvis' de volta.")
            await asyncio.sleep(a_cada_s)


async def rodar_voz(cfg, com_ativacao: bool = True) -> None:
    from jarvis.google_login import aviso_login
    from jarvis.montagem import montar
    from jarvis.voice.audio import Microfone, Saida
    from jarvis.voice.stt import carregar_transcritor
    from jarvis.voice.tts import carregar_voz
    from jarvis.voice.wake import Atalho

    t = time.perf_counter()
    voz = carregar_voz(cfg)
    stt = carregar_transcritor(cfg)
    pasta_oww = cfg.modelos / "openwakeword"
    ativacao = None
    if com_ativacao:
        try:
            ativacao = PalavraAtivacao(pasta_oww, cfg.get("voz.palavra_ativacao", "hey_jarvis"),
                                       float(cfg.get("voz.limiar_ativacao", 0.3)))
        except Exception as e:  # noqa: BLE001
            print(f"[aviso] palavra de ativação indisponível ({e}); use o atalho.")
    detector = DetectorFala(pasta_oww / "silero_vad.onnx", int(cfg.get("voz.silencio_fim_ms", 800)),
                            float(cfg.get("voz.maximo_fala_s", 20)))
    saida = Saida(cfg)
    print(f"Voz carregada em {time.perf_counter() - t:.1f}s (STT: {stt.nome}, voz: {cfg.get('voz.voz_piper')})")

    async with montar(cfg) as j:
        with Microfone(cfg) as mic:
            laco = LoopVoz(j.agente, voz, stt, mic, saida, detector, ativacao,
                           flag_dormindo=cfg.dados / "dormindo.flag")
            loop = asyncio.get_running_loop()
            atalho = Atalho(cfg.get("voz.atalho", "ctrl+alt+j"), lambda: loop.call_soon_threadsafe(laco.apertou_atalho))
            for nota in mic.notas:
                print(f"  [microfone] {nota}")
            print(f"Microfone: {mic.nome} · Saída: {saida.nome}")
            print("Diga 'Hey Jarvis'" + (f" ou aperte {atalho.combinacao}" if atalho.ativo else "") + ". Ctrl+C para sair.")
            for nome, st in j.host.status().items():
                if st != "ok":
                    print(f"  [aviso] MCP {nome}: {st[:120]}")
            tarefas = []
            if cfg.get("modo_jogo.ativo", True):
                tarefas.append(asyncio.create_task(laco.vigiar_jogos(
                    cfg.get("modo_jogo.processos", ["cs2.exe"]), float(cfg.get("modo_jogo.checar_a_cada_s", 20)))))
            if aviso := aviso_login(cfg):
                print(f"Jarvis: {aviso}")
                await asyncio.to_thread(saida.tocar, voz.sintetizar(aviso.split(" Rode:")[0] + "."), voz.taxa)
            try:
                await laco.rodar()
            finally:
                for tk in tarefas:
                    tk.cancel()
                atalho.fechar()
