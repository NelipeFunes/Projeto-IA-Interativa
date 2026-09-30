"""As duas frases que controlam a conversa por voz, reconhecidas no texto transcrito.

- "Hey Vision" (no começo da fala) abre a conversa. O que vier depois já é o pedido:
  "Hey Vision, o que eu tenho hoje?" abre e responde de uma vez.
- "Beleza, Vision, pode desligar" (frase curta com "desligar"/"dormir"/"encerrar") fecha a conversa.

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
DESLIGAR = {"desligar", "desliga", "desliger", "dormir", "encerrar", "encerra", "descansar"}
# O que pode vir depois de "desligar" numa despedida. Qualquer outra palavra ("desligar o alarme") é um pedido.
ENCHIMENTO = {"agora", "ja", "por", "favor", "obrigado", "obrigada", "valeu", "entao", "tchau", "ta", "beleza"}
MAX_PALAVRAS_DESPEDIDA = 8
# "Beleza, Vision, pode ..." com o fim mal ouvido: o Parakeet já trocou "desligar" por "dizer I" (30/09).
FECHAMENTO = {"beleza", "valeu", "obrigado", "obrigada", "falou", "blz", "ok", "certo", "show"}
# Depois de "pode", isto é pedido, não despedida ("beleza, Vision, pode marcar o dentista").
PEDIDOS = {"marcar", "marca", "criar", "cria", "apagar", "apaga", "mudar", "muda", "ver", "ve", "listar", "falar",
           "fala", "me", "lembrar", "anotar", "anota", "guardar", "colocar", "coloca", "mandar", "manda", "ler", "le",
           "responder", "procurar", "buscar", "confirmar", "cancelar", "sim", "nao", "tocar", "abrir", "abre"}


def _normalizar(texto: str) -> list[str]:
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", sem_acento)


def _e_o_nome(palavra: str, depois_de_cumprimento: bool) -> bool:
    if palavra in GRAFIAS or NOME in palavra:  # "valuvision" também conta
        return True
    if depois_de_cumprimento and palavra in {"visao", "visoes"}:  # "Ei, visão" = alguém chamando
        return True
    return len(palavra) >= 5 and difflib.SequenceMatcher(None, palavra, NOME).ratio() >= 0.8


def achar_ativacao(texto: str) -> str | None:
    """None se a fala não começa chamando o assistente; senão, o resto da fala ("" se só chamou).

    O resto sai do texto original (com acentos e pontuação), para ir ao modelo como foi dito.
    """
    palavras = _normalizar(texto)
    for i, p in enumerate(palavras[:3]):
        if _e_o_nome(p, depois_de_cumprimento=i > 0) and all(a in ANTES for a in palavras[:i]):
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


def e_despedida(texto: str) -> bool:
    """"Beleza, Vision, pode desligar", "pode dormir", "valeu, pode encerrar": fecha a conversa.

    Só frase curta: "pode desligar o alarme amanhã às sete" é um pedido, não uma despedida.
    """
    palavras = _normalizar(texto)
    if not palavras or len(palavras) > MAX_PALAVRAS_DESPEDIDA:
        return False
    ultimo = max((i for i, p in enumerate(palavras) if p in DESLIGAR), default=None)
    if ultimo is not None:
        return all(p in ENCHIMENTO or _e_o_nome(p, True) for p in palavras[ultimo + 1:])
    if "tchau" in palavras and any(_e_o_nome(p, True) for p in palavras):
        return True
    return _fechamento_mal_ouvido(palavras)


def _fechamento_mal_ouvido(palavras: list[str]) -> bool:
    """"beleza vision pode <até 2 palavras que não são pedido>"."""
    if len(palavras) > 6 or not palavras or palavras[0] not in FECHAMENTO:
        return False
    try:
        i = palavras.index("pode")
    except ValueError:
        return False
    if not any(_e_o_nome(p, True) for p in palavras[1:i]):
        return False
    depois = palavras[i + 1:]
    return 0 < len(depois) <= 2 and not any(p in PEDIDOS for p in depois)
