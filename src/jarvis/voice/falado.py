"""Deixa o texto do Jarvis bom de ouvir: '14:30' → '14 e 30', 'sex 02/10' → 'sexta, dia 2 de outubro',
'R$ 52,90' → '52 reais e 90 centavos', tira markdown, emojis e ids."""

from __future__ import annotations

import re

from jarvis.tempo import MESES

DIAS_POR_EXTENSO = {
    "seg": "segunda", "ter": "terça", "qua": "quarta", "qui": "quinta", "sex": "sexta", "sáb": "sábado", "dom": "domingo",
}

EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")

# Crase e preposição certas depois de virar texto: "às meio-dia" → "ao meio-dia", "às 1 hora" → "à 1 hora".
PREPOSICOES = [
    (r"\b(?:à|a)s meio-dia\b", "ao meio-dia"),
    (r"\bdas meio-dia\b", "do meio-dia"),
    (r"\bàs meia-noite\b", "à meia-noite"),
    (r"\bdas meia-noite\b", "da meia-noite"),
    (r"\bàs 1 (hora|e)\b", r"à 1 \1"),
    (r"\bdas 1 (hora|e)\b", r"da 1 \1"),
]


def hora_falada(h: int, mi: int) -> str:
    if mi == 0:
        if h == 12:
            return "meio-dia"
        if h == 0:
            return "meia-noite"
        return "1 hora" if h == 1 else f"{h} horas"
    return f"{h} e {mi}"


def _data(m: re.Match[str]) -> str:
    dia, mes = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        return m.group(0)
    return f"dia {dia} de {MESES[mes - 1]}"


def _reais(m: re.Match[str]) -> str:
    n = int(m.group(1).replace(".", ""))
    texto = "um real" if n == 1 else f"{n} reais"
    if m.group(2) and int(m.group(2)):
        texto += f" e {int(m.group(2))} centavos"
    return texto


def para_fala(texto: str) -> str:
    t = texto
    t = re.sub(r"\(\s*id:[^)]*\)|\bid:\s*\S+", "", t)            # ids de evento
    t = re.sub(r"[*_`#>]+", "", t)                               # markdown
    t = EMOJIS.sub("", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.MULTILINE)         # marcadores de lista
    t = re.sub(r"R\$\s*([\d.]+)(?:,(\d{2}))?", _reais, t)
    t = re.sub(r"\b(seg|ter|qua|qui|sex|sáb|dom)\s+(?=\d{1,2}/\d{1,2})", lambda m: DIAS_POR_EXTENSO[m.group(1)] + ", ", t)
    t = re.sub(r"\b(?:dia\s+)?(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\b", _data, t)
    t = re.sub(r"\b(\d{1,2})[:h](\d{2})\b", lambda m: hora_falada(int(m.group(1)), int(m.group(2))), t)
    t = re.sub(r"\b(\d{1,2})h\b", lambda m: hora_falada(int(m.group(1)), 0), t)
    for padrao, troca in PREPOSICOES:
        t = re.sub(padrao, troca, t)
    t = re.sub(r"\s*–\s*|\s+-\s+", " até ", t)
    t = t.replace("\n", ". ")
    t = re.sub(r"\s+([.,!?])", r"\1", t)
    t = re.sub(r"([.!?])\s*\.", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def frases(texto: str) -> list[str]:
    """Quebra em frases para o TTS começar a falar antes do texto todo chegar."""
    partes = re.split(r"(?<=[.!?;:])\s+", texto.strip())
    return [p for p in partes if p]
