"""Núcleo do assistente: roda em segundo plano (sem console), inicia com o Windows e junta num processo só:

- agente + MCPs + memória (montagem.montar);
- voz ("Hey Vision" abre a conversa, "Vision, standby" fecha; atalho), se os modelos de voz estiverem baixados;
- servidor local (API + WebSocket da tela, com token), só em 127.0.0.1;
- ícone na bandeja, com o menu;
- a janela, que é OUTRO processo (pywebview quer a thread principal; se ela travar, a voz continua).
  O núcleo sobe a janela escondida, reinicia se ela cair e manda comandos pela entrada padrão dela.

Entradas: `visionw` (sem console, é o que o atalho de inicialização roda) e `vision nucleo` (com console,
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
import socket
import subprocess
import queue
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from vision import ajustes, config, conexoes, inicializacao
from vision.eventos import Barramento
from vision.voice.loop import FALA_DE_ERRO

log = logging.getLogger("vision.nucleo")

NOME_MUTEX = "Local\\VisionNucleo"
ESPERA_REINICIO_S = 20  # o núcleo novo espera o velho soltar o mutex (e a porta) ao reiniciar pela tela
# A agenda muda por fora (celular): a cada 5 min o cache dela é refeito e a tela recebe uma foto nova.
ATUALIZAR_PAINEL_S = 300
VIGIAR_SISTEMA_S = 120  # placa quente, memória ou disco no fim: avisa (uma vez por hora cada um)
CHECAR_AVISOS_S = 30  # compromisso chegando: olha o cache da agenda a cada 30 s


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
        if bool(self.handle) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return True
        self.soltar()  # o handle de um mutex que já era de outro núcleo não segura nada
        return False

    def soltar(self) -> None:
        if sys.platform == "win32" and self.handle:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CloseHandle.argtypes = (ctypes.c_void_p,)
            k32.CloseHandle(self.handle)
        self.handle = None


def nucleo_rodando(nome: str = NOME_MUTEX) -> bool:
    """Só olha se o mutex existe, sem criar (o `vision servidor` usa para não brigar com o núcleo)."""
    if sys.platform != "win32":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = ctypes.c_void_p
    k32.OpenMutexW.argtypes = (ctypes.c_ulong, ctypes.c_bool, ctypes.c_wchar_p)
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    SYNCHRONIZE = 0x00100000
    h = k32.OpenMutexW(SYNCHRONIZE, False, nome)
    if h:
        k32.CloseHandle(h)
    return bool(h)


def ler_acesso(cfg: config.Config) -> dict[str, Any] | None:
    try:
        acesso = json.loads((cfg.dados / "nucleo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(acesso, dict) and "porta" in acesso and "token" in acesso):
        return None
    import psutil

    # Arquivo velho de um núcleo que morreu (queda de energia): não vale.
    return acesso if psutil.pid_exists(int(acesso.get("pid", -1))) else None


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
        # Os comandos vão para o stdin da janela por uma thread: se a janela travar e o pipe encher, quem
        # espera é essa thread, não o loop do núcleo (voz e servidor continuam).
        self.fila: queue.Queue[tuple[subprocess.Popen, str]] = queue.Queue(maxsize=100)
        threading.Thread(target=self._escritor, name="janela-stdin", daemon=True).start()

    def _iniciar(self) -> None:
        env = {**os.environ, "VISION_URL": self.url, "VISION_TOKEN": self.token,
               "VISION_MOSTRAR": "1" if self.mostrar_ao_subir else "0"}
        saida = (self.cfg.dados / "logs" / "janela.log").open("a", encoding="utf-8")
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "vision.cli", "interface", "--nucleo"],
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
        assert self.proc is not None
        try:
            self.fila.put_nowait((self.proc, comando))
        except queue.Full:
            log.warning("janela parada: '%s' descartado", comando)

    def _escritor(self) -> None:
        while True:
            proc, comando = self.fila.get()
            try:
                assert proc.stdin is not None
                proc.stdin.write(comando + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
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
        self.reiniciar_ao_sair = False
        # Conexões: o que o Vision montou ao iniciar (mudou depois, a tela pede para reiniciar), os logins em
        # andamento (um por serviço) e a última frase de cada um para a tela.
        self.conexoes_inicio = conexoes.assinatura(cfg)
        self.logins: dict[str, tuple[asyncio.Task, threading.Event]] = {}
        self.andamento: dict[str, dict[str, Any]] = {}

    # ---------- o que a tela pode pedir ----------

    def _controle(self):
        from vision.server import Controle

        async def texto(t: str) -> None:
            try:
                await self.j.agente.responder(t, "texto", "tela")
            except Exception:  # noqa: BLE001 - Ollama fora do ar: a tela recebe uma resposta, não fica esperando
                log.exception("o agente falhou numa mensagem da tela")
                self.barramento.publicar({"tipo": "resposta", "texto": FALA_DE_ERRO})

        async def confirmar(pid: str, sim: bool) -> None:
            try:
                canal = self.j.agente.canal_da_pendente(pid)
                r = await self.j.agente.resolver_pendente(pid, sim)
                if r is not None and canal == "voz" and self.laco is not None:
                    self.laco.pedir_fala(r.texto)  # pedido por voz, confirmado na tela: a resposta também é falada
            except Exception:  # noqa: BLE001
                log.exception("o agente falhou ao confirmar pela tela")
                self.barramento.publicar({"tipo": "resposta", "texto": FALA_DE_ERRO})

        def ouvir(segurando: bool) -> None:
            if segurando and self.laco is not None:
                self.laco.apertou_atalho()

        def parar_fala() -> None:
            if self.laco is not None:
                self.laco.saida.interromper.set()

        return Controle(texto=texto, confirmar=confirmar, ouvir=ouvir, parar_fala=parar_fala,
                        abrir_janela=self.abrir_janela, painel=self.painel, ler_ajustes=self.ler_ajustes,
                        salvar_ajustes=self.salvar_ajustes, amostra_voz=self.amostra_voz,
                        ler_conexoes=self.ler_conexoes, conectar=self.conectar, desconectar=self.desconectar,
                        ligar_conexao=self.ligar_conexao, cancelar_conexao=self.cancelar_conexao,
                        reiniciar=self.reiniciar)

    # ---------- tela de conexões ----------

    async def ler_conexoes(self) -> None:
        status = self.j.host.status() if self.j is not None else {}
        servicos = await asyncio.to_thread(conexoes.estado, self.cfg, status)
        reiniciar = await asyncio.to_thread(conexoes.mudou_desde, self.cfg, self.conexoes_inicio)
        self.barramento.publicar({"tipo": "conexoes", "servicos": servicos, "andamento": dict(self.andamento),
                                  "reiniciar": reiniciar})

    def _andamento(self, servico: str, texto: str, *, rodando: bool, ok: bool | None = None) -> None:
        self.andamento[servico] = {"texto": str(texto)[:500], "rodando": rodando, "ok": ok}

    async def conectar(self, servico: str, dados: dict[str, Any]) -> None:
        if servico in self.logins:
            return  # já tem um login deste serviço em andamento: a tela mostra o dele
        try:
            limpos = conexoes.validar(servico, dados)
        except ValueError as e:
            self._andamento(str(servico)[:20], str(e), rodando=False, ok=False)
            await self.ler_conexoes()
            return
        cancelar = threading.Event()
        assert self.loop is not None
        loop = self.loop
        frases: list[str] = []  # a última vira o resultado ("Spotify ligado...", "E-mail inválido.")

        def aplicar(texto: str) -> None:
            if servico in self.logins:
                frases.append(texto)
                self._andamento(servico, texto, rodando=True)
                asyncio.ensure_future(self.ler_conexoes())

        def avisar(texto: str) -> None:
            try:
                no_loop = asyncio.get_running_loop() is loop
            except RuntimeError:
                no_loop = False
            if no_loop:  # Spotify, Alexa e Wispr: na hora (adiada, a última frase chegaria depois do fim)
                aplicar(texto)
            else:  # o login do Google roda numa thread
                loop.call_soon_threadsafe(aplicar, texto)

        async def rodar() -> None:
            try:
                ok = await conexoes.conectar(self.cfg, servico, limpos, avisar, cancelar)
                self._andamento(servico, frases[-1] if frases else ("Pronto." if ok else "Não deu certo."),
                                rodando=False, ok=ok)
                log.info("conexão %s: %s", servico, "feita" if ok else "não concluída")
            except asyncio.CancelledError:
                self._andamento(servico, "Cancelado.", rodando=False, ok=False)
                raise
            except Exception as e:  # noqa: BLE001 - credencial recusada, rede: a tela recebe a frase
                log.exception("falha ao conectar %s", servico)
                self._andamento(servico, f"Não deu certo: {e}", rodando=False, ok=False)
            finally:
                self.logins.pop(servico, None)
                with contextlib.suppress(Exception):
                    await self.ler_conexoes()

        self._andamento(servico, "Conectando…", rodando=True)
        self.logins[servico] = (asyncio.create_task(rodar()), cancelar)
        await self.ler_conexoes()

    async def cancelar_conexao(self, servico: str) -> None:
        if (login := self.logins.get(servico)) is not None:
            login[1].set()  # o do Google roda numa thread: ela vê o aviso e mata o Node
            login[0].cancel()

    async def desconectar(self, servico: str) -> None:
        if servico in self.logins or servico not in conexoes.POR_ID:
            return
        try:
            texto = await asyncio.to_thread(conexoes.desconectar, self.cfg, servico)
            self._andamento(servico, texto, rodando=False, ok=True)
            log.info("conexão %s: desconectada", servico)
        except (ValueError, OSError) as e:
            self._andamento(servico, f"Não consegui desconectar: {e}", rodando=False, ok=False)
        await self.ler_conexoes()

    async def ligar_conexao(self, servico: str, ligado: bool) -> None:
        s = conexoes.POR_ID.get(servico)
        if s is None:
            return
        config.salvar_ajustes(self.cfg, {s.chave_ligado: ligado})
        self._andamento(servico, "Ligado." if ligado else "Desligado.", rodando=False, ok=True)
        await self.ler_conexoes()

    def reiniciar(self) -> None:
        """Pela tela: encerra e sobe um núcleo novo (as conexões só são montadas ao iniciar)."""
        log.info("reiniciando a pedido da tela")
        self.reiniciar_ao_sair = True
        if self.parar is not None:
            self.parar.set()

    # ---------- tela de ajustes ----------

    async def ler_ajustes(self, **extra: Any) -> None:
        dados = await asyncio.to_thread(ajustes.ler, self.cfg)  # a lista de microfones consulta o áudio
        dados["valores"]["inicia_com_windows"] = inicializacao.ativo()
        self.barramento.publicar({"tipo": "ajustes", **dados, **extra})

    async def salvar_ajustes(self, valores: dict[str, Any]) -> None:
        inicio = valores.pop("inicia_com_windows", None)
        try:
            mudancas = await asyncio.to_thread(ajustes.validar, self.cfg, valores)  # consulta o áudio
        except ValueError as e:
            await self.ler_ajustes(erro=str(e))
            return
        reiniciar = ajustes.precisa_reiniciar(mudancas, self.cfg)
        voz_antes = (self.cfg.get("voz.voz_piper"), self.cfg.get("voz.velocidade_fala"))
        config.salvar_ajustes(self.cfg, mudancas)
        erros = []
        try:
            if isinstance(inicio, bool) and inicio != inicializacao.ativo():
                await asyncio.to_thread(inicializacao.ligar if inicio else lambda _p: inicializacao.desligar(),
                                        self.cfg.raiz)
        except Exception as e:  # noqa: BLE001 - o resto dos ajustes vale mesmo assim
            log.exception("não consegui mudar o início com o Windows")
            erros.append(f"início com o Windows: {e}")
        try:
            if self.laco is not None:
                self.laco.ajustar_silencio(float(self.cfg.get("voz.conversa_silencio_max_s", 120)))
                if (self.cfg.get("voz.voz_piper"), self.cfg.get("voz.velocidade_fala")) != voz_antes:
                    from vision.voice.tts import VozXTTS, carregar_piper, carregar_voz

                    voz = self.laco.voz
                    if isinstance(voz, VozXTTS):  # a voz do Piper é só a reserva: não recarrega o XTTS (14 s)
                        voz.reserva = await asyncio.to_thread(carregar_piper, self.cfg)
                        voz.velocidade = 1.0 / max(float(self.cfg.get("voz.velocidade_fala", 1.0)), 0.1)
                    else:
                        self.laco.voz = await asyncio.to_thread(carregar_voz, self.cfg)
        except Exception as e:  # noqa: BLE001
            log.exception("não consegui aplicar os ajustes")
            erros.append(f"voz: {e}")
        erro = f"Salvei, mas não consegui aplicar agora: {'; '.join(erros)}" if erros else None
        log.info("ajustes salvos: %s", ", ".join(sorted(mudancas)) or "(início com o Windows)")
        await self.ler_ajustes(salvo=True, reiniciar=reiniciar, **({"erro": erro} if erro else {}))

    async def amostra_voz(self, nome: str) -> None:
        if self.laco is None or nome not in ajustes.vozes(self.cfg):
            return
        from vision.voice.tts import Voz

        voz = await asyncio.to_thread(Voz, self.cfg.modelos / "piper" / f"{nome}.onnx",
                                      float(self.cfg.get("voz.velocidade_fala", 1.0)))
        self.laco.pedir_fala(f"Oi, Felipe. Esta é a voz {nome.split('-')[1]}.", voz)

    def abrir_janela(self) -> None:
        if self.janela is not None:
            self.janela.enviar("mostrar")

    async def painel(self) -> dict[str, Any]:
        """Foto da tela: agenda de hoje, memórias recentes e status."""
        from vision.google_login import dias_desde_login

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
            await self._atualizar_agenda()
            await asyncio.sleep(ATUALIZAR_PAINEL_S)
            await self._atualizar_agenda()
            if not self.barramento.assinantes:
                continue
            try:
                self.barramento.publicar(await self.painel())
            except Exception:  # noqa: BLE001 - uma foto que falha não pode parar as próximas
                log.exception("não consegui atualizar o painel")

    async def _vigiar_sistema(self) -> None:
        """Como o JARVIS avisando da armadura: placa de vídeo quente, memória ou disco no fim viram aviso."""
        from vision import sistema

        if not self.cfg.get("sistema.alertas", True):
            return
        vigia = sistema.Vigia()
        while True:
            await asyncio.sleep(VIGIAR_SISTEMA_S)
            if self.laco is not None and self.laco.jogando:
                continue  # no jogo, nem um processo a mais: o CS2 já disputa o processador
            try:
                novos = vigia.novos(await sistema.estado())
            except Exception:  # noqa: BLE001 - sem leitura agora, tenta na próxima
                log.exception("não consegui ler o estado do PC")
                continue
            if novos:
                frase = f"{self.cfg.get('usuario.nome', 'Felipe')}, atenção: " + " ".join(novos)
                log.warning("alerta do sistema: %s", frase)
                self.barramento.publicar({"tipo": "aviso", "texto": frase})
                if self.laco is not None:
                    self.laco.pedir_fala(frase)  # no jogo, fica só na tela
    async def _avisar_compromissos(self) -> None:
        """Como o JARVIS: alguns minutos antes de cada compromisso, fala e mostra o aviso, sem ninguém perguntar."""
        from vision.avisos import AvisosDaAgenda

        try:
            antes = float(self.cfg.get("agenda.avisar_antes_min", 10) or 0)
        except (TypeError, ValueError):
            log.warning("agenda.avisar_antes_min inválido; usando 10")
            antes = 10.0
        if self.agenda is None or antes <= 0:
            return
        avisos = AvisosDaAgenda(self.agenda, antes, self.cfg.get("usuario.nome", "Felipe"))
        while True:
            await asyncio.sleep(CHECAR_AVISOS_S)
            try:
                for frase in await avisos.checar():
                    log.info("aviso de compromisso")
                    self.barramento.publicar({"tipo": "aviso", "texto": frase})
                    if self.laco is not None:
                        self.laco.pedir_fala(frase)  # no jogo ou com a escuta pausada, fica só na tela
            except Exception:  # noqa: BLE001 - um aviso que falha não para os próximos
                log.exception("falha nos avisos de compromisso")

    async def _atualizar_agenda(self) -> None:
        """Refaz o cache da agenda (o "o que tenho amanhã?" responde sem esperar o Google)."""
        if self.agenda is None:
            return
        try:
            await asyncio.wait_for(self.agenda.atualizar_cache(), 20)
        except Exception as e:  # noqa: BLE001 - sem rede ou sem login: as consultas vão direto ao Google
            log.warning("não deu para atualizar o cache da agenda: %s", e)

    # ---------- bandeja (thread própria: tudo volta ao loop por call_soon_threadsafe) ----------

    def _no_loop(self, fn, *args) -> None:
        assert self.loop is not None
        self.loop.call_soon_threadsafe(fn, *args)

    def _iniciar_bandeja(self) -> None:
        from vision.bandeja import Acoes, Bandeja

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

        from vision.google_login import aviso_login
        from vision.montagem import montar
        from vision.server import criar_app, gravar_acesso
        from vision.voice.wake import Atalho

        self.loop = asyncio.get_running_loop()
        self.parar = asyncio.Event()
        self.barramento.ligar(self.loop)
        self.barramento.ouvir(self._ao_evento)
        self._iniciar_bandeja()
        tarefas: list[asyncio.Task] = []
        acesso: Path | None = None
        with contextlib.ExitStack() as pilha:
            pilha.callback(self.bandeja.parar)
            async with montar(self.cfg, ao_disparar_timer=self._timer_acabou) as j:
                self.j = j
                j.agente.ao_evento = self.barramento.publicar
                self.agenda = j.agenda  # a mesma do agente: um cache só para a tela e para as perguntas

                app = criar_app(self.cfg, j, token=self.token, barramento=self.barramento,
                                controle=self._controle(), pasta_app=self.cfg.raiz / "ui" / "dist")
                # A porta é aberta aqui: ocupada, vira aviso na bandeja. (Dentro do uvicorn, ele faria
                # sys.exit no meio da tarefa e o núcleo sumiria sem dizer nada.)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind(("127.0.0.1", self.porta))
                except OSError as e:
                    sock.close()
                    self.bandeja.avisar(f"A porta {self.porta} está ocupada (outro `vision servidor`?).")
                    await asyncio.sleep(3)  # dá tempo de a notificação aparecer
                    raise RuntimeError(f"porta {self.porta} ocupada") from e
                pilha.callback(sock.close)
                servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.porta,
                                                         log_level="warning", log_config=None,
                                                         timeout_graceful_shutdown=2))  # sem esperar a janela fechar o /ws
                tarefa_servidor = asyncio.create_task(servidor.serve(sockets=[sock]))
                for _ in range(100):
                    if servidor.started or tarefa_servidor.done():
                        break
                    await asyncio.sleep(0.1)
                if not servidor.started:
                    self.bandeja.avisar(f"A porta {self.porta} está ocupada (outro `vision servidor`?). Veja o log.")
                    raise RuntimeError(f"servidor não subiu na porta {self.porta}")
                acesso = gravar_acesso(self.cfg, self.porta, self.token)
                log.info("servidor em http://127.0.0.1:%s", self.porta)

                self.janela = Janela(self.cfg, f"http://127.0.0.1:{self.porta}", self.token, self.abrir_ao_subir)
                tarefas.append(asyncio.create_task(self.janela.supervisionar()))
                tarefas.append(asyncio.create_task(self._atualizar_painel()))
                tarefas.append(asyncio.create_task(self._vigiar_sistema()))
                tarefas.append(asyncio.create_task(self._avisar_compromissos()))
                atalho = Atalho(self.cfg.get("nucleo.atalho_janela", "ctrl+alt+k"),
                                lambda: self._no_loop(self.abrir_janela))
                pilha.callback(atalho.fechar)

                await asyncio.sleep(0.2)  # a janela começa a subir antes: carregar a voz trava o loop por uns segundos
                if self.com_voz:
                    await self._subir_voz(pilha, tarefas)
                if aviso := aviso_login(self.cfg):
                    self.barramento.publicar({"tipo": "aviso", "texto": aviso})
                if self.laco is None or not self.cfg.get("modo_jogo.ativo", True):  # com voz, quem pré-carrega é o vigia do jogo, depois de olhar se tem jogo aberto
                    tarefas.append(asyncio.create_task(j.agente.carregar()))  # a 1ª pergunta não espera o modelo
                log.info("%s pronto", self.nome)
                try:
                    await self.parar.wait()
                finally:
                    log.info("encerrando")
                    for t in tarefas:
                        t.cancel()
                    for t, cancelar in list(self.logins.values()):
                        cancelar.set()
                        t.cancel()
                    self.janela.encerrar()
                    servidor.should_exit = True
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(tarefa_servidor, 5)
                    if acesso is not None:
                        acesso.unlink(missing_ok=True)

    def _timer_acabou(self, t: Any) -> None:
        """Fim de timer: alarme e fala (pelo laço de voz) e aviso na bandeja e na tela."""
        texto = t.aviso()
        self.barramento.publicar({"tipo": "aviso", "texto": texto})
        if self.laco is not None:
            self.laco.pedir_alarme(texto)

    async def _subir_voz(self, pilha: contextlib.ExitStack, tarefas: list[asyncio.Task]) -> None:
        from vision.voice.loop import preparar_voz, vigiar_jogos_se_ligado
        from vision.voice.tts import carregar_voz

        try:
            # O XTTS leva 15–30 s para carregar: numa thread, para a tela e a bandeja não travarem.
            voz = await asyncio.to_thread(carregar_voz, self.cfg)
            self.laco, _ = pilha.enter_context(preparar_voz(
                self.cfg, self.j.agente, escrever=escritor_do_log(self.nome), ao_evento=self.barramento.publicar,
                voz=voz))
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


def escritor_do_log(nome_assistente: str):
    """O que o laço de voz escreve vai para o log; o que você falou e a resposta ficam em debug (já estão em
    data/conversas/, não precisam de uma segunda cópia em texto no log)."""
    privadas = ("Você:", f"{nome_assistente}:")

    def escrever(linha: str) -> None:
        (log.debug if linha.startswith(privadas) else log.info)(linha)

    return escrever


def main(argv: list[str] | None = None, *, console: bool = False) -> int:
    """`visionw` (sem console) e `vision nucleo` (console=True)."""
    argv = sys.argv[1:] if argv is None else argv
    cfg = config.carregar()
    arquivo_log = configurar_log(cfg, console=console)
    abrir = "--abrir" in argv
    unica = InstanciaUnica()
    pegou = unica.pegar()
    if not pegou and "--reiniciando" in argv:
        # O núcleo velho ainda está fechando (a porta, a janela): espera ele sair em vez de só pedir a janela.
        fim = time.monotonic() + ESPERA_REINICIO_S
        while not pegou and time.monotonic() < fim:
            time.sleep(0.5)
            pegou = unica.pegar()
    if not pegou:
        ok = pedir_janela(cfg)
        log.info("já existe um núcleo rodando; %s", "janela aberta" if ok else "não consegui falar com ele")
        return 0 if ok else 1
    try:
        if inicializacao.primeira_vez(cfg.dados / "inicializacao.txt", cfg.raiz):
            log.info("início com o Windows ligado: %s", inicializacao.atalho())
    except Exception:  # noqa: BLE001 - sem o atalho, o resto funciona
        log.exception("não consegui criar o atalho de inicialização")
    log.info("núcleo iniciando (log em %s)", arquivo_log)
    nucleo = Nucleo(cfg, abrir_janela=abrir, com_voz="--sem-voz" not in argv)
    try:
        asyncio.run(nucleo.rodar())
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        log.exception("o núcleo caiu")
        return 1
    if nucleo.reiniciar_ao_sair:
        unica.soltar()
        subir_de_novo(cfg)
    return 0


def subir_de_novo(cfg: config.Config) -> None:
    """Sobe o núcleo novo, já com a janela. As variáveis que a tela grava no .env ficam de fora do ambiente
    herdado: o novo lê o .env de novo (load_dotenv não troca o que já veio do processo pai)."""
    exe, extra = inicializacao.alvo()
    env = {k: v for k, v in os.environ.items() if k not in conexoes.CHAVES_ENV}
    subprocess.Popen([str(exe), *extra.split(), "--abrir", "--reiniciando"], cwd=cfg.raiz, env=env, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.info("núcleo novo pedido (%s)", exe.name)


if __name__ == "__main__":
    sys.exit(main())
