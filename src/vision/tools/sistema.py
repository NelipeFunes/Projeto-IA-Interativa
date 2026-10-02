"""Ferramenta do diagnóstico do PC (vision/sistema.py), com atalho para "status do sistema"."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from vision import sistema
from vision.tools.base import Ferramenta, esquema
from vision.tools.pc import normalizar

_PEDIDO = re.compile(
    r"^(?:(?:vision|visao|hey|ei|ok)\s+)*(?:me (?:da|de) (?:um|o) )?(?:status|diagnostico|relatorio)"
    r"(?: do| de)? (?:sistema|pc|computador|maquina)(?:\s+(?:vision|visao))?$"
    r"|^(?:(?:vision|visao)\s+)?como (?:esta|ta) (?:o )?(?:pc|computador|sistema)(?:\s+(?:vision|visao))?$"
)


def pede_status(frase: str) -> bool:
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    return bool(_PEDIDO.match(t))


async def status(_args: dict[str, Any] | None = None) -> str:
    return sistema.relatorio(await sistema.estado())


async def atalho(frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
    if not pede_status(frase):
        return None
    try:
        return "status_do_sistema", {}, await asyncio.wait_for(status(), 15), True
    except Exception:  # noqa: BLE001 - pelo atalho ninguém captura: vira resposta, não um turno quebrado
        return "status_do_sistema", {}, "Não consegui ler o estado do PC agora.", False


def ferramentas() -> list[Ferramenta]:
    return [Ferramenta(
        "status_do_sistema",
        "Diagnóstico do PC: uso do processador, memória, placa de vídeo (temperatura e VRAM), disco e há quanto "
        "tempo está ligado. Use para 'status do sistema', 'como está o PC', 'o PC está esquentando?'.",
        esquema([]), status, grupo="pc", prazo_s=15,
    )]
