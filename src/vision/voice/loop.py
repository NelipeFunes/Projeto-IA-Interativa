"""Loop de voz com conversa aberta e fechada por você.

- Esperando: só "Hey Vision" (ou o atalho) acorda. Ele dá um bipe e já ouve o pedido (sem "Oi, Felipe",
  pedido de 01/10). "Hey Vision, o que eu tenho hoje?" abre e já responde.
- Em conversa: tudo o que você fala vai para ele, sem repetir o nome, até "Vision, standby"
  ou 2 minutos de silêncio. Aí ele volta a esperar o "Hey Vision".
- O atalho durante a fala interrompe e começa a ouvir.

Pedido do Felipe (30/09): antes, depois de cada resposta ele ouvia tudo por 8 s e pegava conversa que não
era com ele. Agora quem abre e fecha a conversa é você.

Como "Hey Vision" não tem modelo pronto de ativação, esperando ele transcreve (localmente) o começo de cada
fala curta e procura o nome (voice/comandos.py). Essas transcrições não vão para a tela, o log nem o disco.
Com `voz.ativacao: modelo`, usa o openWakeWord (o "Hey Jarvis" de antes).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from vision.brain.agent import Agente
from vision.voice.audio import TAXA, bipe, bipe_desligar
from vision.voice.comandos import achar_ativacao, e_despedida
from vision.voice.wake import DetectorFala, PalavraAtivacao

log = logging.getLogger(__name__)

FALA_DE_ERRO = "Não consegui pensar agora. O modelo pode estar carregando; tenta de novo em um instante."
FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")
BLOCO_S = 0.08
TRECHO_ATIVACAO_S = 2.5  # para achar o nome, basta transcrever o começo da fala (mais leve para a CPU)
MAXIMO_CANDIDATO_S = 12.0  # fala mais longa que isso, esperando, não é alguém chamando: nem transcreve
PASSO_NIVEL_S = 1 / 15  # ~15 atualizações de volume por segundo para a tela


def nivel_do_bloco(bloco: np.ndarray) -> float:
    """Volume de 0 a 1 de um bloco int16 do microfone (fala normal fica entre 0,3 e 0,8)."""
    if bloco.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean((bloco.astype(np.float32) / 32768.0) ** 2)))
    return min(1.0, rms * 8.0)


def envelope(audio: np.ndarray, taxa: int, passo_s: float = PASSO_NIVEL_S) -> np.ndarray:
    """Volume da fala sintetizada (float32) a cada `passo_s`, normalizado para 0..1."""
    n = max(1, int(taxa * passo_s))
    if audio.size == 0:
        return np.zeros(0, np.float32)
    partes = audio[: audio.size - audio.size % n].reshape(-1, n) if audio.size >= n else audio.reshape(1, -1)
    rms = np.sqrt(np.mean(partes.astype(np.float32) ** 2, axis=1))
    pico = float(rms.max()) or 1.0
    return (rms / pico).astype(np.float32)


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
        ativacao_por_texto: bool = True,
        silencio_max_s: float = 120.0,
        prazo_confirmacao_s: float = 30.0,
        saudacao: str = "",
        despedida: str = "",
        ao_evento: Callable[[dict[str, Any]], None] | None = None,
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
        self._carregando: asyncio.Future | None = None
        # Falas pedidas de fora do laço (a resposta de uma confirmação da voz feita pela tela). Faladas pelo
        # próprio laço, entre um bloco e outro: nunca por cima de outra fala, e o microfone descarta o eco.
        self.para_falar: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.estado = "ocioso"
        self.historico: list[dict[str, Any]] = []  # para testes e para o relatório
        self.ativacao_por_texto = ativacao_por_texto
        self.saudacao = saudacao
        self.despedida = despedida
        # Conversa aberta: tudo o que você fala vai para ele. Silêncio contado em blocos de 80 ms (tempo de
        # áudio, não relógio: igual no microfone e nos testes).
        self.em_conversa = False
        self.ajustar_silencio(silencio_max_s)
        self.silencio = 0
        self.voz_seguida = 0
        # Confirmação por voz tem prazo: um "beleza" da TV 5 min depois não pode virar "sim" (revisão do PR 3).
        self.prazo_confirmacao_s = prazo_confirmacao_s
        self.pergunta_em: float | None = None
        self.pre_fala: deque[np.ndarray] = deque(maxlen=4)  # 320 ms antes do início, para não cortar a 1ª sílaba
        # Para a tela: estado do orbe e volumes (microfone quando ouve, a própria voz quando fala).
        self.ao_evento = ao_evento
        self._estado_tela = ""

    # ------------------------------------------------------------------ eventos para a tela

    def _emitir(self, evento: dict[str, Any]) -> None:
        if self.ao_evento is not None:
            self.ao_evento(evento)

    def _pausado(self) -> bool:
        return self.jogando or bool(self.flag_dormindo and self.flag_dormindo.exists())

    def _mostrar(self, estado: str) -> None:
        if estado == "ocioso" and self.em_conversa and not self._pausado():
            estado = "ouvindo"  # conversa aberta: ele está te ouvindo
        elif estado == "ocioso" and self.jogando:
            estado = "jogo"
        elif estado == "ocioso" and self.flag_dormindo is not None and self.flag_dormindo.exists():
            estado = "dormindo"
        if estado != self._estado_tela:
            self._estado_tela = estado
            self._emitir({"tipo": "estado", "valor": estado})

    # ------------------------------------------------------------------ controles externos

    def apertou_atalho(self) -> None:
        """Chamado pela thread do teclado (via call_soon_threadsafe)."""
        self.saida.interromper.set()
        self.acionar.set()

    def reavaliar_estado(self) -> None:
        """A escuta foi pausada/retomada por fora (bandeja): o orbe mostra "dormindo" ou volta ao normal."""
        if self.estado == "ocioso":
            self._mostrar("ocioso")

    def escuta_ligada(self) -> bool:
        """Se o "Hey Vision" pode acordar agora (não no jogo, não com a escuta pausada na bandeja)."""
        if self._pausado():
            return False
        return self.ativacao_por_texto or self.ativacao is not None

    # ------------------------------------------------------------------ loop

    async def rodar(self, limite: int | None = None) -> None:
        """`limite`: para depois de tantas respostas (testes). Sem limite, roda enquanto houver áudio."""
        gravado: list[np.ndarray] = []
        origem = ""
        self.feitas = 0
        self._mostrar("ocioso")
        async for bloco in self.entrada.blocos():
            if self.estado == "ocioso" and not self.para_falar.empty():
                await self._falar_de_fora(self.para_falar.get_nowait())
                continue
            if self.estado == "ocioso":
                self.pre_fala.append(bloco)
                origem = await self._esperando(bloco)
                if not origem:
                    continue
                self.detector.zerar()
                gravado = []
                if origem in ("conversa", "candidato"):
                    gravado = list(self.pre_fala)  # a fala já começou: guarda o começo dela
                    for b in gravado:
                        self.detector.bloco(b)
                else:
                    await self._bipe(subindo=True)
                self.estado = "gravando"
                if origem != "candidato":  # esperando, ninguém sabe que ele está transcrevendo para achar o nome
                    self.escrever("…ouvindo")
                    self._mostrar("ouvindo")
                continue

            gravado.append(bloco)
            if origem == "candidato" and self.acionar.is_set():
                # Atalho no meio de uma fala qualquer: vira gravação para ele, sem perder o que você já disse.
                self.acionar.clear()
                origem = "atalho"
                if not self._pausado():
                    self._abrir_conversa()
                self.escrever("…ouvindo")
                self._mostrar("ouvindo")
            if origem != "candidato":
                self._emitir({"tipo": "nivel", "fonte": "mic", "valor": nivel_do_bloco(bloco)})
            if origem == "candidato" and len(gravado) * BLOCO_S > MAXIMO_CANDIDATO_S:
                gravado, self.estado = [], "ocioso"  # falação longa, não é alguém chamando
                continue
            if not self.detector.bloco(bloco):
                continue
            pcm = np.concatenate(gravado) if self.detector.falou else np.zeros(0, np.int16)
            self.pre_fala.clear()  # o começo desta fala não pode entrar na próxima
            if origem == "candidato":
                await self._candidato(pcm)
                if self.em_conversa:  # acordou e falou: o que tocou no alto-falante não é você
                    self.entrada.descartar()
                # Se não era com ele, a fila fica: um "Hey Vision" dito enquanto ele transcrevia não se perde.
            elif origem == "conversa" and self._pausado():
                pass  # pausou (bandeja ou jogo) enquanto você falava: não vai para ninguém
            else:
                self._mostrar("pensando")
                self._emitir({"tipo": "nivel", "fonte": "mic", "valor": 0.0})
                await self._fala_na_conversa(pcm)
                self.entrada.descartar()  # o que tocou enquanto ele falava (a própria voz) não é você
            if self.ativacao is not None:
                self.ativacao.zerar()
            self.estado = "ocioso"
            self.voz_seguida = 0
            self._mostrar("ocioso")
            if limite is not None and self.feitas >= limite:
                return

    async def _esperando(self, bloco: np.ndarray) -> str:
        """Um bloco sem gravação em andamento. Devolve de onde vem a próxima gravação ("" = nenhuma)."""
        if self.em_conversa and self._pausado():
            # Pausou a escuta na bandeja ou abriu o jogo: a conversa acaba na hora (revisão do PR 3).
            self.escrever("(conversa encerrada: escuta pausada ou modo jogo)")
            await self._fechar_conversa(falar=False)
            return ""
        if self.acionar.is_set():  # atalho: acorda (se precisar) e já ouve
            self.acionar.clear()
            if not self.em_conversa and not self._pausado():  # no jogo, vale só uma fala, sem abrir conversa
                self._abrir_conversa()
            return "atalho"
        if self.em_conversa:
            self.silencio += 1
            if self.silencio >= self.blocos_silencio_max:
                self.escrever("(conversa encerrada: 2 minutos sem falar. Para voltar: 'Hey Vision')")
                await self._fechar_conversa(falar=False)
                return ""
            return "conversa" if self._comecou_a_falar(bloco) else ""
        if not self.escuta_ligada():
            return ""
        if not self.ativacao_por_texto:  # openWakeWord ("Hey Jarvis")
            if self.ativacao is not None and self.ativacao.ouvir(bloco):
                self.agente.cancelar_pendente("voz", "voz")  # como no "Hey Vision": nada de antes é confirmado
                self.pergunta_em = None
                self._abrir_conversa()
                if self.saudacao.strip():  # sem saudação, o bipe é o do laço (origem "atalho"): um só
                    await self._dizer(self.saudacao)
                    self.entrada.descartar()  # a saudação que saiu na caixa de som não é você falando
                return "atalho"
            return ""
        return "candidato" if self._comecou_a_falar(bloco) else ""

    def _comecou_a_falar(self, bloco: np.ndarray) -> bool:
        self.voz_seguida = self.voz_seguida + 1 if self.detector.tem_voz(bloco) else 0
        return self.voz_seguida >= 2  # 160 ms de voz seguida

    # ------------------------------------------------------------------ abrir e fechar a conversa

    def _abrir_conversa(self) -> None:
        # O modelo pode ter saído da VRAM (30 min parado): carrega enquanto ele cumprimenta e você fala.
        if self._carregando is None or self._carregando.done():
            self._carregando = asyncio.ensure_future(self.agente.carregar())
        self.em_conversa = True
        self.silencio = 0
        self.escrever("(conversa aberta: fale à vontade; para encerrar, 'Vision, standby')")
        self._mostrar("ouvindo")

    async def _fechar_conversa(self, falar: bool) -> None:
        self.em_conversa = False
        self.pergunta_em = None
        # Uma confirmação no ar não sobrevive ao fim da conversa (e o cartão some da tela).
        self.agente.cancelar_pendente("voz", "voz")
        if falar and self.despedida:  # por padrão não fala nada: o bipe de desligar basta (pedido de 30/09)
            await self._dizer(self.despedida)
        if self.bipes:
            await asyncio.to_thread(self.saida.tocar, bipe_desligar(), 22050)
        self._mostrar("ocioso")

    async def _candidato(self, pcm: np.ndarray) -> None:
        """Esperando: alguém falou. Se começou chamando o assistente, abre a conversa."""
        if pcm.size == 0:
            return
        comeco = pcm[: int(TRECHO_ATIVACAO_S * TAXA)]
        texto = await self._transcrever(comeco)
        resto = achar_ativacao(texto)
        if resto is None:
            return  # não era com ele: nada é guardado nem mostrado
        if pcm.size > comeco.size:  # a fala continua: o pedido vem inteiro (nunca o trecho cortado em 2,5 s)
            inteiro = await self._transcrever(pcm)
            if inteiro.strip():  # se o STT falhou só aqui, fica o que já foi ouvido no começo
                resto = achar_ativacao(inteiro)
                if resto is None:
                    resto = inteiro.strip()
        if resto and e_despedida(resto):
            return  # "Hey Vision, standby" com a conversa já fechada: nada a fazer
        # Pendência de antes da conversa (atalho no modo jogo) nunca é respondida por um "Hey Vision, sim".
        self.agente.cancelar_pendente("voz", "voz")
        self.pergunta_em = None
        self._abrir_conversa()
        if not resto:
            await self._saudar()
            return
        await self._bipe(subindo=True)  # "Hey Vision, <pedido>": o bipe avisa que ouviu
        self._mostrar("pensando")
        await self._responder(resto, 0.0)

    async def _fala_na_conversa(self, pcm: np.ndarray) -> None:
        if pcm.size == 0:
            self.escrever("(não ouvi nada)")
            return
        t0 = time.perf_counter()
        texto = await self._transcrever(pcm)
        t_stt = time.perf_counter() - t0
        if not texto.strip():
            self.escrever("(não entendi o áudio)")
            return
        self.silencio = 0  # só fala de verdade reinicia os 2 min (tosse, porta e ruído não)
        self.escrever(f"Você: {texto}")
        if e_despedida(texto):
            self.escrever("(conversa encerrada. Para voltar: 'Hey Vision')")
            await self._fechar_conversa(falar=True)
            self.historico.append({"felipe": texto, "vision": self.despedida, "despedida": True})
            self.feitas += 1
            return
        await self._bipe(subindo=False)
        await self._responder(texto, t_stt)

    async def _transcrever(self, pcm: np.ndarray) -> str:
        """O STT roda em toda fala da sala: um erro dele vale como "não entendi", não derruba a escuta."""
        try:
            return await asyncio.to_thread(self.stt.transcrever, pcm, TAXA)
        except Exception:  # noqa: BLE001
            log.exception("o STT falhou num trecho de %.1f s", pcm.size / TAXA)
            return ""

    async def _saudar(self) -> None:
        """Acordou só com "Hey Vision": um bipe e já ouve (a `voz.saudacao`, se houver, é falada no lugar)."""
        if self.saudacao.strip():
            await self._dizer(self.saudacao)
        else:
            await self._bipe(subindo=True)
            self._mostrar("ouvindo")

    async def _dizer(self, texto: str) -> None:
        """Uma frase dele fora das respostas do modelo (saudação, despedida): falada e mostrada na tela."""
        self._emitir({"tipo": "resposta", "texto": texto})
        fila: asyncio.Queue[str | None] = asyncio.Queue()
        fila.put_nowait(texto)
        fila.put_nowait(None)
        await self._falador(fila, [])
        self._mostrar("ocioso")

    async def _responder(self, texto: str, t_stt: float) -> None:
        self.feitas += 1
        # Vale para todo caminho (conversa, "Hey Vision, sim", atalho no jogo): passou do prazo, o que vier
        # agora não é resposta ao "Confirma?" (2ª revisão do PR 3).
        if self.pergunta_em is not None and time.monotonic() - self.pergunta_em > self.prazo_confirmacao_s:
            self.agente.cancelar_pendente("voz", "voz")
            self.pergunta_em = None
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
        r = None
        try:
            r = await self.agente.responder(texto, canal="voz", sessao="voz", ao_texto=ao_texto)
        except Exception:  # noqa: BLE001 - Ollama fora do ar (ex.: logo depois de ligar o PC) não pode matar a voz
            log.exception("o agente falhou numa pergunta por voz")
            pendente[0] = ""
            fila.put_nowait(FALA_DE_ERRO)
            self._emitir({"tipo": "resposta", "texto": FALA_DE_ERRO})
        finally:
            if pendente[0].strip():
                fila.put_nowait(pendente[0])
            fila.put_nowait(None)
            await falador
        if r is None:
            self.historico.append({"felipe": texto, "vision": FALA_DE_ERRO, "erro": True})
            return
        self.pergunta_em = time.monotonic() if r.aguardando_confirmacao else None
        usadas = ", ".join(f["nome"] for f in r.ferramentas)
        ate_falar = (primeira_fala[0] - t1) if primeira_fala else 0.0
        self.escrever(f"{self.agente.nome_assistente}: {r.texto}")
        self.escrever(f"  [ouvir {t_stt:.1f}s · pensar {r.segundos:.1f}s · 1ª palavra {ate_falar:.1f}s"
                      f"{' · ' + usadas if usadas else ''}]")
        self.historico.append({"felipe": texto, "vision": r.texto, "ferramentas": usadas,
                               "stt_s": t_stt, "pensar_s": r.segundos, "ate_falar_s": ate_falar})

    async def _falador(self, fila: asyncio.Queue[str | None], primeira_fala: list[float]) -> None:
        self.saida.interromper.clear()
        interrompido = False
        while (frase := await fila.get()) is not None:
            if interrompido or not frase.strip():
                continue
            voz = self.voz  # uma vez por frase: a tela de ajustes pode trocar a voz no meio
            audio = await asyncio.to_thread(voz.sintetizar, frase)
            if not primeira_fala:
                primeira_fala.append(time.perf_counter())
            self._mostrar("falando")
            ondas = asyncio.create_task(self._emitir_voz(envelope(audio, voz.taxa))) if self.ao_evento else None
            try:
                if not await asyncio.to_thread(self.saida.tocar, audio, voz.taxa):
                    interrompido = True
            finally:
                if ondas is not None:
                    ondas.cancel()
                    self._emitir({"tipo": "nivel", "fonte": "voz", "valor": 0.0})

    async def _emitir_voz(self, niveis: np.ndarray) -> None:
        """Volume da própria fala no ritmo em que ela toca: o orbe ondula junto."""
        inicio = time.perf_counter()
        for i, v in enumerate(niveis):
            espera = inicio + i * PASSO_NIVEL_S - time.perf_counter()
            if espera > 0:
                await asyncio.sleep(espera)
            self._emitir({"tipo": "nivel", "fonte": "voz", "valor": float(v)})

    def pedir_fala(self, texto: str, voz: Any = None) -> None:
        """Pede ao laço para falar isto assim que estiver livre (só no loop do núcleo). `voz`: outra voz só
        para esta frase (a amostra da tela de ajustes)."""
        if texto.strip():
            self.para_falar.put_nowait((texto, voz))

    async def _falar_de_fora(self, pedido: tuple[str, Any]) -> None:
        texto, voz = pedido
        if self._pausado():
            return  # jogo ou escuta pausada: fica só na tela
        fila: asyncio.Queue[str | None] = asyncio.Queue()
        fila.put_nowait(texto)
        fila.put_nowait(None)
        antes = self.voz
        if voz is not None:
            self.voz = voz
        try:
            await self._falador(fila, [])
        except Exception:  # noqa: BLE001 - uma fala que falha (síntese, alto-falante) não derruba a escuta
            log.exception("não consegui falar uma frase pedida de fora do laço")
        finally:
            if self.voz is voz and voz is not None:  # se salvaram outra voz enquanto isso, fica a salva
                self.voz = antes
        if voz is None:
            self.pergunta_em = None
        self.pre_fala.clear()
        self.entrada.descartar()  # a própria voz não é você falando
        self._mostrar("ocioso")

    def ajustar_silencio(self, silencio_max_s: float) -> None:
        self.blocos_silencio_max = int(silencio_max_s / BLOCO_S)

    async def falar(self, texto: str) -> None:
        """Fala uma frase fora de conversa (aviso de login, lembrete)."""
        await asyncio.to_thread(self.saida.tocar, self.voz.sintetizar(texto), self.voz.taxa)

    async def _bipe(self, subindo: bool) -> None:
        if self.bipes:
            await asyncio.to_thread(self.saida.tocar, bipe(subindo), 22050)

    # ------------------------------------------------------------------ modo jogo

    async def vigiar_jogos(self, processos: list[str], a_cada_s: float) -> None:
        import psutil

        alvos = {p.lower() for p in processos}

        def rodando() -> bool:
            return any((p.info.get("name") or "").lower() in alvos for p in psutil.process_iter(["name"]))

        primeira = True
        while True:
            agora = await asyncio.to_thread(rodando)
            if primeira and not agora:
                await self.agente.carregar()  # o núcleo acabou de subir sem jogo aberto: modelo pronto
            primeira = False
            if agora and not self.jogando:
                self.jogando = True
                if self.estado == "ocioso":
                    self._mostrar("ocioso")  # vira "jogo"
                await self.agente.descarregar()
                self.escrever("[modo jogo] modelo fora da VRAM; 'Hey Vision' pausado, o atalho continua valendo.")
            elif not agora and self.jogando:
                self.jogando = False
                if self.estado == "ocioso":
                    self._mostrar("ocioso")
                self.escrever("[modo jogo] fim do jogo; 'Hey Vision' de volta.")
                await self.agente.liberar_modelo()
            await asyncio.sleep(a_cada_s)


@contextmanager
def preparar_voz(
    cfg,
    agente: Agente,
    *,
    com_ativacao: bool = True,
    escrever: Callable[[str], None] = print,
    ao_evento: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[tuple[LoopVoz, Any]]:
    """Carrega voz, transcrição, ativação e microfone e monta o LoopVoz (usado pelo `vision voz` e pelo núcleo).

    Devolve (laço, atalho). O microfone e o atalho são fechados na saída.
    """
    from vision.voice.audio import Microfone, Saida
    from vision.voice.stt import carregar_transcritor
    from vision.voice.tts import carregar_voz
    from vision.voice.wake import Atalho

    t = time.perf_counter()
    voz = carregar_voz(cfg)
    stt = carregar_transcritor(cfg)
    pasta_oww = cfg.modelos / "openwakeword"
    ativacao = None
    por_texto = cfg.get("voz.ativacao", "transcricao") != "modelo"
    if com_ativacao and not por_texto:
        try:
            ativacao = PalavraAtivacao(pasta_oww, cfg.get("voz.palavra_ativacao", "hey_jarvis"),
                                       float(cfg.get("voz.limiar_ativacao", 0.3)))
        except Exception as e:  # noqa: BLE001
            escrever(f"[aviso] palavra de ativação indisponível ({e}); use o atalho.")
    detector = DetectorFala(pasta_oww / "silero_vad.onnx", int(cfg.get("voz.silencio_fim_ms", 800)),
                            float(cfg.get("voz.maximo_fala_s", 20)))
    saida = Saida(cfg)
    escrever(f"Voz carregada em {time.perf_counter() - t:.1f}s (STT: {stt.nome}, voz: {cfg.get('voz.voz_piper')})")
    with Microfone(cfg) as mic:
        laco = LoopVoz(agente, voz, stt, mic, saida, detector, ativacao,
                       flag_dormindo=cfg.dados / "dormindo.flag", escrever=escrever, ao_evento=ao_evento,
                       ativacao_por_texto=com_ativacao and por_texto,
                       silencio_max_s=float(cfg.get("voz.conversa_silencio_max_s", 120)),
                       prazo_confirmacao_s=float(cfg.get("voz.confirmacao_prazo_s", 30)),
                       saudacao=cfg.get("voz.saudacao") or "",
                       despedida=cfg.get("voz.despedida") or "")
        loop = asyncio.get_running_loop()
        atalho = Atalho(cfg.get("voz.atalho", "ctrl+alt+j"), lambda: loop.call_soon_threadsafe(laco.apertou_atalho))
        for nota in mic.notas:
            escrever(f"  [microfone] {nota}")
        escrever(f"Microfone: {mic.nome} · Saída: {saida.nome}")
        try:
            yield laco, atalho
        finally:
            atalho.fechar()


def vigiar_jogos_se_ligado(cfg, laco: LoopVoz) -> asyncio.Task | None:
    if not cfg.get("modo_jogo.ativo", True):
        return None
    return asyncio.create_task(laco.vigiar_jogos(
        cfg.get("modo_jogo.processos", ["cs2.exe"]), float(cfg.get("modo_jogo.checar_a_cada_s", 20))))


async def rodar_voz(cfg, com_ativacao: bool = True) -> None:
    from vision.google_login import aviso_login
    from vision.montagem import montar

    nome = cfg.get("assistente.nome", "Vision")
    async with montar(cfg) as j:
        with preparar_voz(cfg, j.agente, com_ativacao=com_ativacao) as (laco, atalho):
            print(f"Diga 'Hey {nome}'" + (f" ou aperte {atalho.combinacao}" if atalho.ativo else "")
                  + f". Para encerrar a conversa: '{nome}, standby'. Ctrl+C para sair.")
            for servidor, st in j.host.status().items():
                if st != "ok":
                    print(f"  [aviso] MCP {servidor}: {st[:120]}")
            jogos = vigiar_jogos_se_ligado(cfg, laco)
            if aviso := aviso_login(cfg):
                print(f"{nome}: {aviso}")
                await laco.falar(aviso.split(" Rode:")[0] + ".")
            try:
                await laco.rodar()
            finally:
                if jogos is not None:
                    jogos.cancel()
