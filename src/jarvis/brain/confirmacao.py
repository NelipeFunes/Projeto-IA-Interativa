"""Classifica a resposta do Felipe a uma confirmação: sim, não, ou outra coisa."""

from __future__ import annotations

import re
import unicodedata

SIM = {
    "sim", "s", "pode", "pode sim", "confirma", "confirmo", "confirmado", "isso", "isso mesmo", "ok", "okay",
    "beleza", "manda", "manda ver", "claro", "com certeza", "fechado", "bora", "positivo", "faz", "faz isso",
    "pode fazer", "pode criar", "pode apagar", "pode lancar", "pode mudar", "pode esquecer", "certo", "correto",
    "exato", "perfeito", "yes", "uhum", "aham", "sim pode", "sim por favor", "por favor", "vai", "segue",
}
NAO = {
    "nao", "n", "cancela", "cancelar", "deixa", "deixa pra la", "esquece", "esquece isso", "negativo", "para",
    "nao precisa", "melhor nao", "nao obrigado", "nao valeu", "deixa quieto", "nem", "nope",
}


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\b(jarvis|entao|ah|e|ai)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classificar(resposta: str) -> str:
    """'sim', 'nao' ou 'outro' (o Felipe mudou de assunto ou corrigiu algo)."""
    t = normalizar(resposta)
    if t in SIM:
        return "sim"
    if t in NAO:
        return "nao"
    palavras = t.split()
    if palavras and palavras[0] == "sim" and len(palavras) <= 4:
        return "sim"
    if palavras and palavras[0] == "nao" and len(palavras) <= 2:
        return "nao"
    return "outro"
