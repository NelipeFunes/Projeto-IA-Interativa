"""O cérebro como serviço local (só 127.0.0.1), para a tela do núcleo e outros canais (ex.: Alexa, depois).

Toda rota, menos os arquivos da interface em /app, exige o token do núcleo (aleatório a cada início,
gravado em data/nucleo.json). HTTP: cabeçalho `Authorization: Bearer <token>`. WebSocket: a primeira
mensagem é {"tipo": "ola", "token": ...}. Se vier cabeçalho Origin, ele precisa ser o próprio servidor.
Motivo: sem isso, qualquer site aberto no navegador podia chamar /conversa e responder "sim" por você
(CSRF), ou abrir o WebSocket e ler tudo o que você fala.

POST   /conversa     {"texto": "...", "canal": "texto|voz|alexa", "sessao": "id"} → resposta
GET    /status       modelo, MCPs, memórias, ferramentas
GET    /estado       foto da tela (evento "painel")
GET    /memorias     lista; DELETE /memorias/{id} apaga (clique na tela = você decidiu, sem perguntar de novo)
POST   /janela       {"acao": "mostrar"}: abre a janela (usado quando você roda `vision` com o núcleo já ligado)
WS     /ws           eventos do núcleo → tela; comandos da tela → núcleo (texto, confirmar, ouvir, parar_fala,
                     ajustes, salvar_ajustes, amostra_voz)
GET    /app/...      a interface (arquivos estáticos de ui/dist, sem segredo)
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from vision.config import Config
from vision.eventos import Barramento
from vision.montagem import Vision, montar

log = logging.getLogger(__name__)

LIMITE_TEXTO = 2000
PRAZO_OLA_S = 3.0


@dataclass
class Controle:
    """O que a tela pode pedir ao núcleo. Sem núcleo (`vision servidor`), só as rotas HTTP funcionam."""

    texto: Callable[[str], Awaitable[None]] | None = None
    confirmar: Callable[[str, bool], Awaitable[None]] | None = None
    ouvir: Callable[[bool], None] | None = None
    parar_fala: Callable[[], None] | None = None
    abrir_janela: Callable[[], None] | None = None
    painel: Callable[[], Awaitable[dict[str, Any]]] | None = None
    # Tela de ajustes: a resposta volta como evento "ajustes" (e um "aviso" se algo deu errado).
    ler_ajustes: Callable[[], Awaitable[None]] | None = None
    salvar_ajustes: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    amostra_voz: Callable[[str], Awaitable[None]] | None = None


def origens_permitidas(porta: int) -> set[str]:
    return {f"http://127.0.0.1:{porta}", f"http://localhost:{porta}"}


def _token_confere(recebido: str, token: str) -> bool:
    return hmac.compare_digest(recebido.encode(), token.encode())


def criar_app(
    cfg: Config,
    vision: Vision | None = None,
    *,
    token: str | None = None,
    barramento: Barramento | None = None,
    controle: Controle | None = None,
    pasta_app: Path | None = None,
) -> Starlette:
    estado: dict[str, Vision] = {"j": vision} if vision is not None else {}
    token = token or secrets.token_urlsafe(32)
    origens = origens_permitidas(int(cfg.get("servidor.porta", 8765)))
    controle = controle or Controle()
    tarefas: set[asyncio.Task] = set()  # comandos da tela em andamento (referência forte até terminarem)

    @asynccontextmanager
    async def ciclo(_app):
        if vision is not None:
            yield
            return
        async with montar(cfg) as j:
            estado["j"] = j
            yield

    def barrar(req: Request) -> Response | None:
        """Token e Origin de uma requisição HTTP; None = pode passar."""
        origem = req.headers.get("origin")
        if origem is not None and origem not in origens:
            return JSONResponse({"erro": "origem não permitida"}, status_code=403)
        esquema, _, valor = req.headers.get("authorization", "").partition(" ")
        if esquema.lower() != "bearer" or not _token_confere(valor.strip(), token):
            return JSONResponse({"erro": "token ausente ou errado (veja data/nucleo.json)"}, status_code=401)
        return None

    def protegida(rota: Callable[[Request], Awaitable[Response]]) -> Callable[[Request], Awaitable[Response]]:
        async def envolvida(req: Request) -> Response:
            return barrar(req) or await rota(req)

        return envolvida

    async def conversa(req: Request) -> JSONResponse:
        try:
            dados = await req.json()
        except ValueError:
            return JSONResponse({"erro": "corpo precisa ser JSON"}, status_code=400)
        if not isinstance(dados, dict):
            return JSONResponse({"erro": "corpo precisa ser um objeto JSON"}, status_code=400)
        texto = str(dados.get("texto") or "").strip()[:LIMITE_TEXTO]
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

    async def foto() -> dict[str, Any]:
        if controle.painel is not None:
            return await controle.painel()
        j = estado["j"]
        memorias = [{"id": m.id, "texto": m.texto} for m in reversed(j.memorias.todas())] if j.memorias else []
        return {"tipo": "painel", "nome": cfg.get("assistente.nome", "Vision"), "memorias": memorias[:30]}

    async def estado_tela(_req: Request) -> JSONResponse:
        return JSONResponse(await foto())

    async def memorias(req: Request) -> JSONResponse:
        m = estado["j"].memorias
        if m is None:
            return JSONResponse({"erro": "memória desligada"}, status_code=404)
        if req.method == "DELETE":
            try:
                mid = int(req.path_params["id"])
            except (KeyError, ValueError):
                return JSONResponse({"erro": "id inválido"}, status_code=400)
            apagou = await m.esquecer(mid)
            if apagou and barramento is not None:
                barramento.publicar({"tipo": "ferramenta_fim", "nome": "esquecer", "ok": True,
                                     "dados": {"id": mid}})
            return JSONResponse({"apagou": apagou}, status_code=200 if apagou else 404)
        return JSONResponse([{"id": x.id, "texto": x.texto, "categoria": x.categoria, "criado_em": x.criado_em}
                             for x in m.todas()])

    async def janela(req: Request) -> JSONResponse:
        try:
            dados = await req.json()
        except ValueError:
            dados = {}
        if not isinstance(dados, dict) or dados.get("acao") != "mostrar" or controle.abrir_janela is None:
            return JSONResponse({"erro": "ação desconhecida"}, status_code=400)
        controle.abrir_janela()
        return JSONResponse({"ok": True})

    async def ws(sock: WebSocket) -> None:
        # Navegador SEMPRE manda Origin no WebSocket: sem ele, ou com outro, não é a nossa tela.
        if sock.headers.get("origin") not in origens:
            await sock.close(code=4403)
            return
        await sock.accept()
        try:
            ola = json.loads(await asyncio.wait_for(sock.receive_text(), PRAZO_OLA_S))
            ok = isinstance(ola, dict) and ola.get("tipo") == "ola" and _token_confere(str(ola.get("token", "")), token)
        except (TimeoutError, ValueError, KeyError, WebSocketDisconnect):
            ok = False
        if not ok:
            await sock.close(code=4401)
            return
        if barramento is None:
            await sock.send_json(await foto())
            await sock.close()
            return
        with barramento.assinar() as fila:
            await sock.send_json(await foto())
            for ev in list(barramento.ultimos.values()):
                await sock.send_json(ev)
            envio = asyncio.create_task(_encaminhar(fila, sock))
            try:
                while True:
                    _comando(await sock.receive_json(), controle, tarefas)
            except (WebSocketDisconnect, RuntimeError, ValueError):
                pass
            finally:
                envio.cancel()

    rotas = [
        Route("/conversa", protegida(conversa), methods=["POST"]),
        Route("/status", protegida(status)),
        Route("/estado", protegida(estado_tela)),
        Route("/memorias", protegida(memorias)),
        Route("/memorias/{id}", protegida(memorias), methods=["DELETE"]),
        Route("/janela", protegida(janela), methods=["POST"]),
        WebSocketRoute("/ws", ws),
    ]
    if pasta_app is not None and (pasta_app / "index.html").exists():
        rotas.append(Mount("/app", StaticFiles(directory=pasta_app, html=True), name="app"))
    app = Starlette(routes=rotas, lifespan=ciclo)
    app.state.token = token
    return app


async def _encaminhar(fila: asyncio.Queue, sock: WebSocket) -> None:
    try:
        while True:
            await sock.send_json(await fila.get())
    except (WebSocketDisconnect, RuntimeError):
        pass


def _comando(msg: Any, controle: Controle, tarefas: set[asyncio.Task]) -> None:
    """Um comando da tela. Tudo é validado: a tela é código nosso, mas a mensagem chega por um socket."""
    if not isinstance(msg, dict):
        return
    tipo = msg.get("tipo")
    tarefa = None
    if tipo == "texto" and controle.texto is not None:
        texto = str(msg.get("texto") or "").strip()[:LIMITE_TEXTO]
        if texto:
            tarefa = asyncio.create_task(controle.texto(texto))
    elif tipo == "confirmar" and controle.confirmar is not None:
        pid, sim = msg.get("id"), msg.get("sim")
        if isinstance(pid, str) and isinstance(sim, bool):
            tarefa = asyncio.create_task(controle.confirmar(pid, sim))
    elif tipo == "ouvir" and controle.ouvir is not None and isinstance(msg.get("segurando"), bool):
        controle.ouvir(msg["segurando"])
    elif tipo == "parar_fala" and controle.parar_fala is not None:
        controle.parar_fala()
    elif tipo == "ajustes" and controle.ler_ajustes is not None:
        tarefa = asyncio.create_task(controle.ler_ajustes())
    elif tipo == "salvar_ajustes" and controle.salvar_ajustes is not None and isinstance(msg.get("valores"), dict):
        tarefa = asyncio.create_task(controle.salvar_ajustes(msg["valores"]))  # validado em vision/ajustes.py
    elif tipo == "amostra_voz" and controle.amostra_voz is not None and isinstance(msg.get("voz"), str):
        tarefa = asyncio.create_task(controle.amostra_voz(msg["voz"][:80]))
    if tarefa is not None:
        tarefas.add(tarefa)
        tarefa.add_done_callback(tarefas.discard)


def gravar_acesso(cfg: Config, porta: int, token: str) -> Path:
    """data/nucleo.json: como outro processo seu (a janela, o `vision` de novo) acha o núcleo."""
    arquivo = cfg.dados / "nucleo.json"
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps({"porta": porta, "token": token, "pid": os.getpid()}), encoding="utf-8")
    return arquivo


def rodar(cfg: Config) -> None:
    import uvicorn

    host = cfg.get("servidor.host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("Por segurança o servidor só escuta em 127.0.0.1. Exponha via túnel quando for plugar a Alexa.")
    from vision.nucleo import nucleo_rodando

    if nucleo_rodando():
        # O núcleo já serve esta mesma API (e é dono do data/nucleo.json): subir outro só quebraria o dele.
        raise SystemExit("O núcleo já está rodando e já serve a API em 127.0.0.1 (token em data/nucleo.json).")
    porta = int(cfg.get("servidor.porta", 8765))
    app = criar_app(cfg)
    arquivo = gravar_acesso(cfg, porta, app.state.token)
    print(f"Servidor em http://{host}:{porta} (token em {arquivo})")
    try:
        uvicorn.run(app, host=host, port=porta, log_level="warning")
    finally:
        arquivo.unlink(missing_ok=True)
