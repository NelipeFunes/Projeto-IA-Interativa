"""Ferramentas de timer e alarme (vision/timers.py faz a contagem; o núcleo toca e avisa no fim)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from vision import tempo
from vision.timers import Timers, descrever_duracao, para_dados
from vision.tools.base import ComDados, ErroFerramenta, Ferramenta, esquema, numero, texto
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
    def __init__(self, timers: Timers, relogio=tempo.agora):
        self.timers = timers
        self.relogio = relogio

    async def criar(self, args: dict[str, Any]) -> str:
        nome = str(args.get("nome") or "").strip()[:60]
        if re.match(r"^(timer|alarme|cronometro|temporizador)\b", normalizar(nome)):
            nome = ""  # "Timer de 1 minuto" não é nome: o aviso ficaria "o timer 'Timer de 1 minuto' de 1 minuto"
        if args.get("hora"):
            agora = self.relogio()
            alvo = _proxima(str(args["hora"]), agora)
            segundos = (alvo - agora).total_seconds()
            rotulo = f"das {alvo:%H:%M}" + ("" if alvo.date() == agora.date() else " (amanhã)")
            t = self.timers.criar(segundos, rotulo, nome, alarme=True)
            return ComDados(f"Alarme marcado para as {alvo:%H:%M}" + ("" if alvo.date() == agora.date() else
                            " de amanhã") + ".", para_dados(t, segundos))
        segundos = _numero(args, "horas") * 3600 + _numero(args, "minutos") * 60 + _numero(args, "segundos")
        if segundos <= 0:
            raise ErroFerramenta("Quanto tempo? Diga minutos, segundos ou horas (ou a hora do alarme).")
        if segundos > MAXIMO_S:
            raise ErroFerramenta("Timer de no máximo 24 horas.")
        rotulo = descrever_duracao(segundos)
        try:
            t = self.timers.criar(segundos, rotulo, nome)
        except ValueError as e:
            raise ErroFerramenta(str(e)) from e
        return ComDados(f"Timer de {rotulo} ligado" + (f" ({nome})" if nome else "") + ".", para_dados(t, segundos))

    async def listar(self, _args: dict[str, Any]) -> str:
        timers = self.timers.listar()
        if not timers:
            return "Nenhum timer ligado."
        linhas = []
        for t in timers:
            quem = (f"Alarme {t.rotulo}" if t.alarme else f"Timer de {t.rotulo}") + (f" ({t.nome})" if t.nome else "")
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
        if nome == "timer_cancelar" and len(self.timers.listar()) != 1:
            return None  # mais de um (ou nenhum): o modelo pergunta qual ou explica
        executar = self.criar if nome == "timer_criar" else self.cancelar
        try:
            return nome, args, str(await executar(args)), True
        except ErroFerramenta as e:
            return nome, args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        return [
            Ferramenta("timer_criar",
                       "Liga um timer (minutos/segundos/horas) ou um alarme num horário ('me avisa às 15h'). No fim o "
                       "Vision toca um alarme e avisa.",
                       esquema([], minutos=numero("Minutos"), segundos=numero("Segundos"), horas=numero("Horas"),
                               hora=texto("Para alarme: HH:MM (24h)"),
                               nome=texto("Para que é (ex.: 'forno', 'ligar pro banco'); vazio se ele não disse")),
                       self.criar, escrita=True, grupo="timer", confirmar=False),
            Ferramenta("timer_listar", "Timers e alarmes ligados e quanto falta.", esquema([]), self.listar,
                       grupo="timer"),
            Ferramenta("timer_cancelar", "Cancela um timer ou alarme (pelo nome, ou 'todos').",
                       esquema([], nome=texto("Nome ou duração do timer; vazio se só tem um")),
                       self.cancelar, escrita=True, grupo="timer", confirmar=False),
        ]


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
