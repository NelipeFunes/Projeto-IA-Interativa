"""Núcleo do assistente: roda em segundo plano (sem console), inicia com o Windows e junta num processo só:

- agente + MCPs + memória (montagem.montar);
- voz ("Hey Jarvis", atalho, janela de conversa), se os modelos de voz estiverem baixados;
- servidor local (API + WebSocket da tela, com token), só em 127.0.0.1;
- ícone na bandeja, com o menu;
- a janela, que é OUTRO processo (pywebview quer a thread principal; se ela travar, a voz continua).
  O núcleo sobe a janela escondida, reinicia se ela cair e manda comandos pela entrada padrão dela.

Entradas: `jarvisw` (sem console, é o que o atalho de inicialização roda) e `jarvis nucleo` (com console,
para ver o que acontece). Só um núcleo por vez: rodar de novo só abre a janela do que já está rodando.
Log em data/logs/nucleo.log.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import math
import os
import secrets
import subprocess
import sys
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from jarvis import config, inicializacao
from jarvis.eventos import Barramento

log = logging.getLogger("jarvis.nucleo")

NOME_MUTEX = "Local\\VisionNucleo"
ATUALIZAR_PAINEL_S = 300  # a agenda muda por fora (celular): a tela recebe uma foto nova a cada 5 min


def configurar_log(cfg: config.Config, console: bool = False) -> Path:
    pasta = cfg.dados / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / "nucleo.log"
    formato = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    h = RotatingFileHandler(arquivo, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(formato)
    raiz.addHandler(h)
    if console:
        c = logging.StreamHandler(sys.stdout)
        c.setFormatter(formato)
        raiz.addHandler(c)
    for ruidoso in ("httpx", "httpcore", "mcp", "openwakeword", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    return arquivo


# ------------------------------------------------------------------ instância única


class InstanciaUnica:
    """Mutex nomeado do Windows: existe enquanto o núcleo vive (o Windows solta se o processo morrer)."""

    def __init__(self, nome: str = NOME_MUTEX):
        self.nome = nome
        self.handle = None

    def pegar(self) -> bool:
        if sys.platform != "win32":
            return True
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        self.handle = k32.CreateMutexW(None, False, self.nome)
        ERROR_ALREADY_EXISTS = 183
        return bool(self.handle) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def ler_acesso(cfg: config.Config) -> dict[str, Any] | None:
    try:
        acesso = json.loads((cfg.dados / "nucleo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return acesso if isinstance(acesso, dict) and "porta" in acesso and "token" in acesso else None


def pedir_janela(cfg: config.Config) -> bool:
    """Um núcleo já está rodando: pede para ele abrir a janela."""
    import httpx

    acesso = ler_acesso(cfg)
    if acesso is None:
        return False
    try:
        r = httpx.post(f"http://127.0.0.1:{int(acesso['porta'])}/janela", json={"acao": "mostrar"},
                       headers={"Authorization": f"Bearer {acesso['token']}"}, timeout=5)
    except httpx.HTTPError:
        return False
    return r.status_code == 200


# ------------------------------------------------------------------ a janela (outro processo)


class Janela:
    """Supervisiona o processo da janela: sobe junto (escondida), reinicia se cair, recebe comandos."""

    def __init__(self, cfg: config.Config, url: str, token: str, mostrar_ao_subir: bool = False):
        self.cfg = cfg
        self.url = url
        self.token = token
        self.mostrar_ao_subir = mostrar_ao_subir
        self.proc: subprocess.Popen | None = None
        self.quedas: deque[float] = deque(maxlen=5)
        self.desistiu = False
        self.encerrando = False

    def _iniciar(self) -> None:
        env = {**os.environ, "VISION_URL": self.url, "VISION_TOKEN": self.token,
               "VISION_MOSTRAR": "1" if self.mostrar_ao_subir else "0"}
        saida = (self.cfg.dados / "logs" / "janela.log").open("a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "jarvis.cli", "interface", "--nucleo"],
                stdin=subprocess.PIPE, stdout=saida, stderr=subprocess.STDOUT, env=env, cwd=self.cfg.raiz,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            saida.close()  # o filho herdou o arquivo
        self.mostrar_ao_subir = False
        log.info("janela iniciada (pid %s)", self.proc.pid)

    def viva(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def enviar(self, comando: str) -> None:
        """Só no loop do núcleo."""
        if not self.viva():
            if comando == "mostrar" and not self.encerrando:
                self.desistiu = False
                self.mostrar_ao_subir = True
                self._iniciar()
            return
        try:
            assert self.proc is not None and self.proc.stdin is not None
            self.proc.stdin.write(comando + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            log.warning("janela não recebeu '%s'", comando)

    async def supervisionar(self) -> None:
        self._iniciar()
        while not self.encerrando:
            await asyncio.sleep(2)
            if self.encerrando or self.desistiu or self.viva():
                continue
            agora = time.monotonic()
            self.quedas.append(agora)
            if len(self.quedas) == self.quedas.maxlen and agora - self.quedas[0] < 60:
                self.desistiu = True  # volta a tentar quando você pedir para abrir
                log.error("a janela caiu 5 vezes em 1 minuto; veja data/logs/janela.log")
                continue
            log.warning("a janela fechou sozinha (código %s); subindo de novo", self.proc.returncode if self.proc else "?")
            self._iniciar()

    def encerrar(self) -> None:
        self.encerrando = True
        if not self.viva():
            return
        self.enviar("sair")
        assert self.proc is not None
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# ------------------------------------------------------------------ o núcleo


class Nucleo:
    def __init__(self, cfg: config.Config, *, abrir_janela: bool = False, com_voz: bool = True):
        self.cfg = cfg
        self.nome = cfg.get("assistente.nome", "Vision")
        self.porta = int(cfg.get("servidor.porta", 8765))
        self.token = secrets.token_urlsafe(32)
        self.barramento = Barramento()
        self.abrir_ao_subir = abrir_janela
        self.com_voz = com_voz
        self.j: Any = None
        self.agenda: Any = None
        self.laco: Any = None
        self.janela: Janela | None = None
        self.bandeja: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.parar: asyncio.Event | None = None

    # ---------- o que a tela pode pedir ----------

    def _controle(self):
        from jarvis.server import Controle

        async def texto(t: str) -> None:
            await self.j.agente.responder(t, "texto", "tela")

        async def confirmar(pid: str, sim: bool) -> None:
            await self.j.agente.resolver_pendente(pid, sim)

        def ouvir(segurando: bool) -> None:
            if segurando and self.laco is not None:
                self.laco.apertou_atalho()

        def parar_fala() -> None:
            if self.laco is not None:
                self.laco.saida.interromper.set()

        return Controle(texto=texto, confirmar=confirmar, ouvir=ouvir, parar_fala=parar_fala,
                        abrir_janela=self.abrir_janela, painel=self.painel)

    def abrir_janela(self) -> None:
        if self.janela is not None:
            self.janela.enviar("mostrar")

    async def painel(self) -> dict[str, Any]:
        """Foto da tela: agenda de hoje, memórias recentes e status."""
        from jarvis.google_login import dias_desde_login

        agenda: list[dict[str, Any]] = []
        if self.agenda is not None:
            try:
                agenda = await asyncio.wait_for(self.agenda.hoje(), 10)
            except Exception as e:  # noqa: BLE001 - sem agenda (login vencido, rede) a tela abre do mesmo jeito
                log.warning("agenda indisponível para o painel: %s", e)
        memorias = []
        if self.j.memorias is not None:
            memorias = [{"id": m.id, "texto": m.texto} for m in reversed(self.j.memorias.todas())][:30]
        dias = dias_desde_login(self.cfg)
        status = {
            "modelo": self.j.agente.llm.modelo,
            "vram": await self._vram(),
            "microfone": self.laco.entrada.nome if self.laco is not None else "voz desligada",
            "googleDias": None if dias is None else max(0, math.ceil(7 - dias)),
            "modoJogo": bool(self.laco is not None and self.laco.jogando),
        }
        return {"tipo": "painel", "nome": self.nome, "agenda": agenda, "memorias": memorias, "status": status}

    async def _vram(self) -> str:
        import ollama

        try:
            ps = await asyncio.wait_for(ollama.AsyncClient(self.cfg.get("modelo.host", "http://127.0.0.1:11434")).ps(), 3)
        except Exception:  # noqa: BLE001
            return "—"
        total = sum(getattr(m, "size_vram", 0) or 0 for m in ps.models)
        return f"{total / 1e9:.1f} GB" if total else "modelo fora da VRAM"

    # ---------- eventos → bandeja e bolha ----------

    def _ao_evento(self, ev: dict[str, Any]) -> None:
        tipo = ev.get("tipo")
        if tipo == "estado":
            if self.bandeja is not None:
                self.bandeja.mostrar_estado(ev.get("valor", "ocioso"))
            # Falou com a janela fechada: a bolha mostra o que foi ouvido e respondido (nunca durante o jogo).
            if ev.get("valor") == "ouvindo" and self.janela is not None and not (self.laco and self.laco.jogando):
                self.janela.enviar("bolha")
        elif tipo == "aviso" and ev.get("texto") and self.bandeja is not None:
            self.bandeja.avisar(ev["texto"])

    async def _atualizar_painel(self) -> None:
        while True:
            await asyncio.sleep(ATUALIZAR_PAINEL_S)
            if self.barramento.assinantes:
                self.barramento.publicar(await self.painel())

    # ---------- bandeja (thread própria: tudo volta ao loop por call_soon_threadsafe) ----------

    def _no_loop(self, fn, *args) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(fn, *args)

    def _iniciar_bandeja(self) -> None:
        from jarvis.bandeja import Acoes, Bandeja

        flag = self.cfg.dados / "dormindo.flag"

        def alternar_escuta() -> None:
            if flag.exists():
                flag.unlink(missing_ok=True)
            else:
                flag.touch()
            if self.laco is not None:
                self._no_loop(self.laco.reavaliar_estado)
            self.bandeja.atualizar_menu()

        def alternar_inicio() -> None:
            try:
                if inicializacao.ativo():
                    inicializacao.desligar()
                else:
                    inicializacao.ligar(self.cfg.raiz)
            except Exception as e:  # noqa: BLE001
                log.exception("não consegui mudar o início com o Windows")
                self.bandeja.avisar(f"Não consegui mudar o início com o Windows: {e}")
            self.bandeja.atualizar_menu()

        acoes = Acoes(
            abrir=lambda: self._no_loop(self.abrir_janela),
            falar_agora=lambda: self._no_loop(self.laco.apertou_atalho) if self.laco is not None else None,
            alternar_escuta=alternar_escuta,
            escuta_pausada=flag.exists,
            alternar_inicio=alternar_inicio,
            inicia_com_windows=inicializacao.ativo,
            sair=lambda: self._no_loop(self.parar.set),
        )
        self.bandeja = Bandeja(self.nome, acoes)
        self.bandeja.iniciar()

    # ---------- rodar ----------

    async def rodar(self) -> None:
        import uvicorn

        from jarvis.google_login import aviso_login
        from jarvis.montagem import montar
        from jarvis.server import criar_app, gravar_acesso
        from jarvis.tools.agenda import Agenda
        from jarvis.voice.wake import Atalho

        self.loop = asyncio.get_running_loop()
        self.parar = asyncio.Event()
        self.barramento.ligar(self.loop)
        self.barramento.ouvir(self._ao_evento)
        self._iniciar_bandeja()
        tarefas: list[asyncio.Task] = []
        acesso: Path | None = None
        with contextlib.ExitStack() as pilha:
            pilha.callback(self.bandeja.parar)
            async with montar(self.cfg) as j:
                self.j = j
                j.agente.ao_evento = self.barramento.publicar
                if self.cfg.get("agenda.servidor") in j.host.conexoes:
                    self.agenda = Agenda(self.cfg, j.host)

                app = criar_app(self.cfg, j, token=self.token, barramento=self.barramento,
                                controle=self._controle(), pasta_app=self.cfg.raiz / "ui" / "dist")
                servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.porta,
                                                         log_level="warning", log_config=None))
                tarefa_servidor = asyncio.create_task(servidor.serve())
                for _ in range(100):
                    if servidor.started or tarefa_servidor.done():
                        break
                    await asyncio.sleep(0.1)
                if not servidor.started:
                    self.bandeja.avisar(f"A porta {self.porta} está ocupada (outro `jarvis servidor`?). Veja o log.")
                    raise RuntimeError(f"servidor não subiu na porta {self.porta}")
                acesso = gravar_acesso(self.cfg, self.porta, self.token)
                log.info("servidor em http://127.0.0.1:%s", self.porta)

                self.janela = Janela(self.cfg, f"http://127.0.0.1:{self.porta}", self.token, self.abrir_ao_subir)
                tarefas.append(asyncio.create_task(self.janela.supervisionar()))
                tarefas.append(asyncio.create_task(self._atualizar_painel()))
                atalho = Atalho(self.cfg.get("nucleo.atalho_janela", "ctrl+alt+k"),
                                lambda: self._no_loop(self.abrir_janela))
                pilha.callback(atalho.fechar)

                await asyncio.sleep(0.2)  # a janela começa a subir antes: carregar a voz trava o loop por uns segundos
                if self.com_voz:
                    await self._subir_voz(pilha, tarefas)
                if aviso := aviso_login(self.cfg):
                    self.barramento.publicar({"tipo": "aviso", "texto": aviso})
                log.info("%s pronto", self.nome)
                try:
                    await self.parar.wait()
                finally:
                    log.info("encerrando")
                    for t in tarefas:
                        t.cancel()
                    self.janela.encerrar()
                    servidor.should_exit = True
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(tarefa_servidor, 5)
                    if acesso is not None:
                        acesso.unlink(missing_ok=True)

    async def _subir_voz(self, pilha: contextlib.ExitStack, tarefas: list[asyncio.Task]) -> None:
        from jarvis.voice.loop import preparar_voz, vigiar_jogos_se_ligado

        try:
            self.laco, _ = pilha.enter_context(preparar_voz(
                self.cfg, self.j.agente, escrever=log.info, ao_evento=self.barramento.publicar))
        except Exception as e:  # noqa: BLE001 - sem voz, a tela e a bandeja continuam
            log.exception("voz indisponível")
            self.barramento.publicar({"tipo": "aviso", "texto": f"Voz desligada: {e}"})
            return
        tarefas.append(asyncio.create_task(self._rodar_voz()))
        if (jogos := vigiar_jogos_se_ligado(self.cfg, self.laco)) is not None:
            tarefas.append(jogos)

    async def _rodar_voz(self) -> None:
        try:
            await self.laco.rodar()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("o laço de voz caiu")
            self.barramento.publicar({"tipo": "aviso", "texto": "A voz parou por um erro; veja data/logs/nucleo.log."})


def main(argv: list[str] | None = None, *, console: bool = False) -> int:
    """`jarvisw` (sem console) e `jarvis nucleo` (console=True)."""
    argv = sys.argv[1:] if argv is None else argv
    cfg = config.carregar()
    arquivo_log = configurar_log(cfg, console=console)
    abrir = "--abrir" in argv
    unica = InstanciaUnica()
    if not unica.pegar():
        ok = pedir_janela(cfg)
        log.info("já existe um núcleo rodando; %s", "janela aberta" if ok else "não consegui falar com ele")
        return 0 if ok else 1
    try:
        if inicializacao.primeira_vez(cfg.dados / "inicializacao.txt", cfg.raiz):
            log.info("início com o Windows ligado: %s", inicializacao.atalho())
    except Exception:  # noqa: BLE001 - sem o atalho, o resto funciona
        log.exception("não consegui criar o atalho de inicialização")
    log.info("núcleo iniciando (log em %s)", arquivo_log)
    try:
        asyncio.run(Nucleo(cfg, abrir_janela=abrir, com_voz="--sem-voz" not in argv).rodar())
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        log.exception("o núcleo caiu")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
