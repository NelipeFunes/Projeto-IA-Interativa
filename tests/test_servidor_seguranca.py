"""O servidor do núcleo não pode ser usado por um site aberto no navegador (CSRF, sequestro de WebSocket)."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.eventos import Barramento
from jarvis.server import Controle, criar_app

TOKEN = "segredo-de-teste"
ORIGEM = "http://127.0.0.1:8765"


class _Registro:
    def __init__(self):
        self.textos, self.confirmacoes, self.ouvir, self.parar, self.janela = [], [], [], 0, 0


def _app(cfg, barramento=None):
    reg = _Registro()

    async def texto(t):
        reg.textos.append(t)
        if barramento is not None:  # eco: prova que o que o núcleo publica chega na tela
            barramento.publicar({"tipo": "eco", "tamanho": len(t)})

    async def confirmar(pid, sim):
        reg.confirmacoes.append((pid, sim))

    async def painel():
        return {"tipo": "painel", "nome": "Vision", "agenda": [], "memorias": []}

    def parar():
        reg.parar += 1

    def abrir():
        reg.janela += 1

    controle = Controle(texto=texto, confirmar=confirmar, ouvir=reg.ouvir.append, parar_fala=parar,
                        abrir_janela=abrir, painel=painel)
    # Sem `with TestClient(...)`: o ciclo de vida (que montaria o agente de verdade) não roda.
    return TestClient(criar_app(cfg, token=TOKEN, barramento=barramento, controle=controle)), reg


# ------------------------------------------------------------------ HTTP


@pytest.mark.parametrize("rota,metodo", [("/estado", "get"), ("/janela", "post"), ("/conversa", "post"),
                                         ("/memorias", "get"), ("/status", "get")])
def test_http_sem_token_ou_com_token_errado_e_recusado(cfg, rota, metodo):
    c, reg = _app(cfg)
    assert getattr(c, metodo)(rota).status_code == 401
    assert getattr(c, metodo)(rota, headers={"Authorization": "Bearer errado"}).status_code == 401
    assert getattr(c, metodo)(rota, headers={"Authorization": TOKEN}).status_code == 401  # sem "Bearer"
    assert reg.janela == 0


def test_http_de_outra_origem_e_recusado_mesmo_com_token(cfg):
    c, reg = _app(cfg)
    r = c.post("/janela", json={"acao": "mostrar"},
               headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://site-qualquer.com"})
    assert r.status_code == 403 and reg.janela == 0


def test_http_com_token_funciona(cfg):
    c, reg = _app(cfg)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/estado", headers=auth).json()["tipo"] == "painel"
    assert c.post("/janela", json={"acao": "mostrar"}, headers={**auth, "Origin": ORIGEM}).status_code == 200
    assert c.post("/janela", json={"acao": "rm -rf"}, headers=auth).status_code == 400
    assert reg.janela == 1


def test_arquivos_da_interface_nao_precisam_de_token_e_nao_saem_da_pasta(cfg, tmp_path):
    (tmp_path / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (tmp_path.parent / "fora.txt").write_text("segredo", encoding="utf-8")
    c = TestClient(criar_app(cfg, token=TOKEN, pasta_app=tmp_path))
    assert c.get("/app/index.html").text == "<html>ok</html>"
    assert c.get("/app/../fora.txt").status_code == 404
    assert c.get("/app/%2e%2e/fora.txt").status_code == 404


# ------------------------------------------------------------------ WebSocket


def test_ws_sem_origin_ou_de_outra_origem_e_fechado(cfg):
    c, _ = _app(cfg, Barramento())
    for cabecalhos in ({}, {"Origin": "https://site-qualquer.com"}, {"Origin": "null"}):
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/ws", headers=cabecalhos) as ws:
            ws.receive_text()
        assert e.value.code == 4403


def test_ws_sem_ola_valido_e_fechado(cfg):
    c, reg = _app(cfg, Barramento())
    for primeira in ('{"tipo": "ola", "token": "errado"}', '{"tipo": "texto", "texto": "sim"}', "lixo", "[]"):
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
            ws.send_text(primeira)
            ws.receive_text()
        assert e.value.code == 4401
    assert reg.textos == []


def test_ws_autenticado_recebe_a_foto_os_eventos_e_manda_comandos_validados(cfg):
    barramento = Barramento()
    c, reg = _app(cfg, barramento)
    with c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
        ws.send_json({"tipo": "ola", "token": TOKEN})
        assert ws.receive_json()["tipo"] == "painel"
        ws.send_json({"tipo": "texto", "texto": "  marca barbeiro  "})
        assert ws.receive_json() == {"tipo": "eco", "tamanho": len("marca barbeiro")}
        ws.send_json({"tipo": "texto", "texto": ""})  # vazio: ignorado
        ws.send_json({"tipo": "confirmar", "id": "p1", "sim": "sim"})  # sim precisa ser booleano: ignorado
        ws.send_json({"tipo": "confirmar", "id": "p1", "sim": True})
        ws.send_json({"tipo": "ouvir", "segurando": True})
        ws.send_json({"tipo": "parar_fala"})
        ws.send_json({"tipo": "desconhecido"})
        ws.send_json({"tipo": "ouvir", "segurando": "sim"})  # ignorado
        ws.send_json({"tipo": "texto", "texto": "fim"})
        assert ws.receive_json() == {"tipo": "eco", "tamanho": 3}  # tudo antes já foi processado
    assert reg.textos == ["marca barbeiro", "fim"]
    assert reg.confirmacoes == [("p1", True)]
    assert reg.ouvir == [True] and reg.parar == 1


def test_texto_gigante_e_cortado(cfg):
    c, reg = _app(cfg, Barramento())
    with c.websocket_connect("/ws", headers={"Origin": ORIGEM}) as ws:
        ws.send_json({"tipo": "ola", "token": TOKEN})
        ws.receive_json()
        ws.send_json({"tipo": "texto", "texto": "a" * 50_000})
        assert ws.receive_json() == {"tipo": "eco", "tamanho": 2000}


def test_ws_no_uvicorn_de_verdade(cfg):
    """O TestClient não usa o servidor real; sem a biblioteca de WebSocket o uvicorn recusava tudo (30/09)."""
    import asyncio
    import json
    import socket
    import threading
    import time

    import uvicorn
    import websockets

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    cfg.bruto.setdefault("servidor", {})["porta"] = porta
    app = TestClient(criar_app(cfg, token=TOKEN, barramento=Barramento(),
                               controle=Controle(painel=_painel_fixo))).app
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="error", lifespan="off"))
    threading.Thread(target=servidor.run, daemon=True).start()
    for _ in range(100):
        if servidor.started:
            break
        time.sleep(0.05)

    async def conversar():
        url = f"ws://127.0.0.1:{porta}/ws"
        async with websockets.connect(url, origin=f"http://127.0.0.1:{porta}") as ws:
            await ws.send(json.dumps({"tipo": "ola", "token": TOKEN}))
            assert json.loads(await ws.recv())["tipo"] == "painel"
        try:
            async with websockets.connect(url, origin="https://site-qualquer.com") as ws:
                await ws.recv()
            raise AssertionError("aceitou outra origem")
        except websockets.InvalidStatus as e:
            assert e.response.status_code == 403

    try:
        asyncio.run(conversar())
    finally:
        servidor.should_exit = True


async def _painel_fixo():
    return {"tipo": "painel", "nome": "Vision"}
