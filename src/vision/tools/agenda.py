"""Ferramentas de agenda em português, por cima do MCP do Google Agenda (@cocal/google-calendar-mcp).

O MCP real tem esquemas enormes (create-event tem ~30 parâmetros); modelo pequeno se perde.
Aqui o modelo vê 5 ferramentas simples e este módulo traduz para o MCP.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from vision import tempo
from vision.config import Config
from vision.tools.base import ComDados, ErroFerramenta, Ferramenta, esquema, numero, texto
from vision.tools.mcp_host import HostMCP

SEM_LOGIN = ("No valid Google account tokens", "invalid_grant", "No authenticated accounts", "Token has been expired")


class Agenda:
    def __init__(self, cfg: Config, host: HostMCP):
        self.cfg = cfg
        self.host = host
        self.servidor = cfg.get("agenda.servidor", "google-calendar")
        self.leitura: list[str] = cfg.get("agenda.calendarios_leitura", ["primary"])
        self.escrita: str = cfg.get("agenda.calendario_escrita", "primary")
        self.duracao_padrao = int(cfg.get("agenda.duracao_padrao_min", 60))
        self.fuso = cfg.get("usuario.fuso", "America/Sao_Paulo")

    # ---------- chamada ao MCP ----------

    async def _mcp(self, ferramenta: str, args: dict[str, Any]) -> Any:
        r = await self.host.chamar(self.servidor, ferramenta, args)
        if not r.ok and any(s in r.texto for s in SEM_LOGIN):
            await self.host.reconectar(self.servidor)  # talvez o login tenha sido refeito lá fora
            r = await self.host.chamar(self.servidor, ferramenta, args)
        if not r.ok:
            if any(s in r.texto for s in SEM_LOGIN) or "OAuth" in r.texto or "indisponível" in r.texto:
                raise ErroFerramenta(
                    "A agenda do Google está sem login. Diga ao Felipe para rodar `vision google-login`."
                    f" (detalhe: {r.texto[:200]})"
                )
            raise ErroFerramenta(f"A agenda do Google deu erro: {r.texto[:300]}")
        try:
            return r.json()
        except json.JSONDecodeError:
            return {"texto": r.texto}

    # ---------- formatação ----------

    def _linha_evento(self, ev: dict[str, Any], hoje: date) -> str:
        ini, fim = ev.get("start") or {}, ev.get("end") or {}
        titulo = ev.get("summary") or "(sem título)"
        if ini.get("date") and not ini.get("dateTime"):
            d = date.fromisoformat(ini["date"])
            quando = f"{tempo.rotulo_dia(d, hoje)} (dia inteiro)"
        else:
            a = datetime.fromisoformat(ini["dateTime"]).astimezone(tempo.FUSO)
            b = datetime.fromisoformat(fim["dateTime"]).astimezone(tempo.FUSO) if fim.get("dateTime") else None
            quando = f"{tempo.rotulo_dia(a.date(), hoje)} {a:%H:%M}" + (f"–{b:%H:%M}" if b else "")
        extras = []
        if ev.get("location"):
            extras.append(f"local: {ev['location']}")
        if "holiday" in (ev.get("calendarId") or ""):
            extras.append("FERIADO")
        extras.append(f"id: {ev.get('id')}")
        return f"- {quando}  {titulo}  ({'; '.join(extras)})"

    def _formatar(self, eventos: list[dict[str, Any]], vazio: str) -> str:
        if not eventos:
            return vazio
        hoje = tempo.agora().date()
        return "\n".join(self._linha_evento(e, hoje) for e in eventos)

    def para_tela(self, ev: dict[str, Any], dia: date | None = None) -> dict[str, Any] | None:
        """Evento no formato do painel "Agenda de hoje" ({id, titulo, inicio "HH:MM", fim?, local?}).

        None se o evento não for do dia pedido (padrão: hoje): o painel só mostra hoje.
        """
        dia = dia or tempo.agora().date()
        ini, fim = ev.get("start") or {}, ev.get("end") or {}
        tela: dict[str, Any] = {"id": str(ev.get("id") or ""), "titulo": ev.get("summary") or "(sem título)"}
        if ini.get("dateTime"):
            a = datetime.fromisoformat(ini["dateTime"]).astimezone(tempo.FUSO)
            if a.date() != dia:
                return None
            tela["inicio"] = f"{a:%H:%M}"
            if fim.get("dateTime"):
                tela["fim"] = f"{datetime.fromisoformat(fim['dateTime']).astimezone(tempo.FUSO):%H:%M}"
        elif ini.get("date"):
            if date.fromisoformat(ini["date"]) != dia:
                return None
            tela["inicio"], tela["diaInteiro"] = "00:00", True
        else:
            return None
        if ev.get("location"):
            tela["local"] = ev["location"]
        if "holiday" in (ev.get("calendarId") or ""):
            tela["feriado"] = True
        return tela

    async def hoje(self) -> list[dict[str, Any]]:
        """Eventos de hoje para o painel (foto inicial da tela)."""
        dia = tempo.agora().date()
        dados = await self._mcp("list-events", {
            "calendarId": self.leitura, "timeMin": f"{dia.isoformat()}T00:00:00",
            "timeMax": f"{dia.isoformat()}T23:59:59", "timeZone": self.fuso,
        })
        return [t for e in dados.get("events", []) if (t := self.para_tela(e, dia))]

    def previa_criar(self, args: dict[str, Any]) -> dict[str, Any] | None:
        """Cartão do evento pendente, só se for hoje e com horário (é o que o painel mostra)."""
        try:
            ini, fim, dia_inteiro = self._montar_horario(args)
        except ErroFerramenta:
            return None
        if dia_inteiro or ini[:10] != tempo.agora().date().isoformat():
            return None
        return {"titulo": (args.get("titulo") or "").strip(), "inicio": ini[11:16], "fim": fim[11:16]}

    # ---------- ferramentas ----------

    async def listar(self, args: dict[str, Any]) -> str:
        inicio = _data(args.get("data_inicio"), "data_inicio") or tempo.agora().date()
        fim = _data(args.get("data_fim"), "data_fim") or inicio
        if fim < inicio:
            inicio, fim = fim, inicio
        dados = await self._mcp(
            "list-events",
            {
                "calendarId": self.leitura,
                "timeMin": f"{inicio.isoformat()}T00:00:00",
                "timeMax": f"{fim.isoformat()}T23:59:59",
                "timeZone": self.fuso,
            },
        )
        periodo = inicio.strftime("%d/%m") + ("" if fim == inicio else f" a {fim:%d/%m}")
        texto_ = f"Eventos de {periodo}:\n" + self._formatar(dados.get("events", []), "Nenhum evento nesse período.")
        hoje = tempo.agora().date()
        if not (inicio == fim == hoje):
            return texto_  # o painel é de hoje: outra data não substitui a lista
        return ComDados(texto_, [t for e in dados.get("events", []) if (t := self.para_tela(e, hoje))])

    async def buscar(self, args: dict[str, Any]) -> str:
        consulta = (args.get("texto") or "").strip()
        if not consulta:
            raise ErroFerramenta("Informe o texto a buscar.")
        hoje = tempo.agora().date()
        inicio = _data(args.get("data_inicio"), "data_inicio") or hoje
        fim = _data(args.get("data_fim"), "data_fim") or inicio + timedelta(days=60)
        dados = await self._mcp(
            "search-events",
            {
                "calendarId": self.leitura,
                "query": consulta,
                "timeMin": f"{inicio.isoformat()}T00:00:00",
                "timeMax": f"{fim.isoformat()}T23:59:59",
                "timeZone": self.fuso,
            },
        )
        return f"Busca por '{consulta}' de {inicio:%d/%m} a {fim:%d/%m}:\n" + self._formatar(
            dados.get("events", []), "Nada encontrado."
        )

    def _montar_horario(self, args: dict[str, Any]) -> tuple[str, str, bool]:
        d = _data(args.get("data"), "data")
        if d is None:
            raise ErroFerramenta("Informe a data do evento (AAAA-MM-DD).")
        hi = _hora(args.get("hora_inicio"))
        if hi is None:  # dia inteiro
            return d.isoformat(), (d + timedelta(days=1)).isoformat(), True
        inicio = datetime.combine(d, hi)
        hf = _hora(args.get("hora_fim"))
        if hf is not None:
            fim = datetime.combine(d, hf)
            if fim <= inicio:
                fim += timedelta(days=1)
        else:
            dur = args.get("duracao_min") or self.duracao_padrao
            fim = inicio + timedelta(minutes=float(dur))
        return inicio.strftime("%Y-%m-%dT%H:%M:00"), fim.strftime("%Y-%m-%dT%H:%M:00"), False

    async def criar(self, args: dict[str, Any]) -> str:
        titulo = (args.get("titulo") or "").strip()
        if not titulo:
            raise ErroFerramenta("Informe o título do evento.")
        ini, fim, _ = self._montar_horario(args)
        payload: dict[str, Any] = {
            "calendarId": self.escrita,
            "summary": titulo,
            "start": ini,
            "end": fim,
            "timeZone": self.fuso,
        }
        for origem, destino in (("local", "location"), ("descricao", "description")):
            if args.get(origem):
                payload[destino] = args[origem]
        dados = await self._mcp("create-event", payload)
        ev = dados.get("event", {})
        aviso = ""
        if dados.get("conflicts"):
            aviso = f" Atenção: conflita com {len(dados['conflicts'])} evento(s)."
        return ComDados(f"Evento criado: {titulo} (id: {ev.get('id')}).{aviso}", self.para_tela(ev) if ev else None)

    async def descrever_criar(self, args: dict[str, Any]) -> str:
        titulo = (args.get("titulo") or "(sem título)").strip()
        ini, fim, dia_inteiro = self._montar_horario(args)
        return f"Vou criar '{titulo}' {_quando(ini, fim, dia_inteiro)}" + (
            f", em {args['local']}" if args.get("local") else ""
        ) + "."

    async def _evento(self, evento_id: str) -> dict[str, Any]:
        if not evento_id:
            raise ErroFerramenta("Informe o evento_id (use agenda_listar ou agenda_buscar para achar o id).")
        dados = await self._mcp("get-event", {"calendarId": self.escrita, "eventId": evento_id})
        return dados.get("event", dados)

    async def alterar(self, args: dict[str, Any]) -> str:
        evento_id = args.get("evento_id", "")
        payload: dict[str, Any] = {"calendarId": self.escrita, "eventId": evento_id, "timeZone": self.fuso}
        if args.get("titulo"):
            payload["summary"] = args["titulo"]
        if args.get("local"):
            payload["location"] = args["local"]
        if args.get("data") or args.get("hora_inicio"):
            if not args.get("data"):
                atual = await self._evento(evento_id)
                args = {**args, "data": (atual.get("start", {}).get("dateTime") or atual["start"]["date"])[:10]}
            payload["start"], payload["end"], _ = self._montar_horario(args)
        dados = await self._mcp("update-event", payload)
        ev = dados.get("event", dados) if isinstance(dados, dict) else {}
        # dados None = o evento saiu de hoje (ou não deu para ler): a tela tira ele do painel.
        return ComDados(f"Evento {evento_id} alterado.", self.para_tela(ev) if ev.get("start") else None)

    async def descrever_alterar(self, args: dict[str, Any]) -> str:
        ev = await self._evento(args.get("evento_id", ""))
        mudancas = []
        if args.get("titulo"):
            mudancas.append(f"título para '{args['titulo']}'")
        if args.get("data") or args.get("hora_inicio"):
            a = {**args}
            if not a.get("data"):
                a["data"] = (ev.get("start", {}).get("dateTime") or ev["start"]["date"])[:10]
            ini, fim, dia = self._montar_horario(a)
            mudancas.append(f"horário para {_quando(ini, fim, dia)}")
        if args.get("local"):
            mudancas.append(f"local para {args['local']}")
        return f"Vou mudar '{ev.get('summary', '?')}': " + (", ".join(mudancas) or "nada") + "."

    async def apagar(self, args: dict[str, Any]) -> str:
        evento_id = args.get("evento_id", "")
        try:  # sem confirmação, a resposta diz QUAL evento saiu: um id trocado pelo modelo aparece na hora
            qual = (await self.descrever_apagar(args)).removeprefix("Vou apagar ").rstrip(".")
        except Exception:  # noqa: BLE001 - não conseguir ler o evento não impede o apagar
            qual = f"o evento {evento_id}"
        await self._mcp("delete-event", {"calendarId": self.escrita, "eventId": evento_id})
        return ComDados(f"Apaguei {qual}.", {"id": evento_id})

    async def descrever_apagar(self, args: dict[str, Any]) -> str:
        ev = await self._evento(args.get("evento_id", ""))
        ini = ev.get("start", {})
        quando = ""
        if ini.get("dateTime"):
            a = datetime.fromisoformat(ini["dateTime"]).astimezone(tempo.FUSO)
            quando = f" de {tempo.rotulo_dia(a.date(), tempo.agora().date())} às {a:%H:%M}"
        return f"Vou apagar '{ev.get('summary', '?')}'{quando}."

    def ferramentas(self) -> list[Ferramenta]:
        data_p = texto("Data no formato AAAA-MM-DD. Use a tabela de datas do sistema.")
        return [
            Ferramenta(
                "agenda_listar",
                "Lista os compromissos da agenda do Felipe (inclui feriados) entre duas datas. "
                "Use para 'agenda de hoje', 'o que tenho amanhã', 'minha semana', 'estou livre?'.",
                esquema(["data_inicio"], data_inicio=data_p, data_fim=texto("Data final AAAA-MM-DD (opcional; padrão = data_inicio)")),
                self.listar,
                grupo="agenda",
                conteudo_externo=True,  # título e descrição de convite vêm de outras pessoas
            ),
            Ferramenta(
                "agenda_buscar",
                "Procura compromissos por texto (ex.: 'prova', 'dentista') nos próximos 60 dias ou no período dado.",
                esquema(["texto"], texto=texto("O que procurar"), data_inicio=data_p, data_fim=data_p),
                self.buscar,
                grupo="agenda",
                conteudo_externo=True,  # título e descrição de convite vêm de outras pessoas
            ),
            Ferramenta(
                "agenda_criar",
                "Cria um compromisso na agenda. Sem hora_inicio vira evento de dia inteiro.",
                esquema(
                    ["titulo", "data"],
                    titulo=texto("Título curto"),
                    data=data_p,
                    hora_inicio=texto("HH:MM (24h)"),
                    hora_fim=texto("HH:MM (24h), opcional"),
                    duracao_min=numero("Duração em minutos, se não houver hora_fim"),
                    local=texto("Local, opcional"),
                    descricao=texto("Observações, opcional"),
                ),
                self.criar,
                escrita=True,
                descrever=self.descrever_criar,
                grupo="agenda",
                previa=self.previa_criar,
            ),
            Ferramenta(
                "agenda_alterar",
                "Muda título, data, horário ou local de um compromisso existente (precisa do id; liste antes).",
                esquema(
                    ["evento_id"],
                    evento_id=texto("id do evento, vindo de agenda_listar/agenda_buscar"),
                    titulo=texto("Novo título"),
                    data=data_p,
                    hora_inicio=texto("Nova hora HH:MM"),
                    hora_fim=texto("Nova hora final HH:MM"),
                    local=texto("Novo local"),
                ),
                self.alterar,
                escrita=True,
                descrever=self.descrever_alterar,
                grupo="agenda",
            ),
            Ferramenta(
                "agenda_apagar",
                "Apaga um compromisso (precisa do id; liste antes).",
                esquema(["evento_id"], evento_id=texto("id do evento")),
                self.apagar,
                escrita=True,
                descrever=self.descrever_apagar,
                grupo="agenda",
                previa=lambda a: {"id": str(a.get("evento_id") or "")} if a.get("evento_id") else None,
            ),
        ]


def _data(valor: Any, campo: str) -> date | None:
    if not valor:
        return None
    s = str(valor).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m"):
        try:
            d = datetime.strptime(s, fmt).date()
            return d.replace(year=tempo.agora().year) if fmt == "%d/%m" else d
        except ValueError:
            continue
    raise ErroFerramenta(f"{campo} inválida: '{s}'. Use AAAA-MM-DD.")


def _hora(valor: Any):
    if not valor:
        return None
    s = str(valor).strip().lower().replace("h", ":").rstrip(":")
    if "t" in s:  # veio um ISO completo
        s = s.split("t", 1)[1]
    partes = s.split(":")
    try:
        h = int(partes[0])
        m = int(partes[1]) if len(partes) > 1 and partes[1] else 0
        return datetime(2000, 1, 1, h, m).time()
    except (ValueError, IndexError) as e:
        raise ErroFerramenta(f"Hora inválida: '{valor}'. Use HH:MM.") from e


def _quando(ini: str, fim: str, dia_inteiro: bool) -> str:
    hoje = tempo.agora().date()
    if dia_inteiro:
        return f"{tempo.rotulo_dia(date.fromisoformat(ini), hoje)} (dia inteiro)"
    a, b = datetime.fromisoformat(ini), datetime.fromisoformat(fim)
    return f"{tempo.rotulo_dia(a.date(), hoje)} das {a:%H:%M} às {b:%H:%M}"
