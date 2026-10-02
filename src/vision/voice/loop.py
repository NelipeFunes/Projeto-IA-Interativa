"""Loop de voz com conversa aberta e fechada por você.

- Esperando: só "Hey Vision" (ou o atalho) acorda. Ele dá um bipe e já ouve o pedido (sem "Oi, Felipe",
  pedido de 01/10). "Hey Vision, o que eu tenho hoje?" abre e já responde.
- Em conversa: tudo o que você fala vai para ele, sem repetir o nome, até "Vision, standby"
  ou 2 minutos de silêncio. Aí ele volta a esperar o "Hey Vision".
- O atalho durante a fala interrompe e começa a ouvir. Por voz também, como o modo de voz do ChatGPT (pedido
  de 01/10): enquanto ele fala, um segundo detector escuta; meio segundo de voz sua e ele para na hora, e o que
  você está dizendo vira o próximo pedido ("para", "chega" só param; "standby" fecha a conversa).

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
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from vision.brain.agent import Agente
from vision.voice.audio import TAXA, alarme, bipe, bipe_desligar
from vision.voice.comandos import achar_ativacao, aprender_apelidos, e_despedida, e_interrupcao
from vision.voice.wake import DetectorFala, PalavraAtivacao

log = logging.getLogger(__name__)

FALA_DE_ERRO = "Não consegui pensar agora. O modelo pode estar carregando; tenta de novo em um instante."
FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")
# A 1ª frase sai já na 1ª vírgula ("Amanhã às dez você tem barbeiro, e…"): começa a falar antes do ponto.
PRIMEIRA_VIRGULA = re.compile(r"(.+?,)\s+(.*)", re.DOTALL)
PALAVRAS_ANTES_DA_VIRGULA = 3  # "Pronto, Felipe." não corta: pedaço curto demais soa picotado
PIPER_ATE_CARACTERES = 45


def _fechar_quando_abrir(abrindo: asyncio.Future) -> None:
    """O fluxo do fone abriu depois que a resposta já tinha acabado (ou foi cancelada): fecha sem tocar nada."""
    if abrindo.cancelled() or abrindo.exception() is not None:
        return
    try:
        abrindo.result().fechar(False)
    except Exception:  # noqa: BLE001
        log.exception("não consegui fechar a saída de som")


def fatiar(texto: str, primeira: bool) -> tuple[list[str], str]:
    """Texto do modelo chegando aos poucos → (frases prontas para falar, resto ainda incompleto).

    `primeira`: nada foi falado ainda; aí a 1ª vírgula já fecha um pedaço, se ele tiver 3+ palavras e o ponto
    ainda não tiver chegado (resposta de atalho chega inteira: cortar não adianta e a tiraria do Piper)."""
    prontas: list[str] = []
    if primeira:
        m = PRIMEIRA_VIRGULA.fullmatch(texto)
        if m and len(m.group(1).split()) >= PALAVRAS_ANTES_DA_VIRGULA and not re.search(r"[.!?]", texto):
            prontas.append(m.group(1))
            texto = m.group(2)
    *completas, resto = FIM_DE_FRASE.split(texto)
    return prontas + completas, resto
BLOCO_S = 0.08
PRAZO_FALA_CALIBRACAO_S = 14.0  # 6 s esperando a voz + 4 s de fala + folga: em relógio, se o microfone parar
TRECHO_ATIVACAO_S = 2.5  # para achar o nome, basta transcrever o começo da fala (mais leve para a CPU)
MAXIMO_CANDIDATO_S = 12.0  # fala mais longa que isso, esperando, não é alguém chamando: nem transcreve
ALARME = object()  # marca, na fila de falas de fora, um aviso de fim de timer
INTERROMPER_APOS_S = 0.5  # voz contínua enquanto ele fala, antes de cortar: tosse e estalo não cortam
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
        vigia: DetectorFala | None = None,
        interromper_apos_s: float = INTERROMPER_APOS_S,
        piper_ate_caracteres: int = PIPER_ATE_CARACTERES,
        registrar_ativacao: bool = False,
    ):
        self.agente = agente
        # Diagnóstico (voz.registrar_ativacao): escreve o que o STT ouviu no começo de cada fala, esperando o
        # "Hey Vision". Desligado por padrão: ligado, a conversa perto do PC vai para o log.
        self.registrar_ativacao = registrar_ativacao
        # Com o XTTS: resposta de uma frase só até esse tamanho ("Acendi a luz do quarto.") sai pelo Piper, na hora.
        self.piper_ate_caracteres = piper_ate_caracteres
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
        # Detector próprio (estado separado do principal) que escuta enquanto ele fala. None = sem interromper
        # por voz. `interrompido_por`: "parar" ou "standby" se a última fala foi cortada por voz.
        self.vigia = vigia
        self.blocos_para_interromper = max(1, round(interromper_apos_s / BLOCO_S))
        self.interrompido_por: str | None = None
        # O começo da sua fala que cortou a dele: o laço continua a gravação daí, sem perder nada.
        self._retomar: list[np.ndarray] | None = None
        # Calibração do "Hey Vision" (Ajustes): pedida pela tela, roda no laço quando ele está livre.
        self._calibracao: tuple[int, Callable[[list[dict[str, Any]], list[str]], Any]] | None = None
        self.calibrando = False

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

    # ------------------------------------------------------------------ calibração do "Hey Vision"

    def pedir_calibracao(self, vezes: int, ao_fim: Callable[[list[dict[str, Any]], list[str]], Any]) -> str | None:
        """Agenda a calibração; o laço roda quando estiver livre. Devolve o motivo se não der agora."""
        if self.calibrando or self._calibracao is not None:
            return "A calibração já está rodando."
        if self._pausado():
            return "A escuta está pausada (bandeja ou modo jogo): retome antes de calibrar."
        if not self.ativacao_por_texto:
            return "A calibração é para o \"Hey Vision\" por transcrição (voz.ativacao: transcricao)."
        self._calibracao = (max(1, min(10, int(vezes))), ao_fim)
        return None

    def calibracao_agendada(self) -> bool:
        """Pedida e ainda não começada (o núcleo cancela se o laço não pegar a tempo)."""
        return self._calibracao is not None and not self.calibrando

    def cancelar_calibracao_agendada(self) -> None:
        if not self.calibrando:
            self._calibracao = None

    async def _calibrar(self, vezes: int, ao_fim) -> None:
        """Bipe, uma fala sua, o que o STT entendeu no começo dela (como no "Hey Vision"); `vezes` vezes. No fim, as
        grafias que se repetiram e não acordaram (comandos.aprender_apelidos) vão para `ao_fim`."""
        # (`calibrando` já vem True do `rodar`: um 2º pedido durante o _fechar_conversa é recusado; revisão do PR 43)
        ouvidos: list[dict[str, Any]] = []
        try:
            if self.em_conversa:  # dentro do try: um bipe que falhe aqui não deixa `calibrando` preso (2ª revisão)
                await self._fechar_conversa(falar=False)
            for i in range(vezes):
                if self._pausado():  # pausou na bandeja ou abriu o jogo no meio: pausa é pausa (revisão do PR 43)
                    self._emitir({"tipo": "calibracao", "rodando": False,
                                  "erro": "Calibração cancelada: a escuta foi pausada (bandeja ou modo jogo)."})
                    return
                self._emitir({"tipo": "calibracao", "rodando": True, "etapa": i + 1, "de": vezes, "ouvidos": ouvidos})
                self._mostrar("ouvindo")
                await self._bipe(subindo=True)
                self.entrada.descartar()  # o bipe que saiu na caixa de som não é você
                try:
                    pcm = await asyncio.wait_for(self._uma_fala(), PRAZO_FALA_CALIBRACAO_S)
                except TimeoutError:  # o microfone parou de entregar: a vez fica vazia, a calibração não trava
                    pcm = np.zeros(0, np.int16)
                texto = await self._transcrever(pcm[: int(TRECHO_ATIVACAO_S * TAXA)]) if pcm.size else ""
                acordou = achar_ativacao(texto) is not None
                ouvidos = [*ouvidos, {"texto": texto.strip(), "acordou": acordou}]
                # No log só se acordou: o que se fala na sala não vai para o log (revisão do PR 43).
                self.escrever(f"[calibração] {i + 1}/{vezes}: {'acordou' if acordou else 'não acordou'}")
        finally:
            self.calibrando = False
            self._mostrar("ocioso")
            # Nada de antes vaza para depois: o atalho apertado no meio, o começo de fala guardado.
            self.pre_fala.clear()
            self.voz_seguida = 0
            self.acionar.clear()
        aprendidos = aprender_apelidos([o["texto"] for o in ouvidos])
        resultado = ao_fim(ouvidos, aprendidos)
        if asyncio.iscoroutine(resultado):
            await resultado

    async def _uma_fala(self, espera_s: float = 6.0, maximo_s: float = 4.0) -> np.ndarray:
        """Uma fala, do começo da voz até o silêncio. Ninguém falou em `espera_s`: vazio. Tempo de áudio (blocos)."""
        self.detector.zerar()
        antes: deque[np.ndarray] = deque(maxlen=4)
        gravado: list[np.ndarray] = []
        seguida = esperados = 0
        async for bloco in self.entrada.blocos():
            if not gravado:
                antes.append(bloco)
                esperados += 1
                seguida = seguida + 1 if self.detector.tem_voz(bloco) else 0
                if seguida >= 2:
                    gravado = list(antes)
                    self.detector.zerar()
                    for b in gravado:
                        self.detector.bloco(b)
                elif esperados * BLOCO_S >= espera_s:
                    return np.zeros(0, np.int16)
                continue
            gravado.append(bloco)
            if self.detector.bloco(bloco) or len(gravado) * BLOCO_S >= maximo_s:
                return np.concatenate(gravado)
        return np.concatenate(gravado) if gravado else np.zeros(0, np.int16)

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
        self._retomar = None
        self._mostrar("ocioso")
        async for bloco in self.entrada.blocos():
            if self.estado == "ocioso" and self._calibracao is not None and self._retomar is None:
                vezes, ao_fim = self._calibracao
                self.calibrando = True  # antes de qualquer await: um 2º pedido agora é recusado
                self._calibracao = None
                try:
                    await self._calibrar(vezes, ao_fim)
                except Exception:  # noqa: BLE001 - uma calibração que dá erro não desliga a voz
                    log.exception("a calibração falhou")
                    self._emitir({"tipo": "calibracao", "rodando": False, "erro": "A calibração deu erro; veja o log."})
                self.entrada.descartar()
                continue
            if self.estado == "ocioso" and self._retomar is not None:
                # Você começou a falar por cima dele: a gravação já começou (no vigia) e segue com este bloco.
                # (Só com a conversa aberta há retomada: ver `_vigiar_interrupcao`.)
                gravado, self._retomar = self._retomar, None
                origem = "conversa"
                self.acionar.clear()  # atalho apertado junto com a fala: é a mesma gravação
                self.detector.zerar()
                for b in gravado:
                    self.detector.bloco(b)
                self.estado = "gravando"
                self.escrever("…ouvindo")
                self._mostrar("ouvindo")
            elif self.estado == "ocioso" and not self.para_falar.empty():
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
                if self.em_conversa and self._retomar is None:  # o que tocou no alto-falante não é você
                    self.entrada.descartar()
                # Se não era com ele, a fila fica: um "Hey Vision" dito enquanto ele transcrevia não se perde.
            elif origem == "conversa" and self._pausado():
                pass  # pausou (bandeja ou jogo) enquanto você falava: não vai para ninguém
            else:
                self._mostrar("pensando")
                self._emitir({"tipo": "nivel", "fonte": "mic", "valor": 0.0})
                try:
                    await self._fala_na_conversa(pcm)
                except Exception:  # noqa: BLE001 - um pedido que dá erro não pode desligar a voz (caiu em 01/10)
                    log.exception("erro ao responder; a escuta continua")
                    self.escrever("(deu erro nessa resposta; veja o log)")
                if self._retomar is None:  # cortado por você, o áudio que chegou é a sua fala: fica
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
                    if self._retomar is not None:
                        return ""  # falou por cima da saudação: o próximo bloco retoma a sua fala (ver rodar)
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
        if self.registrar_ativacao:
            self.escrever(f"(ativação) ouvi {texto!r}: {'acordou' if resto is not None else 'não acordou'}")
        if resto is None:
            return  # não era com ele: nada é guardado nem mostrado (a não ser com voz.registrar_ativacao)
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
        if e_interrupcao(texto) == "parar":  # "para", "chega", "pera aí": ele já parou; nada vai ao modelo
            self.agente.cancelar_pendente("voz", "voz")  # "chega" no lugar do "sim" é desistir
            self.pergunta_em = None
            self.historico.append({"felipe": texto, "vision": "", "parou": True})
            self.feitas += 1
            await self._bipe(subindo=True)
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
        await self._depois_de_interrompido()
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
            prontas, pendente[0] = fatiar(pendente[0] + parte, primeira=enviadas[0] == 0)
            for frase in prontas:
                fila.put_nowait(frase)
                enviadas[0] += 1

        enviadas = [0]

        # Zera já (o falador também zera, mas só quando começa a rodar): o corte da resposta anterior não pode
        # parar o modelo desta.
        self.interrompido_por = None
        falador = asyncio.create_task(self._falador(fila, primeira_fala))
        t1 = time.perf_counter()
        r = None
        try:
            # Cortado por voz: o modelo para de escrever na hora, em vez de terminar uma resposta que ninguém
            # vai ouvir enquanto o próximo pedido espera (pendência antiga, 01/10).
            r = await self.agente.responder(texto, canal="voz", sessao="voz", ao_texto=ao_texto,
                                            parar=lambda: self.interrompido_por is not None)
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
        await self._depois_de_interrompido()
        if r is None:
            self.historico.append({"felipe": texto, "vision": FALA_DE_ERRO, "erro": True})
            return
        self.pergunta_em = time.monotonic() if r.aguardando_confirmacao else None
        if r.aguardando_confirmacao and self.interrompido_por is not None:
            # Cortado antes de você ouvir o "Confirma?" inteiro: um "sim" depois não pode valer (revisão do PR 17).
            self.agente.cancelar_pendente("voz", "voz")
            self.pergunta_em = None
        usadas = ", ".join(f["nome"] for f in r.ferramentas)
        ate_falar = (primeira_fala[0] - t1) if primeira_fala else 0.0
        self.escrever(f"{self.agente.nome_assistente}: {r.texto}")
        self.escrever(f"  [ouvir {t_stt:.1f}s · pensar {r.segundos:.1f}s · 1ª palavra {ate_falar:.1f}s"
                      f"{' · ' + usadas if usadas else ''}]")
        self.historico.append({"felipe": texto, "vision": r.texto, "ferramentas": usadas,
                               "stt_s": t_stt, "pensar_s": r.segundos, "ate_falar_s": ate_falar,
                               "interrompido": self.interrompido_por})

    async def _sintetizador(self, fila: asyncio.Queue[str | None],
                            prontas: asyncio.Queue[tuple[int, np.ndarray] | Exception | None],
                            parar: threading.Event) -> None:
        """Gera o áudio das frases na ordem, em pedaços (`(taxa, áudio)`), enquanto o falador toca os anteriores.

        - XTTS: streaming, o 1º pedaço sai em ~0,6 s.
        - Resposta inteira de uma frase curta (luz, timer, "feito"): sai pela reserva (Piper), instantânea.
        - Um erro de síntese vai pela fila e o falador o levanta."""
        loop = asyncio.get_running_loop()

        def entregar(item: tuple[int, np.ndarray]) -> None:
            loop.call_soon_threadsafe(prontas.put_nowait, item)

        def cortado() -> bool:
            return parar.is_set() or self.interrompido_por is not None

        entregues = [0]

        def entregar_contando(item: tuple[int, np.ndarray]) -> None:
            entregues[0] += 1
            entregar(item)

        adiante: list[str | None] = []
        primeira = True
        xtts_falhou = False  # depois de uma falha, o resto da resposta vai direto pela reserva
        try:
            while (frase := adiante.pop(0) if adiante else await fila.get()) is not None:
                if cortado() or not frase.strip():
                    continue  # cortado: não gasta a placa com o que não vai tocar
                voz = self.voz  # uma vez por frase: a tela de ajustes pode trocar a voz no meio
                fonte = voz
                reserva = getattr(voz, "reserva", None)
                if primeira and reserva is not None and len(frase.strip()) <= self.piper_ate_caracteres:
                    if not fila.empty():
                        adiante.append(fila.get_nowait())
                    if adiante == [None]:  # é a resposta inteira: rapidez vale mais que a voz bonita aqui
                        fonte = reserva
                primeira = False
                if xtts_falhou and reserva is not None:
                    fonte = reserva
                entregues[0] = 0
                try:
                    await asyncio.to_thread(self._gerar, fonte, frase, entregar_contando, cortado)
                except Exception:
                    if reserva is None or fonte is reserva:
                        raise
                    # A voz boa falhou: o resto da resposta sai pela robótica, em vez de ficar mudo (ou cair).
                    xtts_falhou = True
                    log.exception("XTTS falhou; o resto desta resposta sai pelo Piper")
                    if entregues[0] == 0:  # se o começo da frase já tocou, refazer repetiria o começo
                        await asyncio.to_thread(self._gerar, reserva, frase, entregar, cortado)
        except Exception as e:  # noqa: BLE001 - repassado ao falador
            prontas.put_nowait(e)
            return
        prontas.put_nowait(None)

    @staticmethod
    def _gerar(voz: Any, frase: str, entregar: Callable[[tuple[int, np.ndarray]], None],
               cortado: Callable[[], bool]) -> None:
        """Na thread: os pedaços do streaming (XTTS) ou a frase inteira (Piper)."""
        if not hasattr(voz, "pedacos"):
            entregar((voz.taxa, voz.sintetizar(frase)))
            return
        gerador = voz.pedacos(frase)
        try:
            for pedaco in gerador:
                if cortado():
                    return
                entregar((voz.taxa, pedaco))
        finally:
            gerador.close()  # solta a trava da placa já, na mesma thread

    async def _falador(self, fila: asyncio.Queue[str | None], primeira_fala: list[float]) -> None:
        self.saida.interromper.clear()
        self.interrompido_por = None
        interrompido = False
        vigia: asyncio.Task | None = None
        fluxo: Any = None
        parar = threading.Event()
        prontas: asyncio.Queue[tuple[int, np.ndarray] | Exception | None] = asyncio.Queue()
        sintetizador = asyncio.create_task(self._sintetizador(fila, prontas, parar))
        # Abrir o fone leva ~0,2 s (MME): já abre enquanto ele pensa e gera o 1º pedaço.
        abrindo = asyncio.create_task(asyncio.to_thread(self.saida.abrir, getattr(self.voz, "taxa", 22050)))
        def foi_cortado() -> bool:
            return self.interrompido_por is not None or (fluxo is not None and self.saida.interromper.is_set())

        async def cortar() -> None:
            """Para o som já: o que estava no buffer do fone não toca (revisão do PR 22)."""
            nonlocal interrompido
            interrompido = True
            parar.set()
            if fluxo is not None:
                await asyncio.to_thread(fluxo.fechar, False)

        async def proxima() -> tuple[int, np.ndarray] | Exception | None:
            # Enquanto espera o próximo pedaço (o modelo ainda escrevendo), o corte também vale na hora.
            while True:
                try:
                    return await asyncio.wait_for(prontas.get(), 0.05)
                except TimeoutError:
                    if not interrompido and foi_cortado():
                        await cortar()

        try:
            while (pronta := await proxima()) is not None:
                if isinstance(pronta, Exception):
                    raise pronta
                taxa, audio = pronta
                if not interrompido and foi_cortado():  # cortado (voz ou atalho) entre um pedaço e outro
                    await cortar()
                if interrompido:
                    continue
                if audio.size == 0:
                    continue
                if fluxo is None:  # um fluxo só para a resposta toda: os pedaços emendam sem buraco
                    fluxo = await asyncio.shield(abrindo)  # cancelado aqui, o `finally` ainda fecha o fluxo
                    # Como no `tocar` antigo: um atalho apertado enquanto ele pensava não corta a resposta.
                    self.saida.interromper.clear()
                if not primeira_fala:
                    primeira_fala.append(time.perf_counter())
                self._mostrar("falando")
                if vigia is None and self.vigia is not None:
                    vigia = asyncio.create_task(self._vigiar_interrupcao())
                ondas = asyncio.create_task(self._emitir_voz(envelope(audio, taxa))) if self.ao_evento else None
                try:
                    if not await asyncio.to_thread(fluxo.escrever, audio, taxa):
                        await cortar()
                        if vigia is not None and self.interrompido_por is None:  # atalho: o vigia para aqui
                            vigia.cancel()
                finally:
                    if ondas is not None:
                        ondas.cancel()
                        self._emitir({"tipo": "nivel", "fonte": "voz", "valor": 0.0})
        finally:
            parar.set()
            sintetizador.cancel()
            await asyncio.gather(sintetizador, return_exceptions=True)
            if fluxo is None:  # nada tocou (resposta vazia, erro, cortado antes): fecha o que abriu
                if abrindo.done():
                    fluxo = None if abrindo.cancelled() or abrindo.exception() else abrindo.result()
                else:
                    # Ainda abrindo (ou o falador foi cancelado): fecha quando terminar, sem esperar aqui.
                    abrindo.add_done_callback(_fechar_quando_abrir)
            if fluxo is not None:
                try:
                    await asyncio.to_thread(fluxo.fechar, not interrompido)
                except Exception:  # noqa: BLE001 - o alto-falante sumiu no meio: a escuta continua
                    log.exception("não consegui fechar a saída de som")
            if vigia is not None:
                vigia.cancel()
                await asyncio.gather(vigia, return_exceptions=True)

    async def _vigiar_interrupcao(self) -> None:
        """Enquanto ele fala: `interromper_apos_s` de voz sua e ele para (como o modo de voz do ChatGPT). O áudio
        já ouvido vai para `_retomar`, e o laço continua a gravação dali: o que você disse vira o próximo pedido."""
        d = self.vigia
        assert d is not None
        d.zerar()
        antes: deque[np.ndarray] = deque(maxlen=4)  # 320 ms antes da voz, para não cortar a 1ª sílaba
        voz: list[np.ndarray] = []
        com_voz = 0
        sem_voz = 0
        blocos = self.entrada.blocos()
        async for bloco in blocos:
            if d.tem_voz(bloco):
                voz.append(bloco)
                com_voz += 1
                sem_voz = 0
            elif voz and sem_voz == 0:  # um bloco sem voz no meio da fala (uma oclusiva, "b", "d"): tolera
                voz.append(bloco)
                sem_voz = 1
                continue
            else:
                antes.extend(voz)  # voz curta demais (tosse): vira contexto, não corte
                antes.append(bloco)
                voz, com_voz, sem_voz = [], 0, 0
                continue
            if com_voz >= self.blocos_para_interromper:
                break
        else:
            return
        self.saida.interromper.set()
        self.interrompido_por = "fala"
        if not self.em_conversa:
            return  # lembrete ou despedida com a conversa fechada: só para de falar, não abre conversa
        # Até o pedido antigo terminar, segue guardando a sua fala aqui (a fila do microfone só cabe 16 s), até
        # você terminar de falar; o resto fica na fila para o laço.
        self._retomar = [*antes, *voz]
        d.zerar()
        for b in self._retomar:
            d.bloco(b)
        async for bloco in blocos:
            self._retomar.append(bloco)
            if d.bloco(bloco):
                return

    async def _depois_de_interrompido(self) -> None:
        """Cortado pela sua voz: nada a fazer aqui além de avisar; o laço continua a gravação (ver `_retomar`)."""
        if self.interrompido_por is not None:
            self.escrever("(interrompido: você começou a falar)")

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

    def pedir_alarme(self, texto: str) -> None:
        """Fim de timer: o alarme toca mesmo no jogo ou com a escuta pausada (foi pedido); a fala, só fora deles."""
        self.para_falar.put_nowait((texto, ALARME))

    async def _falar_de_fora(self, pedido: tuple[str, Any]) -> None:
        texto, voz = pedido
        if voz is ALARME:
            voz = None
            try:
                await asyncio.to_thread(self.saida.tocar, alarme(), 22050)
            except Exception:  # noqa: BLE001
                log.exception("não consegui tocar o alarme")
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
            await self._depois_de_interrompido()
        except Exception:  # noqa: BLE001 - uma fala que falha (síntese, alto-falante) não derruba a escuta
            log.exception("não consegui falar uma frase pedida de fora do laço")
        finally:
            if self.voz is voz and voz is not None:  # se salvaram outra voz enquanto isso, fica a salva
                self.voz = antes
        if voz is None:
            self.pergunta_em = None
        self.pre_fala.clear()
        if self._retomar is None:
            self.entrada.descartar()  # a própria voz não é você falando
        self._mostrar("ocioso")

    def ajustar_silencio(self, silencio_max_s: float) -> None:
        self.blocos_silencio_max = int(silencio_max_s / BLOCO_S)

    async def falar(self, texto: str) -> None:
        """Fala uma frase fora de conversa (aviso de login, lembrete)."""
        voz = self.voz
        audio = await asyncio.to_thread(voz.sintetizar, texto)  # o XTTS leva ~1 s: fora do laço de eventos
        await asyncio.to_thread(self.saida.tocar, audio, voz.taxa)

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
                await self._voz_na_placa(False)
                self.escrever("[modo jogo] modelo fora da VRAM; 'Hey Vision' pausado, o atalho continua valendo.")
            elif not agora and self.jogando:
                self.jogando = False
                if self.estado == "ocioso":
                    self._mostrar("ocioso")
                self.escrever("[modo jogo] fim do jogo; 'Hey Vision' de volta.")
                await self._voz_na_placa(True)  # antes do Qwen: com ele de volta, a VRAM pode não caber
                await self.agente.liberar_modelo()
            elif not agora and getattr(self.voz, "descansando", False):
                await self._voz_na_placa(True)  # a volta falhou (VRAM cheia): tenta de novo a cada checagem
            await asyncio.sleep(a_cada_s)

    async def _voz_na_placa(self, sim: bool) -> None:
        """XTTS: sai da VRAM durante o jogo (fala com o Piper) e volta depois. O Piper não usa a placa."""
        metodo = getattr(self.voz, "acordar" if sim else "descansar", None)
        if metodo is None:
            return
        try:
            await asyncio.to_thread(metodo)
            self._falhas_placa = 0
        except Exception as e:  # noqa: BLE001 - sem a placa, a voz de reserva continua falando
            self._falhas_placa = getattr(self, "_falhas_placa", 0) + 1
            if self._falhas_placa == 1:
                log.exception("não consegui mover a voz %s da placa de vídeo", "para a" if sim else "para fora")
            else:  # a nova tentativa a cada checagem não enche o log de tracebacks (2ª revisão do PR 22)
                log.warning("voz ainda fora da placa (%d tentativas): %s", self._falhas_placa, e)


@contextmanager
def preparar_voz(
    cfg,
    agente: Agente,
    *,
    com_ativacao: bool = True,
    escrever: Callable[[str], None] = print,
    ao_evento: Callable[[dict[str, Any]], None] | None = None,
    voz: Any = None,
) -> Iterator[tuple[LoopVoz, Any]]:
    """Carrega voz, transcrição, ativação e microfone e monta o LoopVoz (usado pelo `vision voz` e pelo núcleo).

    Devolve (laço, atalho). O microfone e o atalho são fechados na saída.
    """
    from vision.voice.audio import Microfone, Saida
    from vision.voice.stt import carregar_transcritor
    from vision.voice.tts import carregar_voz, descrever
    from vision.voice.wake import Atalho

    from vision.voice.comandos import definir_apelidos, ler_apelidos

    t = time.perf_counter()
    definir_apelidos(ler_apelidos(cfg.dados))  # o que a calibração aprendeu (data/ativacao.json)
    voz = voz or carregar_voz(cfg)  # o núcleo carrega antes, numa thread: o XTTS leva 15–30 s
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
    escrever(f"Voz carregada em {time.perf_counter() - t:.1f}s (STT: {stt.nome}, voz: {descrever(voz, cfg)})")
    with Microfone(cfg) as mic:
        laco = LoopVoz(agente, voz, stt, mic, saida, detector, ativacao,
                       flag_dormindo=cfg.dados / "dormindo.flag", escrever=escrever, ao_evento=ao_evento,
                       ativacao_por_texto=com_ativacao and por_texto,
                       silencio_max_s=float(cfg.get("voz.conversa_silencio_max_s", 120)),
                       prazo_confirmacao_s=float(cfg.get("voz.confirmacao_prazo_s", 30)),
                       saudacao=cfg.get("voz.saudacao") or "",
                       despedida=cfg.get("voz.despedida") or "",
                       vigia=(DetectorFala(pasta_oww / "silero_vad.onnx", 500, 60)
                              if cfg.get("voz.interromper_por_voz", True) else None),
                       interromper_apos_s=float(cfg.get("voz.interromper_apos_s", INTERROMPER_APOS_S)),
                       piper_ate_caracteres=int(cfg.get("voz.piper_ate_caracteres", PIPER_ATE_CARACTERES)),
                       registrar_ativacao=bool(cfg.get("voz.registrar_ativacao", False)))
        loop = asyncio.get_running_loop()
        atalho = Atalho(cfg.get("voz.atalho", "ctrl+alt+j"), lambda: loop.call_soon_threadsafe(laco.apertou_atalho))
        for nota in mic.notas:
            escrever(f"  [microfone] {nota}")
        mic.ao_trocar = escrever  # mudo/desligado → o próximo da lista; o preferido voltou → volta (vai ao log)
        if laco.registrar_ativacao:
            escrever("[aviso] voz.registrar_ativacao ligado: o começo do que se fala perto do PC vai para o log. "
                     "Desligue no config.yaml quando terminar.")
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
