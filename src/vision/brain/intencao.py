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
    "casa": re.compile(r"\b(luz|luzes|l[aâ]mpada\w*|light\w*|acend\w*|apag(a|ue|ar) a luz|abajur|ilumina\w*|"
                       r"(des)?lig(a|ue|ar) (a|as|o|os) (luz|luzes|l[aâ]mpada\w*|light\w*|abajur|quarto|sala))\b",
                       re.IGNORECASE),
    # Só verbo + alvo: "qual sua música favorita?" não é pedido para mexer no PC.
    "pc": re.compile(r"\b(abr(e|a|ir)|fech(a|e|ar)) |\bvolume\b|\bpaus(a|e|ar)\b|\bpr[oó]xima (m[uú]sica|faixa)\b|"
                     r"\btoca(r)? (a |uma |o |um )?\w|\bno spotify\b|\btrav(a|e|ar) (o pc|a tela|o computador)\b|"
                     r"\b(deslig|reinici|suspend)\w* o (pc|computador)\b|\bpowershell\b|"
                     r"\b(lista|listar|mostra|apaga|move|copia|renomeia|cria)\w* (os |as |o |a |uma |um )?"
                     r"(arquivos?|pastas?)\b", re.IGNORECASE),
    "timer": re.compile(r"\b(timer|alarme|cron[oô]metro|temporizador|me avis[ae] (daqui|em|às|as|quando der))\b",
                        re.IGNORECASE),
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
    r"\b(vou|j[aá] vou|deixa eu|irei)\s+(te\s+)?(precisar\s+(de\s+)?)?(verificar|checar|olhar|consultar|criar|"
    r"marcar|colocar|agendar|cancelar|apagar|alterar|mudar|lan[cç]ar|registrar|anotar|guardar|buscar|procurar|"
    r"acender|ligar|desligar|abrir|fechar|tocar|pausar|colocar um timer|programar|rodar|executar|listar)\b"
    r"|\bpreciso\s+(de\s+)?(rodar|executar|abrir)\b"
    r"|\bj[aá]\s+(crio|marco|coloco|agendo|cancelo|apago|altero|mudo|lan[cç]o|registro|anoto|guardo|acendo|ligo|"
    r"desligo)\b"
    r"|\bestou\s+(verificando|checando|olhando|consultando|criando|marcando|agendando|cancelando|apagando|"
    r"alterando|lan[cç]ando|buscando|procurando)\b"
    r"|\bconfirm(a|ou|e)\?\s*$",
    re.IGNORECASE,
)


def anunciou_sem_fazer(resposta: str) -> bool:
    return bool(ANUNCIO.search(resposta.strip()))


# Pior que anunciar: dizer que FEZ ("Feito.", "Ligado.", "Apaguei as luzes") sem ter chamado nada no turno.
# Visto em 01/10: depois de algumas confirmações reais, o 4B passou a imitar o "Feito." sozinho.
AFIRMACAO = re.compile(
    r"^\W*(feito|ok,? feito|acendi|apaguei|aparei|liguei|desliguei|criei|marquei|agendei|cancelei|lancei|anotei|"
    r"guardei|alterei|mudei|abri|fechei|pausei|coloquei|timer (de .{1,30} )?ligado|aumentei|abaixei|travei|rodei)\b",
    re.IGNORECASE,
)


def afirmou_sem_fazer(resposta: str) -> bool:
    return bool(AFIRMACAO.search(resposta.strip()))
