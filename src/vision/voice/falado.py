"""Deixa o texto do Vision bom de ouvir: '14:30' → '14 e 30', 'sex 02/10' → 'sexta, dia 2 de outubro',
'R$ 52,90' → 'cinquenta e dois reais e noventa centavos', tira markdown, emojis e ids."""

from __future__ import annotations

import re

from vision.tempo import MESES

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


# "1/2 litro", "3/4 de xícara", "1/2 hora": fração de quantidade, não data. Com "dia" na frente (ou ano) é sempre data.
_QUANTIDADE = re.compile(r"[ \t]*(?:de[ \t]+)?(?:(?:litros?|l|quilos?|kg|gramas?|g|ml|x[ií]caras?|copos?|colheres?|metade|"
                         r"horas?|minutos?|porç(?:ão|ões)|fatias?|pedaços?|unidades?|dúzias?|do total)\b|%)")
_DIA_DA_SEMANA_ANTES = re.compile(r"(?:segunda|terça|quarta|quinta|sexta|sábado|domingo)(?:-feira)?,[ \t]*$", re.IGNORECASE)


def _data(m: re.Match[str]) -> str:
    dia, mes = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        return m.group(0)
    explicita = (m.group(0).lower().startswith("dia") or m.group(0).count("/") == 2
                 or _DIA_DA_SEMANA_ANTES.search(m.string, 0, m.start()) is not None)
    if not explicita and _QUANTIDADE.match(m.string, m.end()):
        return m.group(0)
    return f"dia {dia} de {MESES[mes - 1]}"


_UNIDADES = ("zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze",
             "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove")
_DEZENAS = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa")
_CENTENAS = ("", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos",
             "novecentos")
_ESCALAS = ((10**9, "bilhão", "bilhões"), (10**6, "milhão", "milhões"), (10**3, "mil", "mil"))


def _ate_999(n: int) -> str:
    if n == 100:
        return "cem"
    c, resto = divmod(n, 100)
    partes = [_CENTENAS[c]] if c else []
    if resto:
        d, u = divmod(resto, 10)
        partes.append(_UNIDADES[resto] if resto < 20 else _DEZENAS[d] + (f" e {_UNIDADES[u]}" if u else ""))
    return " e ".join(partes)


def por_extenso(n: int) -> str:
    """Número inteiro por extenso, no masculino ("um", "dois": é para valores em reais). De um trilhão (10**12) em diante
    fica em dígitos. 1234 → "mil duzentos e trinta e quatro"; 2500 → "dois mil e quinhentos"; 1000000 → "um milhão"."""
    if n < 0 or n >= 10**12:
        return str(n)
    if n == 0:
        return "zero"
    partes: list[tuple[str, int]] = []
    for valor, singular, plural in _ESCALAS:
        grupo, n = divmod(n, valor)
        if grupo:
            partes.append(("mil" if valor == 1000 and grupo == 1 else f"{_ate_999(grupo)} {singular if grupo == 1 else plural}", grupo))
    if n:
        partes.append((_ate_999(n), n))
    if len(partes) == 1:
        return partes[0][0]
    ultimo = partes[-1][1]
    ligacao = " e " if ultimo < 100 or ultimo % 100 == 0 else " "  # "mil e cem", "mil duzentos e trinta e quatro"
    return " ".join(t for t, _ in partes[:-1]) + ligacao + partes[-1][0]


# "R$ 1.234,56", "R$ 50", "-R$ 1.250,00": começa em dígito e só aceita ponto de milhar (o ponto final da frase, "R$ 50.", fica
# fora; "R$." sozinho nem casa: antes levantava ValueError e calava o resto da resposta).
# O sinal de menos só vale COLADO no R$ (como o Orbit escreve): "- R$ 50" com espaços é travessão, e "-" no começo da linha é
# marcador de lista (os dois já eram tratados à parte).
_DINHEIRO = re.compile(r"(?<![\w$])(-)?R\$\s*(\d+(?:\.\d{3})*)(?:,(\d{2}))?")
# "R$ 120 mil", "R$ 1,5 milhão": o formato de valor grande em notícia.
_DINHEIRO_COM_ESCALA = re.compile(r"(?<![\w$])(-)?R\$\s*(\d+)(?:,(\d{1,2}))?\s+(mil|milhão|milhões|bilhão|bilhões)\b", re.IGNORECASE)


def _reais(m: re.Match[str]) -> str:
    """"R$ 1.234,56" → "mil duzentos e trinta e quatro reais e cinquenta e seis centavos". Por extenso porque o Qwen3-TTS lia
    números de 4 e 5 dígitos errado ("R$ 1.234,56" saía "R$ 134,56"); só valor em reais, que é sempre masculino. O sinal de
    menos vira "menos" (o espeak do Piper lia o "-" colado em "-mil", e um saldo negativo saía positivo)."""
    menos = "menos " if m.group(1) else ""
    n = int(m.group(2).replace(".", ""))
    centavos = int(m.group(3)) if m.group(3) else 0
    palavra = f"{por_extenso(centavos)} {'centavo' if centavos == 1 else 'centavos'}"
    if n == 0 and centavos:
        return menos + palavra  # "R$ 0,50" → "cinquenta centavos"
    de = "de " if n >= 10**6 and n % 10**6 == 0 else ""  # "um milhão de reais", mas "um milhão e cem reais"
    texto = "um real" if n == 1 else f"{por_extenso(n)} {de}reais"
    return menos + (f"{texto} e {palavra}" if centavos else texto)


def _reais_com_escala(m: re.Match[str]) -> str:
    """"R$ 120 mil" → "cento e vinte mil reais"; "R$ 1,5 milhão" → "um vírgula cinco milhão de reais"."""
    menos = "menos " if m.group(1) else ""
    valor = por_extenso(int(m.group(2)))
    if m.group(3):
        decimal = m.group(3)
        valor += " vírgula " + ("zero " if decimal.startswith("0") and len(decimal) > 1 else "") + por_extenso(int(decimal))
    escala = m.group(4).lower()
    return f"{menos}{valor} {escala} {'' if escala == 'mil' else 'de '}reais"


# Unidades depois de um número (vêm muito em resposta de busca na web): (singular, plural). Ordem importa: km/h
# antes de km.
UNIDADES = [
    (r"km/h", ("quilômetro por hora", "quilômetros por hora")),
    (r"km/l", ("quilômetro por litro", "quilômetros por litro")),
    (r"km", ("quilômetro", "quilômetros")),
    (r"kg", ("quilo", "quilos")),
    (r"cm", ("centímetro", "centímetros")),
    (r"mm", ("milímetro", "milímetros")),
    (r"m²|m2", ("metro quadrado", "metros quadrados")),
    (r"ml", ("mililitro", "mililitros")),
    (r"[lL]", ("litro", "litros")),
    (r"cv", ("cavalo", "cavalos")),
    (r"kWh", ("quilowatt-hora", "quilowatts-hora")),
    (r"GB", ("giga", "gigas")),
    (r"MB", ("mega", "megas")),
    (r"°\s?C|ºC", ("grau", "graus")),
    (r"°", ("grau", "graus")),  # só o sinal de grau: "º" sozinho é ordinal ("1º lugar")
    (r"min", ("minuto", "minutos")),
]


def _unidade(singular: str, plural: str):
    def troca(m: re.Match[str]) -> str:
        valor = float(m.group(1).replace(",", "."))
        return f"{m.group(1)} {singular if valor < 2 else plural}"  # "1,7 litro", "2 litros"

    return troca


def unidades(t: str) -> str:
    t = re.sub(r"(?<![\d.])(\d+)\.(\d{1,2})(?![\d.])", r"\1,\2", t)  # 1.7 → 1,7; 1.250, 1.2.3 e 02.10.2026 ficam
    t = re.sub(r"(\d)\s?%", r"\1 por cento", t)
    t = re.sub(r"(\d)\s+[xX×]\s+(\d)", r"\1 vezes \2", t)   # 12 x 8 (conta, com espaços)
    t = re.sub(r"(\d)[xX×](\d)", r"\1 por \2", t)             # 4x4, 1920x1080 (medida, colado)
    t = re.sub(r"\b[nN]\.?[º°]\s?(?=\d)", "número ", t)  # nº 45, n.º 45 ("no 2º andar" não)
    for padrao, (singular, plural) in UNIDADES:
        t = re.sub(r"(\d+(?:,\d+)?)\s?(?:" + padrao + r")(?![\w/])", _unidade(singular, plural), t)
    return t


def para_fala(texto: str) -> str:
    t = texto
    t = re.sub(r"\(\s*id:[^)]*\)|\bid:\s*\S+", "", t)            # ids de evento
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                # [texto](link) → texto
    t = re.sub(r"\(?[ \t]*(?:https?://|www\.)[^\s)]+[ \t]*\)?", "", t)  # links: ficam na tela, não na fala
    t = re.sub(r"[*_`#>]+", "", t)                               # markdown
    t = EMOJIS.sub("", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.MULTILINE)         # marcadores de lista
    t = re.sub(r"(\d)\s+[xX×]\s+(?=R\$)", r"\1 vezes ", t)  # "12 x R$ 83,25" (parcela): antes do R$ virar palavra
    t = _DINHEIRO_COM_ESCALA.sub(_reais_com_escala, t)
    t = _DINHEIRO.sub(_reais, t)
    t = unidades(t)
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
    t = re.sub(r"[:;,]\s*([.!?])", r"\1", t)  # "casas: <link>." → "casas."
    return re.sub(r"\s{2,}", " ", t).strip()


def frases(texto: str) -> list[str]:
    """Quebra em frases para o TTS começar a falar antes do texto todo chegar."""
    partes = re.split(r"(?<=[.!?;:])\s+", texto.strip())
    return [p for p in partes if p]
