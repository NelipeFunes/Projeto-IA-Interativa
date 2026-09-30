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
    "tarefas": re.compile(r"\b(tarefa\w*|to-?do|pend[eê]ncia\w*|afazer\w*)\b", re.IGNORECASE),
    "memoria": re.compile(
        r"(\b(lembr[ae]|anot[ae]|guard[ae]) (que|isso|a[ií])\b|\bn[aã]o esque[cç]a\b|\bvoc[eê] (sabe|lembra)\b|"
        r"\b(esquece|apaga da mem[oó]ria)\b)",
        re.IGNORECASE,
    ),
}


def detectar(texto: str) -> list[str]:
    return [grupo for grupo, p in PADROES.items() if p.search(texto)]
