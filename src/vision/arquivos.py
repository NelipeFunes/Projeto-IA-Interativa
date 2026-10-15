"""JSON em data/ com gravação atômica: um arquivo de sessão nunca fica pela metade (Alexa, TV)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def ler_json(arquivo: Path) -> Any:
    """O conteúdo, ou None se o arquivo não existe ou está estragado."""
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("%s ilegível", arquivo.name)
        return None


def gravar_json(arquivo: Path, dados: Any, *, duravel: bool = True) -> None:
    """Grava por um arquivo temporário e troca (nunca fica pela metade). `duravel=False` (cache): compacto e sem
    esperar o disco confirmar: arquivo ruim já vale cache vazio, e a escrita roda no laço de eventos."""
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    tmp = arquivo.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline=None if duravel else "") as f:
            f.write(json.dumps(dados, ensure_ascii=False, indent=1 if duravel else None))
            if duravel:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, arquivo)
    except BaseException:
        tmp.unlink(missing_ok=True)  # não deixa .tmp para trás
        raise


def limitar_log(arquivo: Path, maximo_bytes: int = 1_000_000, copias: int = 1) -> None:
    """Antes de abrir um log para escrever: se já passou de `maximo_bytes`, vira `.1` (e o `.1` vira `.2`, até
    `copias`). Só o `nucleo.log` rodava; `janela.log` e `mcp-*.log` cresciam para sempre."""
    try:
        if not arquivo.exists() or arquivo.stat().st_size <= maximo_bytes:
            return
        for i in range(copias, 0, -1):
            velho = arquivo.with_name(f"{arquivo.name}.{i}")
            origem = arquivo if i == 1 else arquivo.with_name(f"{arquivo.name}.{i - 1}")
            if origem.exists():
                os.replace(origem, velho)
    except OSError as e:
        log.warning("não deu para girar o log %s: %s", arquivo.name, e)


def podar_datados(pasta: Path, dias: int, hoje: date | None = None) -> int:
    """Apaga os arquivos `AAAA-MM-DD*` de `pasta` com mais de `dias` dias (conversas, medições). 0 dias = não apaga."""
    if dias <= 0 or not pasta.is_dir():
        return 0
    corte = (hoje or date.today()) - timedelta(days=dias)
    apagados = 0
    for arquivo in pasta.iterdir():
        try:
            if arquivo.is_file() and date.fromisoformat(arquivo.name[:10]) < corte:
                arquivo.unlink()
                apagados += 1
        except (ValueError, OSError):
            continue
    return apagados
