"""O cérebro como serviço HTTP local (127.0.0.1), para outros canais (ex.: a skill da Alexa, depois).

POST /conversa  {"texto": "...", "canal": "texto|voz|alexa", "sessao": "id"}
  → {"resposta": "...", "aguardando_confirmacao": bool, "ferramentas": [...], "segundos": 1.2}
GET  /status
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from jarvis.config import Config
from jarvis.montagem import Jarvis, montar


def criar_app(cfg: Config, jarvis: Jarvis | None = None) -> Starlette:
    estado: dict[str, Jarvis] = {"j": jarvis} if jarvis is not None else {}

    @asynccontextmanager
    async def ciclo(_app):
        if jarvis is not None:
            yield
            return
        async with montar(cfg) as j:
            estado["j"] = j
            yield

    async def conversa(req: Request) -> JSONResponse:
        try:
            dados = await req.json()
        except ValueError:
            return JSONResponse({"erro": "corpo precisa ser JSON"}, status_code=400)
        texto = str(dados.get("texto") or "").strip()
        if not texto:
            return JSONResponse({"erro": "campo 'texto' vazio"}, status_code=400)
        canal = str(dados.get("canal") or "texto")
        sessao = str(dados.get("sessao") or "padrao")
        agente = estado["j"].agente
        if canal == "alexa":
            r = await agente.responder_com_prazo(texto, canal, sessao, float(cfg.get("servidor.prazo_alexa_s", 6)))
        else:
            r = await agente.responder(texto, canal, sessao)
        return JSONResponse(
            {
                "resposta": r.texto,
                "aguardando_confirmacao": r.aguardando_confirmacao,
                "ferramentas": [{"nome": f["nome"], "ok": f["ok"]} for f in r.ferramentas],
                "segundos": round(r.segundos, 2),
            }
        )

    async def status(_req: Request) -> JSONResponse:
        j = estado["j"]
        return JSONResponse(
            {
                "modelo": j.agente.llm.modelo,
                "mcp": j.host.status(),
                "memorias": j.memorias.total() if j.memorias else 0,
                "ferramentas": list(j.registro.ferramentas),
            }
        )

    return Starlette(routes=[Route("/conversa", conversa, methods=["POST"]), Route("/status", status)], lifespan=ciclo)


def rodar(cfg: Config) -> None:
    import uvicorn

    host = cfg.get("servidor.host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Por segurança o servidor só escuta em 127.0.0.1. Exponha via túnel quando for plugar a Alexa.")
    uvicorn.run(criar_app(cfg), host=host, port=int(cfg.get("servidor.porta", 8765)), log_level="warning")
