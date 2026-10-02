"""Clima e o "bom dia" do Vision, como o JARVIS acordando o Tony: hora, clima, o que falta na agenda de hoje e os
timers ligados, numa resposta só e sem passar pelo modelo (é rápido e não inventa nada)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, timedelta
from typing import Any

from vision import tempo
from vision.clima import Clima, ErroClima, descrever_agora, descrever_dia
from vision.timers import Timers, descrever_duracao
from vision.tools.base import ErroFerramenta, Ferramenta, esquema, texto
from vision.tools.pc import normalizar

log = logging.getLogger(__name__)

PRAZO_PARTE_S = 8  # cada parte do resumo (clima, agenda) tem seu prazo: uma fora do ar não segura as outras


def saudacao(hora: int) -> str:
    return "Bom dia" if 5 <= hora < 12 else "Boa tarde" if 12 <= hora < 18 else "Boa noite"


def _dia_pedido(valor: Any, hoje: date) -> date:
    v = normalizar(str(valor or "hoje")).strip()
    if v in {"", "hoje", "agora"}:
        return hoje
    if v in {"amanha"}:
        return hoje + timedelta(days=1)
    if v in {"depois de amanha"}:
        return hoje + timedelta(days=2)
    try:
        return date.fromisoformat(v)
    except ValueError as e:
        raise ErroFerramenta("Dia: hoje, amanhã, depois de amanhã ou AAAA-MM-DD.") from e


class Briefing:
    def __init__(self, nome: str, clima: Clima | None = None, agenda: Any = None, timers: Timers | None = None):
        self.nome = nome
        self.clima = clima
        self.agenda = agenda
        self.timers = timers

    # ------------------------------------------------------------------ ferramentas

    async def ver_clima(self, args: dict[str, Any]) -> str:
        if self.clima is None:
            raise ErroFerramenta("O clima está desligado (clima.ativo no config).")
        hoje = tempo.agora().date()
        dia = _dia_pedido(args.get("dia"), hoje)
        try:
            p = await self.clima.previsao(args.get("cidade"))
        except ErroClima as e:
            raise ErroFerramenta(f"Não consegui ver o clima: {e}.") from e
        d = p.dia(dia)
        if d is None:
            return f"Só tenho a previsão dos próximos 7 dias para {p.cidade}."
        partes = [descrever_agora(p)] if dia == hoje else []
        partes.append(descrever_dia(d, tempo.rotulo_dia(dia, hoje) if dia != hoje else "hoje"))
        if dia != hoje:
            partes.insert(0, f"Em {p.cidade}.")
        return " ".join(partes)

    async def resumo(self, _args: dict[str, Any] | None = None) -> str:
        agora = tempo.agora()
        partes = [f"{saudacao(agora.hour)}, {self.nome}. São {agora:%H:%M} de "
                  f"{tempo.DIAS[agora.weekday()]}, {agora.day} de {tempo.MESES[agora.month - 1]}."]
        clima, agenda = await asyncio.gather(self._parte_clima(), self._parte_agenda(agora), return_exceptions=True)
        for parte in (clima, agenda):
            if isinstance(parte, str) and parte:
                partes.append(parte)
            elif isinstance(parte, BaseException):
                log.warning("uma parte do resumo falhou: %s", parte)
        if self.timers is not None and (ligados := self.timers.listar()):
            descricoes = [f"{t.nome or t.rotulo}, falta {descrever_duracao(self.timers.falta(t))}" for t in ligados[:3]]
            partes.append(f"Timers ligados: {'; '.join(descricoes)}.")
        return " ".join(partes)

    async def _parte_clima(self) -> str:
        if self.clima is None:
            return ""
        try:
            p = await asyncio.wait_for(self.clima.previsao(), PRAZO_PARTE_S)
        except (ErroClima, TimeoutError) as e:
            log.info("clima fora do resumo: %s", e)
            return ""
        hoje = p.dia(tempo.agora().date())
        texto_ = descrever_agora(p)
        if hoje is not None:
            texto_ += f" Máxima de {round(hoje.maxima)} e mínima de {round(hoje.minima)}"
            texto_ += (f", {hoje.chuva_pct}% de chance de chuva." if hoje.chuva_pct and hoje.chuva_pct >= 20 else ".")
        return texto_

    async def _parte_agenda(self, agora) -> str:
        if self.agenda is None:
            return ""
        try:
            eventos = await asyncio.wait_for(self.agenda.hoje(), PRAZO_PARTE_S)
        except Exception as e:  # noqa: BLE001 - sem login ou sem rede: o resto do resumo sai igual
            log.info("agenda fora do resumo: %s", e)
            return "Não consegui ver a agenda agora."
        hhmm = f"{agora:%H:%M}"
        feriados = [e["titulo"] for e in eventos if e.get("feriado")]
        def ainda_vale(e: dict[str, Any]) -> bool:
            inicio, fim = e.get("inicio", ""), e.get("fim") or e.get("inicio", "")
            if fim < inicio:  # 22:00–01:00: cruza a meia-noite, vale até o fim do dia
                fim = "23:59"
            return bool(e.get("diaInteiro")) or fim > hhmm

        faltam = [e for e in eventos if not e.get("feriado") and ainda_vale(e)]
        texto_ = f"Hoje é feriado: {feriados[0]}. " if feriados else ""
        if not faltam:
            return texto_ + ("Nada mais na agenda hoje." if eventos else "Sua agenda de hoje está livre.")
        itens = [e["titulo"] if e.get("diaInteiro") else f"{e['titulo']} às {e['inicio']}" for e in faltam[:4]]
        lista = itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]
        mais = f" (e mais {len(faltam) - 4})" if len(faltam) > 4 else ""
        n = len(faltam)
        return texto_ + f"Ainda hoje {'tem' if n == 1 else 'são'} {n} compromisso{'s' if n > 1 else ''}: {lista}{mais}."

    # ------------------------------------------------------------------ atalho

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """"Bom dia", "resumo do dia", "como está o meu dia?": o resumo sem passar pelo modelo."""
        if not pede_resumo(frase):
            return None
        return "resumo_do_dia", {}, await self.resumo(), True

    def ferramentas(self) -> list[Ferramenta]:
        lista = [
            Ferramenta(
                "resumo_do_dia",
                "Resumo do dia do Felipe: hora, clima, o que falta na agenda de hoje e timers. Use para 'bom dia', "
                "'me atualiza', 'como está meu dia', 'o que tenho pela frente'.",
                esquema([]), self.resumo, grupo="clima", conteudo_externo=self.agenda is not None, prazo_s=20,
            ),
        ]
        if self.clima is not None:
            lista.insert(0, Ferramenta(
                "clima",
                "Clima de agora e previsão até 7 dias (temperatura, chuva). Sem cidade, a do Felipe.",
                esquema([], cidade=texto("Outra cidade, só se ele disser"),
                        dia=texto("hoje (padrão), amanhã, depois de amanhã ou AAAA-MM-DD")),
                self.ver_clima, grupo="clima", prazo_s=15,
            ))
        return lista


_INICIO = r"^(?:(?:vision|visao|hey|ei|ok|e ai|oi|ola)\s+)*"
_RESUMO = re.compile(
    _INICIO + r"(?:bom dia|boa tarde|resumo do dia|me (?:da|de) (?:um|o) resumo(?: do dia)?|me atualiza|"
    r"como (?:esta|ta) (?:o )?meu dia|o que (?:eu )?tenho pela frente|status do dia)"
    r"(?:\s+(?:vision|visao))?(?:\s+(?:por favor|pf))?$"
)


def pede_resumo(frase: str) -> bool:
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    return bool(_RESUMO.match(t))
