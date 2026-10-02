"""O cérebro: recebe uma fala, decide ferramentas, pede confirmação para escrita e responde.

É o mesmo para todos os canais (texto, voz do PC e, no futuro, Alexa). A sessão é por
(canal, sessao): histórico curto + ação pendente de confirmação.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vision import tempo
from vision.brain import confirmacao as classificador, intencao, prompt
from vision.brain.llm import LLM, Interrompido, RespostaLLM
from vision.memory.store import Memorias
from vision.tools.base import ErroFerramenta, Ferramenta, Registro
from vision.voice.comandos import e_despedida  # só texto: frases que fecham a conversa

log = logging.getLogger(__name__)

MAX_VOLTAS = 5
# Grupos cuja escrita não exige que a fala tenha "cara de pedido" daquele tipo (a trava acima): a memória já tem a
# própria regra (guardar fato contado), e "geral" é o que não tem grupo.
SEM_TRAVA_DE_PEDIDO = {"memoria", "geral"}
# Grupos que se cobrem na trava: "pausa a música" é detectado como "pc" (teclas de mídia), mas o modelo pode usar o
# Spotify, e vice-versa (revisão do PR 32).
GRUPOS_IRMAOS = {"musica": {"pc"}, "pc": {"musica"}}
MAX_DIRETAS = 8  # escritas sem confirmação num mesmo pedido
MAX_PALAVRAS_DESPEDIDA_TEXTO = 6  # "pode ficar em standby, Vision" sim; um pedido junto com o standby, não
MODOS_CONFIRMACAO = ("todas", "sensiveis", "nenhuma")
CORTE_TOOL_ANTIGO = 600

PASSADO = {
    "Vou criar": "Criei",
    "Vou mudar": "Mudei",
    "Vou apagar": "Apaguei",
    "Vou esquecer isto:": "Esqueci:",
    "Vou lançar": "Lancei",
    "Vou adicionar": "Adicionei",
    "Vou concluir": "Concluí",
}


@dataclass
class Pendente:
    ferramenta: str
    args: dict[str, Any]
    descricao: str
    id: str = ""


@dataclass
class Sessao:
    turnos: list[list[dict[str, Any]]] = field(default_factory=list)
    pendente: Pendente | None = None
    ultima: float = field(default_factory=time.monotonic)
    trava: asyncio.Lock = field(default_factory=asyncio.Lock)
    atrasada: asyncio.Task | None = None
    nota: str | None = None  # aviso de sistema para a próxima volta (ex.: pendente descartada)
    # Até que turno o texto de terceiros lido (agenda, reunião, nota) ainda está no histórico que o modelo vê.
    externo_ate_turno: int = -1


@dataclass
class Resposta:
    texto: str
    ferramentas: list[dict[str, Any]] = field(default_factory=list)
    aguardando_confirmacao: bool = False
    segundos: float = 0.0
    insistiu: bool = False
    interrompido: bool = False  # o Felipe falou por cima e a geração parou no meio



def _fingiu_que_fez(r: RespostaLLM, escreveu: bool, insistiu: bool, pedido_de_acao: bool) -> bool:
    """A resposta final (já depois da cobrança) diz que fez ou vai fazer, sem nenhuma ferramenta de escrita no
    turno (consultar a lista de luzes e dizer "Apaguei" também é mentira)."""
    if r.chamadas or escreveu or not insistiu or not pedido_de_acao:
        return False
    return intencao.afirmou_sem_fazer(r.texto) or intencao.anunciou_sem_fazer(r.texto)

class Agente:
    def __init__(
        self,
        llm: LLM,
        registro: Registro,
        memorias: Memorias | None = None,
        *,
        nome_usuario: str = "Felipe",
        nome_assistente: str = "Vision",
        perfil: Path | None = None,
        pasta_conversas: Path | None = None,
        turnos_historico: int = 6,
        expira_min: float = 10,
        top_k: int = 3,
        similaridade_minima: float = 0.25,
        relogio: Callable[[], Any] = tempo.agora,
        ao_evento: Callable[[dict[str, Any]], None] | None = None,
        confirmacao: str = "todas",
    ):
        self.llm = llm
        # Quando pedir "Confirma?" (`assistente.confirmacao`):
        #   "todas": toda escrita (menos as com `confirmar=False`, como luzes), e tudo depois de ler texto de fora;
        #   "sensiveis" (pedido do Felipe em 01/10): só o que não dá para desfazer ou mexe com dinheiro
        #     (`sensivel=True`), e guardar memória logo depois de ler texto de fora;
        #   "nenhuma": nada pergunta.
        if confirmacao not in MODOS_CONFIRMACAO:
            raise ValueError(f"assistente.confirmacao deve ser um de {MODOS_CONFIRMACAO}, não {confirmacao!r}")
        self.confirmacao = confirmacao
        self.registro = registro
        self.memorias = memorias
        self.nome = nome_usuario
        self.nome_assistente = nome_assistente
        self.perfil = perfil
        self.pasta_conversas = pasta_conversas
        self.turnos_historico = turnos_historico
        self.expira_s = expira_min * 60
        self.top_k = top_k
        self.sim_min = similaridade_minima
        self.relogio = relogio
        self.sessoes: dict[tuple[str, str], Sessao] = {}
        self._trava_modelo = asyncio.Lock()  # carregar e descarregar nunca se cruzam no Ollama
        self.modelo_seguro = False  # modo jogo: nenhum pré-carregamento até o jogo fechar
        # Quem quiser acompanhar o que acontece (a tela, pelo núcleo) recebe eventos aqui. Ver ui/src/tipos.ts.
        self.ao_evento = ao_evento
        self._ids_pendente = itertools.count(1)
        # Comandos curtos resolvidos sem o modelo (ex.: luzes): texto → (ferramenta, args, resposta) ou None.
        self.atalhos: list[Callable[[str], Awaitable[tuple[str, dict[str, Any], str, bool] | None]]] = []

    def _emitir(self, tipo: str, **campos: Any) -> None:
        if self.ao_evento is None:
            return
        try:
            self.ao_evento({"tipo": tipo, **campos})
        except Exception:  # noqa: BLE001 - a tela nunca pode derrubar a conversa
            log.exception("falha ao emitir evento %s", tipo)

    # ------------------------------------------------------------------ sessão

    def sessao(self, canal: str, sessao: str) -> Sessao:
        chave = (canal, sessao)
        s = self.sessoes.get(chave)
        if s is None or self._expirada(s):
            if s is not None and s.pendente is not None:
                # A pendência morre com a sessão: a tela tira o cartão (senão ele ficava lá para sempre).
                self._emitir("pendente_resolvido", id=s.pendente.id, resultado="cancelada")
            s = self.sessoes[chave] = Sessao()
        s.ultima = time.monotonic()
        return s

    def _expirada(self, s: Sessao) -> bool:
        return time.monotonic() - s.ultima > self.expira_s and not s.trava.locked()

    def cancelar_pendente(self, canal: str, sessao: str) -> None:
        """Descarta a confirmação em aberto dessa sessão (ex.: a conversa por voz foi encerrada) e avisa a tela."""
        s = self.sessoes.get((canal, sessao))
        if s is not None and s.pendente is not None and not s.trava.locked():
            self._emitir("pendente_resolvido", id=s.pendente.id, resultado="cancelada")
            s.pendente = None

    def esquecer_sessao(self, canal: str, sessao: str) -> None:
        self.sessoes.pop((canal, sessao), None)

    # ------------------------------------------------------------------ API

    async def responder(
        self,
        texto: str,
        canal: str = "texto",
        sessao: str = "padrao",
        ao_texto: Callable[[str], None] | None = None,
        pendente_esperada: str | None = None,
        parar: Callable[[], bool] | None = None,
    ) -> Resposta:
        """`pendente_esperada`: o "sim"/"não" é a resposta a ESSA confirmação (clique na tela). Conferido dentro
        da trava: se a voz resolveu ou trocou a pendência enquanto isso, o clique não vale para a nova.

        `parar`: quando ficar verdadeiro (o Felipe falou por cima), a geração para no próximo pedaço de texto ou
        antes da próxima volta do modelo. Uma ferramenta que já está rodando termina; nenhuma nova começa."""
        s = self.sessao(canal, sessao)
        self._emitir("fala_usuario", texto=texto, canal=canal)
        if self.ao_evento is not None:
            original = ao_texto

            def ao_texto(parte: str) -> None:
                self._emitir("resposta_parcial", texto=parte)
                if original:
                    original(parte)

        async with s.trava:
            inicio = time.perf_counter()
            if pendente_esperada is not None and (s.pendente is None or s.pendente.id != pendente_esperada):
                r = Resposta("Essa confirmação já tinha sido resolvida. Nada foi feito agora.")
            else:
                r = (await self._tratar_confirmacao(s, texto, ao_texto, estrito=canal == "voz")
                     if s.pendente is not None else None)
                if r is None:
                    rastro: dict[str, Any] = {}
                    try:
                        r = await self._pensar(s, texto, canal, ao_texto, parar, rastro)
                    except Interrompido:
                        r = self._fechar_interrompido(s, texto, rastro)
                self._registrar(canal, sessao, texto, r)
            r.segundos = time.perf_counter() - inicio
        self._emitir("resposta", texto=r.texto, aguardando_confirmacao=r.aguardando_confirmacao)
        return r

    async def responder_com_prazo(self, texto: str, canal: str, sessao: str, prazo_s: float) -> Resposta:
        """Para a Alexa (limite de ~8 s): se estourar, guarda o resultado para a próxima pergunta."""
        s = self.sessao(canal, sessao)
        if s.atrasada is not None:
            if not s.atrasada.done():
                return Resposta("Ainda estou terminando o pedido anterior. Me pergunta de novo em um instante.")
            tarefa, s.atrasada = s.atrasada, None
            try:
                return tarefa.result()
            except Exception as e:  # noqa: BLE001
                return Resposta(f"O pedido anterior deu erro: {e}")
        tarefa = asyncio.create_task(self.responder(texto, canal, sessao))
        try:
            return await asyncio.wait_for(asyncio.shield(tarefa), timeout=prazo_s)
        except TimeoutError:
            s.atrasada = tarefa
            return Resposta("Ainda estou buscando isso. Me pergunta de novo em um instante.")

    async def resolver_pendente(self, pendente_id: str, sim: bool) -> Resposta | None:
        """Confirmar/Cancelar vindo da tela: responde "sim"/"não" na sessão que tem essa pendência."""
        for (canal, sessao), s in list(self.sessoes.items()):
            if s.pendente is not None and s.pendente.id == pendente_id:
                if self._expirada(s):
                    # Sem isto, o "sim" ia sozinho para o modelo numa sessão nova, sem a pendência.
                    self.sessao(canal, sessao)  # troca a sessão e avisa a tela que o cartão caiu
                    r = Resposta("Essa confirmação expirou. Se ainda quiser, me peça de novo.")
                    self._emitir("resposta", texto=r.texto, aguardando_confirmacao=False)
                    return r
                return await self.responder("sim" if sim else "não", canal, sessao, pendente_esperada=pendente_id)
        return None

    def canal_da_pendente(self, pendente_id: str) -> str | None:
        """De que canal é essa confirmação ("voz", "texto"...): confirmada pela tela, a resposta da voz é falada."""
        for (canal, _sessao), s in self.sessoes.items():
            if s.pendente is not None and s.pendente.id == pendente_id:
                return canal
        return None

    # ------------------------------------------------------------------ confirmação

    async def _tratar_confirmacao(
        self, s: Sessao, texto: str, ao_texto: Callable[[str], None] | None, estrito: bool = False
    ) -> Resposta | None:
        p = s.pendente
        assert p is not None
        f = self.registro.get(p.ferramenta)
        if f is not None and (f.sempre_confirmar or f.sensivel):
            estrito = True  # comando no PC, desligar, apagar, dinheiro: só "sim" explícito, em qualquer canal
        tipo = classificador.classificar(texto, estrito)
        if tipo == "outro":
            # O Felipe corrigiu ou mudou de assunto: o modelo decide de novo. Sem esta nota, o modelo
            # achava que o evento já existia e tentava "alterar" (visto na avaliação de 30/09).
            s.pendente = None
            self._emitir("pendente_resolvido", id=p.id, resultado="cancelada")
            s.nota = (
                f"A ação '{p.descricao}' NÃO foi executada e foi descartada; nada foi criado nem alterado. "
                f"Se o {self.nome} está corrigindo algum dado, chame {p.ferramenta} de novo com os dados corrigidos."
            )
            return None
        s.pendente = None
        if tipo == "nao":
            resposta = "Beleza, cancelei."
            registro = [{"nome": p.ferramenta, "args": p.args, "ok": None, "cancelada": True}]
            self._emitir("pendente_resolvido", id=p.id, resultado="cancelada")
        else:
            ok, resultado, dados = await self.registro.rodar_com_dados(p.ferramenta, p.args)
            registro = [{"nome": p.ferramenta, "args": p.args, "ok": ok, "resultado": resultado[:300]}]
            self._emitir("ferramenta_fim", nome=p.ferramenta, ok=ok, args=p.args, dados=dados)
            # "evento" leva o resultado real (id de verdade do Google) para a tela trocar o cartão provisório.
            self._emitir("pendente_resolvido", id=p.id, resultado="executada" if ok else "cancelada",
                         evento=dados if ok and isinstance(dados, dict) and dados.get("inicio") else None)
            if ok and f is not None and f.devolve_saida:
                resposta = resultado if len(resultado) <= 600 else resultado[:600] + " [...]"
            elif ok:
                resposta = _no_passado(p.descricao)
                if "Atenção:" in resultado:
                    resposta += " " + resultado[resultado.index("Atenção:"):]
            else:
                resposta = f"Não consegui: {resultado}"
        if ao_texto:
            ao_texto(resposta)
        s.turnos.append([{"role": "user", "content": texto}, {"role": "assistant", "content": resposta}])
        return Resposta(resposta, registro)

    # ------------------------------------------------------------------ pensar

    async def _contexto(self, texto: str, canal: str) -> str:
        perfil = ""
        if self.perfil and self.perfil.exists():
            perfil = self.perfil.read_text(encoding="utf-8")
        memorias = ""
        if self.memorias is not None and self.memorias.total():
            try:
                achadas = await self.memorias.buscar(texto, k=self.top_k, minimo=self.sim_min)
                memorias = Memorias.formatar(achadas)
            except Exception as e:  # noqa: BLE001 - memória fora do ar não pode travar a conversa
                log.warning("memória indisponível: %s", e)
        return prompt.montar(self.nome, canal, self.relogio(), perfil, memorias, set(self.registro.ferramentas),
                             self.nome_assistente, confirmacao=self.confirmacao)

    def _historico(self, s: Sessao) -> list[dict[str, Any]]:
        turnos = s.turnos[-self.turnos_historico :]
        msgs: list[dict[str, Any]] = []
        for i, turno in enumerate(turnos):
            antigo = i < len(turnos) - 1
            for m in turno:
                if antigo and m.get("role") == "tool" and len(m.get("content", "")) > CORTE_TOOL_ANTIGO:
                    m = {**m, "content": m["content"][:CORTE_TOOL_ANTIGO] + " [...]"}
                msgs.append(m)
        return msgs

    def _fechar_interrompido(self, s: Sessao, texto: str, rastro: dict[str, Any]) -> Resposta:
        """O turno cortado entra no histórico como foi: as ferramentas que já rodaram (o modelo não repete nem
        nega o que fez) e o que ele chegou a dizer, marcado [interrompido]. Nenhuma pendência nasce de um corte."""
        if s.pendente is not None and s.pendente.id == rastro.get("pendente_nova"):
            s.pendente = None  # defensivo: o corte é antes de criar a pendência, mas não pode sobrar uma não ouvida
            self._emitir("pendente_resolvido", id=rastro["pendente_nova"], resultado="cancelada")
        turno = rastro.get("turno") or [{"role": "user", "content": texto}]
        usadas = rastro.get("usadas") or []
        dito = "".join(rastro.get("parcial") or []).strip()
        turno.append({"role": "assistant", "content": (dito + " [interrompido]").strip()})
        s.turnos.append(turno)
        if any((f := self.registro.get(u["nome"])) is not None and f.conteudo_externo for u in usadas):
            s.externo_ate_turno = len(s.turnos) - 1 + self.turnos_historico
        return Resposta(dito, usadas, s.pendente is not None, interrompido=True)

    async def _pensar(self, s: Sessao, texto: str, canal: str, ao_texto: Callable[[str], None] | None,
                      parar: Callable[[], bool] | None = None, rastro: dict[str, Any] | None = None) -> Resposta:
        # No modo "todas", depois de ler texto de fora quem decide é o caminho normal. Luz nunca é sensível.
        if canal != "voz" and len(texto.split()) <= MAX_PALAVRAS_DESPEDIDA_TEXTO and e_despedida(texto):
            # "Entra em standby" digitado na janela: na voz o laço já fecha a conversa; aqui o modelo achava que era
            # para suspender o PC (02/10). Só frase curta: "apaga a luz e entra em standby" vai ao modelo, senão o
            # pedido da luz se perdia (revisão do PR 42); a descrição do pc_energia segura o standby nele.
            resposta = "Certo, fico em standby. É só me chamar."
            if s.nota:  # a frase descartou um "Confirma?" neste turno: a nota não fica para o próximo
                s.nota = None
                resposta = "Certo, cancelei o que estava pendente e fico em standby. É só me chamar."
            s.turnos.append([{"role": "user", "content": texto}, {"role": "assistant", "content": resposta}])
            if ao_texto:
                ao_texto(resposta)
            return Resposta(resposta, [])
        if self.confirmacao != "todas" or (not self._ainda_tem_externo(s) and not s.nota):
            for atalho in self.atalhos:
                feito = await atalho(texto)
                if feito is not None:
                    return self._registrar_atalho(s, texto, feito, ao_texto)
        sistema = await self._contexto(texto, canal)
        turno: list[dict[str, Any]] = [{"role": "user", "content": texto}]
        nota = [{"role": "system", "content": s.nota}] if s.nota else []
        s.nota = None
        mensagens = [{"role": "system", "content": sistema}, *self._historico(s), *nota, *turno]
        ferramentas = self.registro.para_ollama()
        usadas: list[dict[str, Any]] = []
        parcial: list[str] = []
        if rastro is not None:
            rastro.update(turno=turno, usadas=usadas, parcial=parcial)

        def cortavel(parte: str) -> None:
            """Só o texto que o modelo está transmitindo pode ser cortado (a conexão fecha e ele para)."""
            if parar is not None and parar():
                raise Interrompido
            parcial.append(parte)
            if ao_texto:
                ao_texto(parte)
        leu_de_fora = self.confirmacao != "nenhuma" and self._ainda_tem_externo(s)
        diretas = 0
        grupos = [g for g in intencao.detectar(texto) if self.registro.nomes_do_grupo(g)]
        # Que tipo de ação foi PEDIDA (nesta fala ou na anterior, para "e a da sala também?"). Visto em 02/10:
        # "estou cansado" fez o modelo acender as luzes a 40% por conta própria.
        pedidos = set(intencao.detectar(texto))
        if s.turnos:
            anterior = next((m["content"] for m in s.turnos[-1] if m.get("role") == "user"), "")
            pedidos |= set(intencao.detectar(anterior))
        pedidos |= {irmao for g in list(pedidos) for irmao in GRUPOS_IRMAOS.get(g, ())}
        # Só um pedido de ação (não pergunta, nem resposta a uma pendência que acabou de ser descartada) pode ter
        # um "Feito." de mentira: "já marquei a prova?" → "Marquei sim, dia 5" é resposta legítima.
        pedido_de_acao = bool(grupos) and not texto.strip().endswith("?") and not nota
        # "pc" só obriga ferramenta em pedido, não em pergunta ("como eu faço para abrir uma conta?").
        forcar = [g for g in grupos if g not in intencao.SEM_INSISTIR or not texto.strip().endswith("?")]
        insistiu = False
        final = ""

        for volta in range(MAX_VOLTAS):
            if parar is not None and parar():
                raise Interrompido  # cortado entre uma volta e outra: as ferramentas pedidas depois nem rodam
            transmitir = cortavel if volta > 0 and (ao_texto or parar) else None  # a 1ª volta decide ferramentas
            r: RespostaLLM = await self.llm.conversar(mensagens, ferramentas, transmitir)
            if parar is not None and parar():
                raise Interrompido  # ele falou enquanto o modelo decidia: o que foi pedido agora não roda
            transmitido = transmitir is not None

            escreveu = any(self._mudou_algo(u) for u in usadas)
            afirmou = pedido_de_acao and not r.chamadas and not escreveu and intencao.afirmou_sem_fazer(r.texto)
            anunciou = not r.chamadas and (intencao.anunciou_sem_fazer(r.texto) or afirmou)
            if not r.chamadas and not insistiu and ((volta == 0 and forcar) or anunciou):
                insistiu = True
                if anunciou:
                    # O modelo disse "vou marcar..." e parou: mostra a fala dele e cobra a ação.
                    extra = [{"role": "assistant", "content": r.texto}]
                    puxao = ("ATENÇÃO: você disse que ia fazer ou verificar algo, mas não chamou nenhuma ferramenta. "
                             "Chame a ferramenta certa agora; "
                             + (f"o sistema é quem pede a confirmação ao {self.nome}. "
                                if self.confirmacao != "nenhuma" else "")
                             + "Nunca escreva 'Confirma?' você mesmo.")
                else:
                    extra = []
                    nomes = ", ".join(n for g in forcar for n in self.registro.nomes_do_grupo(g))
                    puxao = (f"ATENÇÃO: para responder isso você PRECISA chamar uma ferramenta ({nomes}). "
                             "Chame a ferramenta agora. Não responda de cabeça.")
                r = await self.llm.conversar([*mensagens, *extra, {"role": "system", "content": puxao}], ferramentas, None)
                transmitido = False

            escreveu = any(self._mudou_algo(u) for u in usadas)
            if _fingiu_que_fez(r, escreveu, insistiu, pedido_de_acao):
                # Cobrado e mesmo assim nada foi chamado: não deixa passar "Feito." de mentira.
                log.warning("o modelo disse que fez/ia fazer sem chamar ferramenta (%d caracteres)", len(r.texto))
                r = RespostaLLM(texto="Não fiz nada ainda: não consegui executar esse pedido. Pode repetir?")
                transmitido = False
            if not r.chamadas:
                final = r.texto.strip()
                if not transmitido and ao_texto and final:
                    ao_texto(final)
                break

            assistente = {
                "role": "assistant",
                "content": r.texto,
                "tool_calls": [{"function": {"name": c.nome, "arguments": c.args}} for c in r.chamadas],
            }
            mensagens.append(assistente)
            turno.append(assistente)
            nova_pendente: Pendente | None = None
            for c in r.chamadas:
                f = self.registro.get(c.nome)
                if f is not None:
                    c.args = f.normalizar_args(c.args)
                self._emitir("ferramenta_inicio", nome=c.nome, args=c.args)
                pergunta = f is not None and self._pede_confirmacao(f, leu_de_fora)
                if f is not None and f.escrita and not pergunta and f.grupo not in pedidos | SEM_TRAVA_DE_PEDIDO:
                    pergunta = True  # ação que ninguém pediu: vira "Confirma?" em vez de acontecer sozinha
                limite = f is not None and f.escrita and not pergunta and diretas >= MAX_DIRETAS
                if limite and self.confirmacao == "todas":
                    pergunta, limite = True, False  # modo antigo: o excesso vira pendência
                if limite:
                    # O excesso não vira "Confirma?": só não é feito (trava contra laço do modelo).
                    ok, resultado = False, f"Limite de {MAX_DIRETAS} ações por pedido; esta não foi feita."
                elif f is not None and f.escrita and not pergunta:
                    diretas += 1
                    ok, resultado, dados = await self.registro.rodar_com_dados(c.nome, c.args)
                    self._emitir("ferramenta_fim", nome=c.nome, ok=ok, args=c.args, dados=dados)
                elif pergunta:
                    if nova_pendente is not None:
                        ok, resultado = False, "Só uma alteração por vez; esta foi ignorada."
                    else:
                        try:
                            descricao = await f.descrever(c.args) if f.descrever else _descricao_padrao(f, c.args)
                            nova_pendente = Pendente(c.nome, c.args, descricao, f"p{next(self._ids_pendente)}")
                            ok, resultado = True, f"AGUARDANDO CONFIRMAÇÃO DO {self.nome.upper()}: {descricao}"
                        except ErroFerramenta as e:
                            ok, resultado = False, f"Não dá para fazer ainda: {e}"
                        except Exception as e:  # noqa: BLE001
                            ok, resultado = False, f"Erro preparando {c.nome}: {type(e).__name__}: {e}"
                else:
                    ok, resultado, dados = await self.registro.rodar_com_dados(c.nome, c.args)
                    self._emitir("ferramenta_fim", nome=c.nome, ok=ok, args=c.args, dados=dados)
                    if f is not None and f.conteudo_externo and self.confirmacao != "nenhuma":
                        leu_de_fora = True
                usadas.append({"nome": c.nome, "args": c.args, "ok": ok, "resultado": resultado[:300]})
                msg_tool = {"role": "tool", "content": resultado, "tool_name": c.nome}
                mensagens.append(msg_tool)
                turno.append(msg_tool)

            if nova_pendente is not None:
                if rastro is not None:
                    rastro["pendente_nova"] = nova_pendente.id
                s.pendente = nova_pendente
                self._emitir("pendente", pendente=self._pendente_para_tela(nova_pendente))
                final = f"{nova_pendente.descricao} Confirma?"
                if ao_texto:
                    ao_texto(final)
                break
        else:
            final = final or "Me enrolei aqui. Pode repetir de outro jeito?"
            if ao_texto:
                ao_texto(final)

        if not final:
            final = "Hmm, não consegui formular uma resposta. Pode repetir?"
            if ao_texto:
                ao_texto(final)
        turno.append({"role": "assistant", "content": final})
        s.turnos.append(turno)
        if any((f := self.registro.get(u["nome"])) is not None and f.conteudo_externo for u in usadas):
            # O resultado fica no histórico por `turnos_historico` turnos: a trava vale esse tempo todo.
            s.externo_ate_turno = len(s.turnos) - 1 + self.turnos_historico
        return Resposta(final, usadas, s.pendente is not None, insistiu=insistiu)

    def _mudou_algo(self, usada: dict[str, Any]) -> bool:
        """Chamada que deu certo e mudou algo: escrita, ou guardar memória (que não é escrita, mas grava)."""
        f = self.registro.get(usada["nome"])
        return bool(usada["ok"]) and f is not None and (f.escrita or f.confirmar_se_externo)

    def _pede_confirmacao(self, f: Ferramenta, leu_de_fora: bool) -> bool:
        """Essa chamada vira "Confirma?" em vez de rodar? (ver `confirmacao` no construtor)"""
        if f.sempre_confirmar:
            return True
        if self.confirmacao == "nenhuma":
            return False
        if self.confirmacao == "sensiveis":
            return (f.escrita and f.sensivel) or (f.confirmar_se_externo and leu_de_fora)
        if f.escrita:
            return f.confirmar or leu_de_fora
        return f.confirmar_se_externo and leu_de_fora

    @staticmethod
    def _ainda_tem_externo(s: Sessao) -> bool:
        return len(s.turnos) <= s.externo_ate_turno

    def _registrar_atalho(self, s: Sessao, texto: str, feito: tuple[str, dict[str, Any], str, bool],
                          ao_texto: Callable[[str], None] | None) -> Resposta:
        """Um comando resolvido sem o modelo entra no histórico como se ele tivesse chamado a ferramenta:
        assim o modelo aprende o jeito certo pelo exemplo, em vez de imitar um "Feito." solto."""
        nome, args, resposta, ok = feito
        self._emitir("ferramenta_inicio", nome=nome, args=args)
        self._emitir("ferramenta_fim", nome=nome, ok=ok, args=args, dados=None)
        s.turnos.append([
            {"role": "user", "content": texto},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": nome, "arguments": args}}]},
            {"role": "tool", "content": resposta, "tool_name": nome},
            {"role": "assistant", "content": resposta},
        ])
        if ao_texto:
            ao_texto(resposta)
        return Resposta(resposta, [{"nome": nome, "args": args, "ok": ok, "resultado": resposta[:300]}])

    # ------------------------------------------------------------------ utilidades

    def _pendente_para_tela(self, p: Pendente) -> dict[str, Any]:
        tela: dict[str, Any] = {"id": p.id, "ferramenta": p.ferramenta, "descricao": p.descricao}
        f = self.registro.get(p.ferramenta)
        if f is not None and f.previa is not None:
            try:
                evento = f.previa(p.args)
            except Exception:  # noqa: BLE001 - sem prévia a tela só mostra a descrição
                log.exception("prévia de %s falhou", p.ferramenta)
                evento = None
            if evento:
                tela["evento"] = {"id": p.id, **evento}  # criar: id provisório; apagar: a prévia traz o id real
        return tela

    async def descarregar(self) -> None:
        """Modo jogo: tira o modelo e segura os pré-carregamentos até `liberar_modelo()`."""
        self.modelo_seguro = True
        async with self._trava_modelo:  # um carregar em andamento termina antes; o descarregar vem por último
            await self.llm.descarregar()

    async def liberar_modelo(self) -> None:
        """Fim do jogo: o modelo pode voltar."""
        self.modelo_seguro = False
        await self.carregar()

    async def carregar(self) -> None:
        """Deixa o modelo pronto (ao subir, ao sair do jogo, ao ouvir "Hey Vision"). Durante o jogo não faz
        nada (uma pergunta pelo atalho carrega o modelo por conta própria). Falha só vai para o log."""
        if self.modelo_seguro:
            return
        async with self._trava_modelo:
            if self.modelo_seguro:
                return
            try:
                await self.llm.carregar()
            except Exception:  # noqa: BLE001
                log.warning("não consegui pré-carregar o modelo", exc_info=True)

    def _registrar(self, canal: str, sessao: str, texto: str, r: Resposta) -> None:
        if self.pasta_conversas is None:
            return
        self.pasta_conversas.mkdir(parents=True, exist_ok=True)
        agora = self.relogio()
        linha = {
            "quando": agora.isoformat(timespec="seconds"),
            "canal": canal,
            "sessao": sessao,
            "felipe": texto,
            "vision": r.texto,
            "ferramentas": r.ferramentas,
            "insistiu": r.insistiu,
            "segundos": round(r.segundos, 2),
            "modelo": getattr(self.llm, "modelo", "?"),
        }
        arquivo = self.pasta_conversas / f"{agora:%Y-%m-%d}.jsonl"
        with arquivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")


def _no_passado(descricao: str) -> str:
    for futuro, passado in PASSADO.items():
        if descricao.startswith(futuro):
            return "Feito. " + passado + descricao[len(futuro) :]
    return "Feito."


def _descricao_padrao(f: Ferramenta, args: dict[str, Any]) -> str:
    """Para quem não tem `descrever`: a descrição da própria ferramenta, falável ("Vou executar musica_controlar"
    soava como código; revisão do PR 32)."""
    detalhe = ", ".join(f"{v}" for v in args.values() if isinstance(v, str | int | float) and str(v).strip())[:80]
    acao = f.descricao.split(".")[0].strip().rstrip(":") or f.nome.replace("_", " ")
    return f"Vou fazer isto: {acao[:1].lower()}{acao[1:]}" + (f" ({detalhe})." if detalhe else ".")
