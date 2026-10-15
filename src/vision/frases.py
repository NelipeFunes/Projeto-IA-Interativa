"""Texto de comando de voz: tirar acento, pontuação e espaço sobrando, em um lugar só.

`normalizar` morava em `tools/pc.py` e 7 módulos o importavam de lá; o trecho `re.sub(...)` de `frase_limpa` estava
copiado em 8 pontos (ajuda, briefing, pc, sistema, spotify x2, timer, tv).
"""

from __future__ import annotations

import re
import unicodedata


def normalizar(t: str) -> str:
    """Minúsculas, sem acento, sem pontuação (exceto `. + -`) e com espaços simples."""
    t = "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.+-]", " ", t)).strip()


def frase_limpa(frase: str) -> str:
    """`normalizar` e mais: tira também o ponto, para casar frases de comando ("toca queen.", "volume 30!")."""
    return re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
