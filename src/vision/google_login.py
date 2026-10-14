"""Login do Google Agenda (app em modo teste → o token vence a cada 7 dias)."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from vision import tempo
from vision.config import Config

ARQUIVO_DATA = "google-login.txt"


def _servidor(cfg: Config) -> dict:
    return cfg.get("mcp.google-calendar") or {}


PRAZO_LOGIN_S = 300


def arquivo_credenciais(cfg: Config) -> Path:
    """O JSON do app "Desktop" criado no Google Cloud (docs/guia-google-cloud.md)."""
    return cfg.caminho((_servidor(cfg).get("env") or {}).get("GOOGLE_OAUTH_CREDENTIALS", "data/google-oauth.json"))


def fazer_login(cfg: Config, avisar: Callable[[str], None] = print, *, sem_console: bool = False,
                cancelar: threading.Event | None = None) -> int:
    """`sem_console`: chamado pela tela do núcleo (sem terminal). O Node não abre janela preta, a saída dele
    é descartada e o login tem prazo e pode ser cancelado (sem isso, uma aba fechada deixaria o Node esperando)."""
    s = _servidor(cfg)
    credenciais = arquivo_credenciais(cfg)
    if not credenciais.exists():
        avisar(f"Falta o arquivo de credenciais: {credenciais}")
        avisar("Siga docs/guia-google-cloud.md (passos 1 a 6) e salve o JSON com esse nome.")
        return 1
    env = dict(os.environ, GOOGLE_OAUTH_CREDENTIALS=str(credenciais))
    comando = [str(cfg.caminho(s["comando"])), *[str(cfg.caminho(a)) for a in s.get("args", [])], "auth"]
    avisar("Abrindo o navegador para o login do Google... (aceite as permissões da agenda)")
    extra: dict = {}
    if sem_console:
        extra = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                 "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        proc = subprocess.Popen(comando, env=env, cwd=cfg.raiz, **extra)
    except OSError as e:
        avisar(f"Não consegui iniciar o Node do Google Agenda ({e.strerror or e}).")
        return 1
    prazo = time.monotonic() + PRAZO_LOGIN_S if sem_console else None
    try:
        while (codigo := proc.poll()) is None:
            if cancelar is not None and cancelar.is_set():
                avisar("Login do Google cancelado.")
                return 1
            if prazo is not None and time.monotonic() > prazo:
                avisar("O login do Google não foi concluído a tempo (5 min). Tente de novo.")
                return 1
            time.sleep(0.3)
    finally:
        if proc.poll() is None:  # cancelado, no prazo ou Ctrl+C: o Node não fica para trás
            proc.kill()
            proc.wait()
    if codigo == 0:
        (cfg.dados / ARQUIVO_DATA).write_text(tempo.agora().isoformat(timespec="seconds"), encoding="utf-8")
        avisar("Login feito. Vale por 7 dias (o Vision avisa no 6º).")
    else:
        avisar("O login do Google não deu certo. Tente de novo.")
    return codigo


def dias_desde_login(cfg: Config) -> float | None:
    arq = cfg.dados / ARQUIVO_DATA
    if not arq.exists():
        return None
    quando = datetime.fromisoformat(arq.read_text(encoding="utf-8").strip())
    return (tempo.agora() - quando).total_seconds() / 86400


def aviso_login(cfg: Config) -> str | None:
    """Frase para o Vision dizer ao iniciar, se o login estiver para vencer ou nunca foi feito."""
    if not _servidor(cfg).get("ativo", True):
        return None
    dias = dias_desde_login(cfg)
    limite = float(cfg.get("agenda.aviso_login_dias", 6))
    if dias is None:
        return "Felipe, a agenda do Google ainda não está conectada. Conecte nos Ajustes, em Conexões."
    if dias >= 7:
        return "Felipe, o login do Google venceu. Reconecte nos Ajustes, em Conexões."
    if dias >= limite:
        return "Felipe, o login do Google vence amanhã. Quando puder, reconecte nos Ajustes, em Conexões."
    return None
