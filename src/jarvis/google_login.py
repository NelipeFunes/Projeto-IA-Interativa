"""Login do Google Agenda (app em modo teste → o token vence a cada 7 dias)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from jarvis import tempo
from jarvis.config import Config

ARQUIVO_DATA = "google-login.txt"


def _servidor(cfg: Config) -> dict:
    return cfg.get("mcp.google-calendar") or {}


def fazer_login(cfg: Config) -> int:
    s = _servidor(cfg)
    credenciais = cfg.caminho((s.get("env") or {}).get("GOOGLE_OAUTH_CREDENTIALS", "data/google-oauth.json"))
    if not credenciais.exists():
        print(f"Falta o arquivo de credenciais: {credenciais}")
        print("Siga docs/guia-google-cloud.md (passos 1 a 6) e salve o JSON com esse nome.")
        return 1
    env = dict(os.environ, GOOGLE_OAUTH_CREDENTIALS=str(credenciais))
    comando = [str(cfg.caminho(s["comando"])), *[str(cfg.caminho(a)) for a in s.get("args", [])], "auth"]
    print("Abrindo o navegador para o login do Google... (aceite as permissões da agenda)")
    r = subprocess.run(comando, env=env, cwd=cfg.raiz)
    if r.returncode == 0:
        (cfg.dados / ARQUIVO_DATA).write_text(tempo.agora().isoformat(timespec="seconds"), encoding="utf-8")
        print("Login feito. Vale por 7 dias (o Jarvis avisa no 6º).")
    return r.returncode


def dias_desde_login(cfg: Config) -> float | None:
    arq = cfg.dados / ARQUIVO_DATA
    if not arq.exists():
        return None
    quando = datetime.fromisoformat(arq.read_text(encoding="utf-8").strip())
    return (tempo.agora() - quando).total_seconds() / 86400


def aviso_login(cfg: Config) -> str | None:
    """Frase para o Jarvis dizer ao iniciar, se o login estiver para vencer ou nunca foi feito."""
    if not _servidor(cfg).get("ativo", True):
        return None
    dias = dias_desde_login(cfg)
    limite = float(cfg.get("agenda.aviso_login_dias", 6))
    if dias is None:
        return "Felipe, a agenda do Google ainda não está conectada. Rode: .\\jarvis google-login"
    if dias >= 7:
        return "Felipe, o login do Google venceu. Rode: .\\jarvis google-login"
    if dias >= limite:
        return "Felipe, o login do Google vence amanhã. Quando puder, rode: .\\jarvis google-login"
    return None
