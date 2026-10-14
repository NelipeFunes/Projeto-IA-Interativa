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
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

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
# Nas 3 palavras antes de "standby", na mesma oração, fazem dele assunto, não despedida: "não entra em standby",
# "o modo standby da TV", "a TV ficou em standby", "coloca o PC em standby". Lista curta de propósito: na dúvida,
# fechar a conversa é melhor do que ela não fechar ("deixa em standby", "por favor, standby" fecham).
# 02/10: "modo" saiu da lista ("pode entrar em modo standby" é despedida e virava "suspender o PC"); "o modo
# standby da TV" continua barrado pelo aparelho, que agora também vale nas 3 palavras DEPOIS do standby.
APARELHOS = {"pc", "computador", "notebook", "tv", "televisao", "monitor", "celular"}
# Pergunta ou configuração antes do "standby" faz dele assunto ("explica o modo standby", "quanto gasta o modo
# standby", "como ativo o modo standby"), não despedida (revisão do PR 42).
ASSUNTO = {"explica", "como", "quanto", "porque", "qual", "quais", "que", "ativa", "ativar", "ativo", "desativa",
           "desativar", "configura", "configurar", "gasta", "consome", "sobre"}
NAO_E_STANDBY = {"nao", "ficou", "estava", "entrou"} | APARELHOS | ASSUNTO
# "Stand. By." e "Stand you by." (o Parakeet com sotaque, 02/10) são o mesmo standby. Só as palavras curtas que ele
# põe no meio: "stand up by 5pm" e "stand 3 by 4" não (revisão do PR 42).
JUNTAR_STANDBY = re.compile(r"\bstand\W+(?:(?:you|u|yu|ya|yo)\W+)?by\b", re.IGNORECASE)
STAND_BY_ME = re.compile(r"\bstand\W+by\W+me\b", re.IGNORECASE)  # a música ("toca Stand by Me"), não despedida
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


# Calibração (02/10: "é o que ele menos entende"): como o STT escreve o "Vision" na voz do Felipe, aprendido nos
# Ajustes ("Calibrar o Hey Vision") e guardado em data/ativacao.json. Valem como GRAFIAS.
#
# Revisão do PR 43: uma grafia errada faz conversa normal (ou a TV) acordar o Vision, e o que vem depois vai ao
# modelo, que tem ferramentas. Por isso, além da lista de palavras comuns: só fala curta (o "Hey X" que foi pedido;
# frase de TV fica de fora), maioria das vezes (3 de 5), no máximo MAX_APELIDOS guardadas, e a tela pergunta antes
# de gravar ("Usar 'deliving' como 'Vision'?").
APELIDOS: set[str] = set()
ARQUIVO_APELIDOS = "ativacao.json"
MAX_APELIDOS = 5
MAX_PALAVRAS_CALIBRACAO = 3  # "Hey Deliving", "E vision", "Deliving": mais que isso não é alguém só chamando
# Palavras comuns não viram apelido, mesmo repetidas: com elas, conversa normal (ou a TV) acordaria o Vision.
COMUNS = {
    "yeah", "yes", "hey", "hi", "hello", "the", "you", "your", "this", "that", "with", "what", "when", "where", "have",
    "just", "like", "know", "okay", "good", "right", "there", "they", "them", "then", "here", "were", "will", "would",
    "could", "should", "about", "really", "thank", "thanks", "nice", "sorry", "please", "music", "living", "love",
    "well", "stop", "wait", "come", "look", "make", "take", "want", "need", "time", "people", "thing", "think",
    "isso", "esse", "essa", "este", "esta", "aqui", "agora", "entao", "tudo", "nada", "sim", "nao", "obrigado",
    "beleza", "valeu", "certo", "ainda", "depois", "antes", "muito", "pouco", "mais", "menos", "coisa", "gente",
    "voce", "vamos", "vai", "vou", "tem", "tenho", "quero", "pode", "fala", "falar", "olha", "olhar", "visao",
    "visita", "visual", "vista", "divisao", "revisao", "televisao", "decisao", "precisa", "preciso",
    "para", "porque", "quando", "como", "onde", "qual", "quem", "sobre", "entre", "brasil", "hoje", "ontem", "amanha",
    "casa", "tempo", "dia", "noite", "jogo", "time", "gol", "cara", "mano", "galera", "pessoal", "senhor", "senhora",
}


def _limpo(palavra: str) -> str:
    return palavra.strip().lower() if isinstance(palavra, str) else ""


def definir_apelidos(apelidos) -> None:
    APELIDOS.clear()
    APELIDOS.update(sorted({_limpo(a) for a in apelidos if apelido_valido(_limpo(a))})[:MAX_APELIDOS])


def ler_apelidos(pasta: Path) -> list[str]:
    try:
        caminho = pasta / ARQUIVO_APELIDOS
        if caminho.stat().st_size > 10_000:  # é uma lista de 5 palavras: maior que isso não é nosso
            return []
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    lista = dados.get("apelidos") if isinstance(dados, dict) else None
    if not isinstance(lista, list):
        return []
    return sorted({_limpo(a) for a in lista if apelido_valido(_limpo(a))})[:MAX_APELIDOS]


def gravar_apelidos(pasta: Path, apelidos: list[str]) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    validos = sorted({_limpo(a) for a in apelidos if apelido_valido(_limpo(a))})[:MAX_APELIDOS]
    tmp = pasta / (ARQUIVO_APELIDOS + ".tmp")
    tmp.write_text(json.dumps({"apelidos": validos}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(pasta / ARQUIVO_APELIDOS)


def apelido_valido(palavra: str) -> bool:
    return (isinstance(palavra, str) and palavra.isascii() and palavra.isalpha() and palavra.islower()
            and 4 <= len(palavra) <= 14
            and palavra not in COMUNS | ANTES | FORTES | FECHAMENTO | DESLIGAR | ENCHIMENTO and palavra != STANDBY)


def candidato_do_nome(texto: str) -> str | None:
    """Onde o nome deveria estar numa fala CURTA: a 1ª palavra depois dos chamados ("Hey Deliving" → "deliving";
    "Deliving" → "deliving"). None se a fala é longa (frase da TV) ou começa com outra coisa."""
    palavras = _normalizar(texto)
    if not palavras or len(palavras) > MAX_PALAVRAS_CALIBRACAO:
        return None
    for i, p in enumerate(palavras):
        if p not in ANTES:
            return p if all(a in ANTES for a in palavras[:i]) else None
    return None


def aprender_apelidos(ouvidos: list[str]) -> list[str]:
    """Das falas da calibração que NÃO acordaram, as grafias do nome que saíram na MAIORIA das vezes (3 de 5) e não
    são palavra comum. É uma sugestão: a tela pergunta antes de gravar."""
    minimo = max(2, len(ouvidos) // 2 + 1)
    contagem = Counter(c for t in ouvidos if achar_ativacao(t) is None and (c := candidato_do_nome(t)))
    return sorted(c for c, n in contagem.items() if n >= minimo and apelido_valido(c))


def _e_o_nome(palavra: str, chamado_forte: bool) -> bool:
    if palavra in GRAFIAS or palavra in APELIDOS:
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
        if STANDBY in p:
            return i
        # "stan by", "sand by": a dupla só conta parecida com "standby" inteira ("modo standby" não é "standby" no
        # "modo", 2ª revisão do PR 16).
        for candidato in (p, juntas):
            if 6 <= len(candidato) <= 8 and difflib.SequenceMatcher(None, candidato, STANDBY).ratio() >= 0.85:
                return i
    return None


def e_despedida(texto: str) -> bool:
    """"Vision, standby", "pode ficar em standby", "pode dormir", "tchau, Vision": fecha a conversa.

    "Standby" vale numa frase de até 20 palavras ("tudo certo, pode ficar em standby que eu te chamo"), menos
    em pergunta ("não entrou em standby, né?") e quando a oração dele é pedido ou assunto ("não entra em
    standby", "coloca o PC em standby", "o modo standby da TV").
    """
    if STAND_BY_ME.search(texto):
        return False
    texto = JUNTAR_STANDBY.sub("standby", texto)
    palavras = _normalizar(texto)
    if not palavras:
        return False
    if _posicao_standby(palavras) is not None:
        if len(palavras) > MAX_PALAVRAS_STANDBY or texto.strip().endswith("?"):
            return False
        # Só a oração do "standby" conta: em "Não, Vision, standby" o "não" responde a outra coisa.
        for oracao in re.split(r"[,.;:!?]+", JUNTAR_STANDBY.sub("standby", texto)):
            p = _normalizar(oracao)
            i = _posicao_standby(p)
            if i is not None:
                return not (NAO_E_STANDBY & set(p[max(0, i - 3):i]) or APARELHOS & set(p[i + 1:i + 4]))
        return True  # "stand" e "by" separados de outro jeito: na dúvida, fecha
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


# Depois que a sua voz cortou a dele: frase só de parar ("para de falar", "chega") não vai ao modelo, só para.
# A frase inteira tem que ser de parar: "para quando é a prova?" é pergunta.
PARAR = {"para", "pare", "parar", "paro", "chega", "silencio", "cala", "calado", "calada", "stop", "espera",
         "espere", "pera", "perai", "quieto", "quieta", "basta", "shh", "psiu"}
# Sem "um pouco": "espere um pouco" é frase que ele mesmo diz (revisão do PR 17).
JUNTO_DO_PARAR = {"ai", "a", "boca", "de", "falar", "ja", "ta", "bom", "ok", "pode", "por", "favor", "e", "ei",
                  "hey", "entendi", "obrigado", "valeu", "mais", "isso", "beleza"}
MAX_PALAVRAS_PARAR = 6


def e_interrupcao(texto: str) -> str | None:
    """"standby" (fecha a conversa), "parar" (só para de falar, nada vai ao modelo) ou None (é um pedido)."""
    texto = texto if STAND_BY_ME.search(texto) else JUNTAR_STANDBY.sub("standby", texto)  # "Stand you by"
    palavras = _normalizar(texto)
    if not palavras:
        return None
    # Só "standby" explícito: "pode descansar" ou "encerrar" podem ser a própria voz vazando (revisão do PR 17).
    if _posicao_standby(palavras) is not None and e_despedida(texto):
        return "standby"
    if len(palavras) > MAX_PALAVRAS_PARAR or not PARAR & set(palavras):
        return None
    if all(p in PARAR or p in JUNTO_DO_PARAR or _e_o_nome(p, True) for p in palavras):
        return "parar"
    return None
