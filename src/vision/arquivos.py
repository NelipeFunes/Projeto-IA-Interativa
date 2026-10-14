"""JSON em data/ com gravação atômica: um arquivo de sessão nunca fica pela metade (Alexa, TV)."""

from __future__ import annotations

import json
import logging
import os
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


def gravar_json(arquivo: Path, dados: Any) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    tmp = arquivo.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(dados, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, arquivo)
