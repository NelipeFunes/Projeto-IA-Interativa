"""As duas frases que controlam a conversa por voz, reconhecidas no texto transcrito.

- "Hey Vision" (no começo da fala) abre a conversa. O que vier depois já é o pedido:
  "Hey Vision, o que eu tenho hoje?" abre e responde de uma vez.
- "Vision, standby" (ou "pode ficar em standby") fecha a conversa e volta a esperar o "Hey Vision". Também valem
  frases curtas com "dormir"/"encerrar" e "tchau, Vision".

Até 01/10 era "pode desligar", mas "desligar" também é apagar a luz: "não precisa de mais nada, pode desligar"
apagava as luzes e a conversa seguia aberta. "Standby" não se confunde com nenhum pedido.

O Parakeet ouve "Vision" de vários jeitos. Na calibração de 30/09, com vozes sintéticas, saíram
"Vision", "Visium", "E vision", "Eye vision" e "ValuVision". Por isso a comparação é tolerante,
mas só vale no começo da fala: "visita" ou "visual" no meio de uma frase não acordam ninguém.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

NOME = "vision"
# Grafias que o Parakeet já produziu para "Vision" (ou que um sotaque produz).
GRAFIAS = {"vision", "visium", "visiom", "vizion", "vizhon", "vishon", "visian", "vijon", "vission", "visio"}
# O que pode vir antes do nome: "Hey", como o Parakeet escreve, e cumprimentos curtos.
ANTES = {"hey", "hei", "ei", "e", "eh", "ey", "eye", "rei", "hi", "oi", "ok", "okay", "ai", "o", "fala", "ola"}
# Chamados de verdade: só depois deles "visão" (português) conta como o nome. "É visão de futuro", "ok, visão
# geral" e "aí, visão turva" são frases comuns (narração de futebol na TV) e não podem acordar.
FORTES = {"hey", "hei", "ei", "ey", "eye", "rei", "hi", "oi", "fala", "ola"}
DESLIGAR = {"dormir", "encerrar", "encerra", "descansar"}
STANDBY = "standby"
MAX_PALAVRAS_STANDBY = 20
# O que pode vir depois de "desligar" numa despedida. Qualquer outra palavra ("desligar o alarme") é um pedido.
ENCHIMENTO = {"agora", "ja", "por", "favor", "obrigado", "obrigada", "valeu", "entao", "tchau", "ta", "beleza"}
MAX_PALAVRAS_DESPEDIDA = 8
FECHAMENTO = {"beleza", "valeu", "obrigado", "obrigada", "falou", "blz", "ok", "certo", "show"}
# Pode vir antes do "dormir" numa despedida ("beleza, Vision, pode dormir"). Qualquer outra coisa antes
# ("me lembra de dormir", "que horas eu preciso dormir") é pergunta ou pedido.
ANTES_DE_DESLIGAR = ENCHIMENTO | FECHAMENTO | {"pode", "poe", "pod", "voce", "ai", "e", "ok", "okay", "hey", "ei"}


def _normalizar(texto: str) -> list[str]:
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", sem_acento)


def _e_o_nome(palavra: str, chamado_forte: bool) -> bool:
    if palavra in GRAFIAS:
        return True
    # Grudado num chamado ("heyvision", "valuvision"), mas não "revision" nem "visionario".
    for g in GRAFIAS:
        if palavra.endswith(g) and palavra[: -len(g)] in ANTES | FECHAMENTO | {"valu"}:
            return True
    if chamado_forte and palavra in {"visao", "visoes"}:  # "Ei, visão" = alguém chamando
        return True
    # Grafia nova parecida ("visiun"), só com o tamanho do nome: "revision" e "visionario" ficam de fora.
    return 5 <= len(palavra) <= 7 and difflib.SequenceMatcher(None, palavra, NOME).ratio() >= 0.8


def achar_ativacao(texto: str) -> str | None:
    """None se a fala não começa chamando o assistente; senão, o resto da fala ("" se só chamou).

    O resto sai do texto original (com acentos e pontuação), para ir ao modelo como foi dito.
    """
    palavras = _normalizar(texto)
    for i, p in enumerate(palavras[:3]):
        if _e_o_nome(p, chamado_forte=i > 0 and palavras[i - 1] in FORTES) and all(a in ANTES for a in palavras[:i]):
            return _resto_depois(texto, i + 1)
        if p not in ANTES:
            return None
    return None


def _resto_depois(texto: str, n_palavras: int) -> str:
    """O texto original sem as n primeiras palavras e sem a pontuação que sobra no começo."""
    achadas = list(re.finditer(r"\w+", texto))
    if len(achadas) <= n_palavras:
        return ""
    return texto[achadas[n_palavras].start():].strip()


def _posicao_standby(palavras: list[str]) -> int | None:
    """Onde está "standby" na fala ("standby", "stand-by", "stand by", e "standry" mal ouvido)."""
    for i, p in enumerate(palavras):
        juntas = p + (palavras[i + 1] if i + 1 < len(palavras) else "")
        for candidato in (p, juntas):
            if STANDBY in candidato or (6 <= len(candidato) <= 8
                                        and difflib.SequenceMatcher(None, candidato, STANDBY).ratio() >= 0.85):
                return i
    return None


def e_despedida(texto: str) -> bool:
    """"Vision, standby", "pode ficar em standby", "pode dormir", "tchau, Vision": fecha a conversa.

    "Standby" vale numa frase de até 20 palavras ("tudo certo, pode ficar em standby que eu te chamo"), menos
    em pergunta ("não entrou em standby, né?") e com "não" logo antes ("não entra em standby").
    """
    palavras = _normalizar(texto)
    if not palavras:
        return False
    i = _posicao_standby(palavras)
    if i is not None:
        return (len(palavras) <= MAX_PALAVRAS_STANDBY and not texto.strip().endswith("?")
                and "nao" not in palavras[max(0, i - 3):i])
    if len(palavras) > MAX_PALAVRAS_DESPEDIDA or "nao" in palavras:  # "não, não vai dormir"
        return False
    nome = lambda p: _e_o_nome(p, True)  # noqa: E731
    ultimo = max((i for i, p in enumerate(palavras) if p in DESLIGAR), default=None)
    if ultimo is not None:
        antes_ok = all(p in ANTES_DE_DESLIGAR or p in DESLIGAR or nome(p) for p in palavras[:ultimo])
        depois_ok = all(p in ENCHIMENTO or nome(p) for p in palavras[ultimo + 1:])
        return antes_ok and depois_ok
    if "tchau" in palavras and any(nome(p) for p in palavras):
        # "tchau, Vision" sim; "tchau Vision, marca dentista amanhã" é um pedido
        return all(p in ENCHIMENTO or p in FECHAMENTO or nome(p) for p in palavras)
    return False
