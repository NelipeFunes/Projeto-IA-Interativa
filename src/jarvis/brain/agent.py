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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis import tempo
from jarvis.brain import confirmacao, intencao, prompt
from jarvis.brain.llm import LLM, RespostaLLM
from jarvis.memory.store import Memorias
from jarvis.tools.base import ErroFerramenta, Registro

log = logging.getLogger(__name__)

MAX_VOLTAS = 5
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


@dataclass
class Resposta:
    texto: str
    ferramentas: list[dict[str, Any]] = field(default_factory=list)
    aguardando_confirmacao: bool = False
    segundos: float = 0.0
    insistiu: bool = False


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
    ):
        self.llm = llm
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
        # Quem quiser acompanhar o que acontece (a tela, pelo núcleo) recebe eventos aqui. Ver ui/src/tipos.ts.
        self.ao_evento = ao_evento
        self._ids_pendente = itertools.count(1)

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
    ) -> Resposta:
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
            r = await self._tratar_confirmacao(s, texto, ao_texto) if s.pendente is not None else None
            if r is None:
                r = await self._pensar(s, texto, canal, ao_texto)
            r.segundos = time.perf_counter() - inicio
            self._registrar(canal, sessao, texto, r)
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
                return await self.responder("sim" if sim else "não", canal, sessao)
        return None

    # ------------------------------------------------------------------ confirmação

    async def _tratar_confirmacao(
        self, s: Sessao, texto: str, ao_texto: Callable[[str], None] | None
    ) -> Resposta | None:
        p = s.pendente
        assert p is not None
        tipo = confirmacao.classificar(texto)
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
            if ok:
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
                             self.nome_assistente)

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

    async def _pensar(self, s: Sessao, texto: str, canal: str, ao_texto: Callable[[str], None] | None) -> Resposta:
        sistema = await self._contexto(texto, canal)
        turno: list[dict[str, Any]] = [{"role": "user", "content": texto}]
        nota = [{"role": "system", "content": s.nota}] if s.nota else []
        s.nota = None
        mensagens = [{"role": "system", "content": sistema}, *self._historico(s), *nota, *turno]
        ferramentas = self.registro.para_ollama()
        usadas: list[dict[str, Any]] = []
        grupos = [g for g in intencao.detectar(texto) if self.registro.nomes_do_grupo(g)]
        insistiu = False
        final = ""

        for volta in range(MAX_VOLTAS):
            transmitir = ao_texto if volta > 0 else None  # a 1ª volta decide ferramentas; não fala antes
            r: RespostaLLM = await self.llm.conversar(mensagens, ferramentas, transmitir)
            transmitido = transmitir is not None

            anunciou = not r.chamadas and intencao.anunciou_sem_fazer(r.texto)
            if not r.chamadas and not insistiu and ((volta == 0 and grupos) or anunciou):
                insistiu = True
                if anunciou:
                    # O modelo disse "vou marcar..." e parou: mostra a fala dele e cobra a ação.
                    extra = [{"role": "assistant", "content": r.texto}]
                    puxao = ("ATENÇÃO: você disse que ia fazer ou verificar algo, mas não chamou nenhuma ferramenta. "
                             "Chame a ferramenta certa agora; o sistema é quem pede a confirmação ao "
                             f"{self.nome}. Nunca escreva 'Confirma?' você mesmo.")
                else:
                    extra = []
                    nomes = ", ".join(n for g in grupos for n in self.registro.nomes_do_grupo(g))
                    puxao = (f"ATENÇÃO: para responder isso você PRECISA chamar uma ferramenta ({nomes}). "
                             "Chame a ferramenta agora. Não responda de cabeça.")
                r = await self.llm.conversar([*mensagens, *extra, {"role": "system", "content": puxao}], ferramentas, None)
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
                if f is not None and f.escrita:
                    if nova_pendente is not None:
                        ok, resultado = False, "Só uma alteração por vez; esta foi ignorada."
                    else:
                        try:
                            descricao = await f.descrever(c.args) if f.descrever else f"Vou executar {c.nome}."
                            nova_pendente = Pendente(c.nome, c.args, descricao, f"p{next(self._ids_pendente)}")
                            ok, resultado = True, f"AGUARDANDO CONFIRMAÇÃO DO {self.nome.upper()}: {descricao}"
                        except ErroFerramenta as e:
                            ok, resultado = False, f"Não dá para fazer ainda: {e}"
                        except Exception as e:  # noqa: BLE001
                            ok, resultado = False, f"Erro preparando {c.nome}: {type(e).__name__}: {e}"
                else:
                    ok, resultado, dados = await self.registro.rodar_com_dados(c.nome, c.args)
                    self._emitir("ferramenta_fim", nome=c.nome, ok=ok, args=c.args, dados=dados)
                usadas.append({"nome": c.nome, "args": c.args, "ok": ok, "resultado": resultado[:300]})
                msg_tool = {"role": "tool", "content": resultado, "tool_name": c.nome}
                mensagens.append(msg_tool)
                turno.append(msg_tool)

            if nova_pendente is not None:
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
        return Resposta(final, usadas, s.pendente is not None, insistiu=insistiu)

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
        await self.llm.descarregar()

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
            "jarvis": r.texto,
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
