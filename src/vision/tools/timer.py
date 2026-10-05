"""Ferramentas de timer e alarme (vision/timers.py faz a contagem; o núcleo toca e avisa no fim)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from vision import tempo
from vision.timers import Timer, Timers, descrever_duracao, para_dados
from vision.tools.base import PRAZO_PC_S, ComDados, ErroFerramenta, Ferramenta, Registro, esquema, numero, texto
from vision.tools.pc import normalizar

MAXIMO_S = 24 * 3600


def _numero(args: dict[str, Any], chave: str) -> float:
    valor = args.get(chave)
    if valor in (None, ""):
        return 0.0
    try:
        return float(str(valor).replace(",", "."))
    except ValueError as e:
        raise ErroFerramenta(f"'{chave}' tem que ser um número.") from e


def _proxima(hora: str, agora: datetime) -> datetime:
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:h](\d{2}))?\s*h?\s*", str(hora))
    if not m or int(m.group(1)) > 23 or int(m.group(2) or 0) > 59:
        raise ErroFerramenta("Hora no formato HH:MM (24h).")
    alvo = agora.replace(hour=int(m.group(1)), minute=int(m.group(2) or 0), second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


class Temporizador:
    def __init__(self, timers: Timers, relogio=tempo.agora, acoes: dict[str, str] | None = None):
        """`acoes`: o que o timer pode fazer no fim, além de avisar ({"desligar_tv": "desligar a TV"}); a lista
        fechada vem da montagem (só as ferramentas que estão ligadas)."""
        self.timers = timers
        self.relogio = relogio
        self.acoes = acoes or {}

    def _acao(self, args: dict[str, Any]) -> str:
        acao = str(args.get("ao_acabar") or "").strip()
        if acao in ("", "nenhuma", "avisar"):
            return ""
        if acao not in self.acoes:
            raise ErroFerramenta("No fim do timer só sei: " + ", ".join(self.acoes.values() or ["avisar"]) + ".")
        return acao

    async def criar(self, args: dict[str, Any]) -> str:
        acao = self._acao(args)
        depois = f" No fim, vou {self.acoes[acao]}." if acao else ""
        nome = str(args.get("nome") or "").strip()[:60]
        if re.match(r"^(timer|alarme|cronometro|temporizador)\b", normalizar(nome)):
            nome = ""  # "Timer de 1 minuto" não é nome: o aviso ficaria "o timer 'Timer de 1 minuto' de 1 minuto"
        if args.get("hora"):
            agora = self.relogio()
            alvo = _proxima(str(args["hora"]), agora)
            segundos = (alvo - agora).total_seconds()
            rotulo = f"das {alvo:%H:%M}" + ("" if alvo.date() == agora.date() else " (amanhã)")
            try:
                t = self.timers.criar(segundos, rotulo, nome, alarme=True, acao=acao)
            except ValueError as e:
                raise ErroFerramenta(str(e)) from e
            return ComDados(f"Alarme marcado para as {alvo:%H:%M}" + ("" if alvo.date() == agora.date() else
                            " de amanhã") + "." + depois, para_dados(t, segundos))
        segundos = _numero(args, "horas") * 3600 + _numero(args, "minutos") * 60 + _numero(args, "segundos")
        if segundos <= 0:
            raise ErroFerramenta("Quanto tempo? Diga minutos, segundos ou horas (ou a hora do alarme).")
        if segundos > MAXIMO_S:
            raise ErroFerramenta("Timer de no máximo 24 horas.")
        rotulo = descrever_duracao(segundos)
        try:
            t = self.timers.criar(segundos, rotulo, nome, acao=acao)
        except ValueError as e:
            raise ErroFerramenta(str(e)) from e
        return ComDados(f"Timer de {rotulo} ligado" + (f" ({nome})" if nome else "") + "." + depois,
                        para_dados(t, segundos))

    async def descrever_criar(self, args: dict[str, Any]) -> str:
        quando = f"às {args['hora']}" if args.get("hora") else "de " + descrever_duracao(
            _numero(args, "horas") * 3600 + _numero(args, "minutos") * 60 + _numero(args, "segundos"))
        nome = str(args.get("nome") or "").strip()[:60]
        acao = self._acao(args)
        return f"Vou ligar um timer {quando}" + (f" com o aviso: {nome}." if nome else ".")             + (f" No fim, vou {self.acoes[acao]}." if acao else "")

    async def listar(self, _args: dict[str, Any]) -> str:
        timers = self.timers.listar()
        if not timers:
            return "Nenhum timer ligado."
        linhas = []
        for t in timers:
            quem = (f"Alarme {t.rotulo}" if t.alarme else f"Timer de {t.rotulo}") + (f" ({t.nome})" if t.nome else "")
            if t.acao:
                quem += f", para {self.acoes.get(t.acao, t.acao)}"
            linhas.append(f"{quem}: faltam {descrever_duracao(self.timers.falta(t))}.")
        return "\n".join(linhas)

    async def cancelar(self, args: dict[str, Any]) -> str:
        timers = self.timers.listar()
        if not timers:
            return "Não tem nenhum timer ligado."
        pedido = normalizar(str(args.get("nome") or ""))
        if pedido in {"todos", "todas", "tudo"}:
            for t in timers:
                self.timers.cancelar(t)
            return f"Cancelei {len(timers)} timer(s)."
        if pedido:
            escolhidos = [t for t in timers if pedido in normalizar(f"{t.nome} {t.rotulo}")]
        else:
            escolhidos = timers if len(timers) == 1 else []
        if len(escolhidos) != 1:
            nomes = "; ".join((t.nome or t.rotulo) for t in timers)
            raise ErroFerramenta(f"Qual timer? Tem {len(timers)}: {nomes}.")
        t = escolhidos[0]
        self.timers.cancelar(t)
        return f"Cancelei o {'alarme' if t.alarme else 'timer'} {t.nome or t.rotulo}."

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """"timer de 5 minutos", "cancela o timer": sem passar pelo modelo. None = o modelo decide."""
        cmd = comando_de_timer(frase)
        if cmd is None:
            return None
        nome, args = cmd
        if nome == "timer_cancelar":
            timers = self.timers.listar()
            if len(timers) != 1:
                return None  # mais de um (ou nenhum): o modelo pergunta qual ou explica
            # "para o alarme" logo depois de um alarme tocar não cancela o timer do forno (revisão do PR 20)
            if "alarme" in normalizar(frase).split() and not timers[0].alarme:
                return None
        executar = self.criar if nome == "timer_criar" else self.cancelar
        try:
            return nome, args, str(await executar(args)), True
        except ErroFerramenta as e:
            return nome, args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        props = {"minutos": numero("Minutos"), "segundos": numero("Segundos"), "horas": numero("Horas"),
                 "hora": texto("Para alarme: HH:MM (24h)"),
                 "nome": texto("Para que é (ex.: 'forno', 'ligar pro banco'); vazio se ele não disse")}
        descricao = ("Liga um timer (minutos/segundos/horas) ou um alarme num horário ('me avisa às 15h'). No fim o "
                     "Vision toca um alarme e avisa.")
        if self.acoes:
            props["ao_acabar"] = {"type": "string", "enum": list(self.acoes),
                                  "description": "Ação no fim, em vez do alarme (ex.: 'desliga a TV em 30 minutos'); "
                                                 "omita para só avisar"}
            descricao += " Também faz uma ação no fim: " + ", ".join(self.acoes.values()) + "."
        return [
            Ferramenta("timer_criar", descricao, esquema([], **props),
                       self.criar, escrita=True, grupo="timer", confirmar=False, confirmar_se_externo=True,
                       descrever=self.descrever_criar,
                       prazo_s=PRAZO_PC_S),
            Ferramenta("timer_listar", "Timers e alarmes ligados e quanto falta.", esquema([]), self.listar,
                       grupo="timer"),
            Ferramenta("timer_cancelar", "Cancela um timer ou alarme (pelo nome, ou 'todos').",
                       esquema([], nome=texto("Nome ou duração do timer; vazio se só tem um")),
                       self.cancelar, escrita=True, grupo="timer", confirmar=False, prazo_s=PRAZO_PC_S),
        ]


async def fazer_acao(t: Timer, registro: Registro, acoes: dict[str, tuple[str, dict[str, Any], str]]) -> None:
    """Fim de um timer com ação: roda a ferramenta da lista fechada e deixa a frase em `t.resultado`."""
    if not t.acao:
        return
    alvo = acoes.get(t.acao)
    if alvo is None:
        t.resultado = "Timer acabou, mas essa ação não está mais disponível."
        return
    ok, saida = await registro.rodar(alvo[0], dict(alvo[1]))
    t.resultado = saida if ok else f"Timer acabou, mas não consegui {alvo[2]}: {saida}"


_UNIDADES = {"s": 1, "seg": 1, "segundo": 1, "segundos": 1, "m": 60, "min": 60, "minuto": 60, "minutos": 60,
             "h": 3600, "hora": 3600, "horas": 3600}
_INICIO = r"^(?:(?:vision|visao|hey|ei|ok|pode|por favor|ai|e|entao|agora|ja|me)\s+)*"
_CRIAR = re.compile(_INICIO + r"(?:(?:poe|coloca|bota|cria|faz|liga|inicia|comeca|marca|ativa)\s+)?(?:um\s+)?"
                    r"(?:timer|cronometro|temporizador)\s+(?:de\s+)?(\d{1,3})\s*([a-z]+)"
                    r"(?:\s+e\s+(\d{1,3})\s*([a-z]+))?(?:\s+(?:por favor|pf))?$")
_CANCELAR = re.compile(_INICIO + r"(?:cancela|cancelar|desliga|desligar|para|parar|tira|apaga)\s+(?:o\s+)?"
                       r"(?:timer|cronometro|temporizador|alarme)(?:\s+(?:por favor|pf))?$")


def comando_de_timer(frase: str) -> tuple[str, dict[str, Any]] | None:
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    if not t or "nao" in t.split():
        return None
    if _CANCELAR.match(t):
        return "timer_cancelar", {}
    m = _CRIAR.match(t)
    if not m:
        return None
    total = 0
    for valor, unidade in ((m.group(1), m.group(2)), (m.group(3), m.group(4))):
        if valor is None:
            continue
        if unidade not in _UNIDADES:
            return None
        total += int(valor) * _UNIDADES[unidade]
    if total <= 0:
        return None
    h, resto = divmod(total, 3600)
    mi, s = divmod(resto, 60)
    return "timer_criar", {k: v for k, v in (("horas", h), ("minutos", mi), ("segundos", s)) if v}
