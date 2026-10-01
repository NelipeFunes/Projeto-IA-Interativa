"""Rede de segurança contra resposta inventada.

No primeiro teste o 4B respondeu "sua agenda está vazia" sem chamar a ferramenta. Se a frase
tem cara de agenda/finanças/tarefas/memória e o modelo não chamou nada, o agente insiste uma vez.
"""

from __future__ import annotations

import re

PADROES = {
    "agenda": re.compile(
        r"\b(agenda|compromisso|evento|reuni[aã]o|aula|prova|marca(r|do|da|dos)?|agend\w*|calend[aá]rio|"
        r"feriado|ocupad[oa]|livre|hor[aá]rio|dentista|m[eé]dico|consulta|minha semana|amanh[aã] (eu )?tenho|"
        r"tenho (hoje|amanh[aã]|algo|alguma))\b",
        re.IGNORECASE,
    ),
    "financas": re.compile(
        r"\b(gast\w*|dinheiro|finan[cç]\w*|or[cç]amento|saldo|fatura|ifood|despesa|receita|reais|r\$|"
        r"quanto (eu )?(j[aá] )?(gastei|paguei)|lan[cç]a(r)?|sobrou|reserva)\b",
        re.IGNORECASE,
    ),
    "reunioes": re.compile(
        r"\b(reuni[oõ]\w*|daily|standup|transcri[cç]\w*|discutimos|falamos|combinamos|decidimos|ata|"
        r"wispr|nota(s)? (do|da|que)|minhas notas|pr[oó]ximos passos)\b",
        re.IGNORECASE,
    ),
    "tarefas": re.compile(r"\b(tarefa\w*|to-?do|pend[eê]ncia\w*|afazer\w*|me lembr[ae] de)\b", re.IGNORECASE),
    "memoria": re.compile(
        r"(\b(lembr[ae]|anot[ae]|guard[ae]) (que|isso|a[ií])\b|\bn[aã]o esque[cç]a\b|\bvoc[eê] (sabe|lembra)\b|"
        r"\b(esquece|apaga da mem[oó]ria)\b)",
        re.IGNORECASE,
    ),
}


def detectar(texto: str) -> list[str]:
    return [grupo for grupo, p in PADROES.items() if p.search(texto)]


# O 9B tinha o vício de ANUNCIAR a ação ("Vou colocar na agenda...", "Já cancelo...") sem chamar a
# ferramenta, e às vezes imitava a frase de confirmação do sistema ("... Confirma?") sem nada pendente.
# Só futuro/presente ("vou marcar", "já cancelo", "estou buscando"); "já verifiquei" é resposta legítima.
ANUNCIO = re.compile(
    r"\b(vou|j[aá] vou|deixa eu|irei)\s+(te\s+)?(verificar|checar|olhar|consultar|criar|marcar|colocar|agendar|"
    r"cancelar|apagar|alterar|mudar|lan[cç]ar|registrar|anotar|guardar|buscar|procurar)\b"
    r"|\bj[aá]\s+(crio|marco|coloco|agendo|cancelo|apago|altero|mudo|lan[cç]o|registro|anoto|guardo)\b"
    r"|\bestou\s+(verificando|checando|olhando|consultando|criando|marcando|agendando|cancelando|apagando|"
    r"alterando|lan[cç]ando|buscando|procurando)\b"
    r"|\bconfirma\?\s*$",
    re.IGNORECASE,
)


def anunciou_sem_fazer(resposta: str) -> bool:
    return bool(ANUNCIO.search(resposta.strip()))
