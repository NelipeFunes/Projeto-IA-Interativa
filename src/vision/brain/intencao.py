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
                     r"\btrav(a|e|ar) (o pc|a tela|o computador)\b|"
                     r"\b(deslig|reinici|suspend)\w* o (pc|computador)\b|\bpowershell\b|"
                     r"\b(lista|listar|mostra|apaga|move|copia|renomeia|cria)\w* (os |as |o |a |uma |um )?"
                     r"(arquivos?|pastas?)\b", re.IGNORECASE),
    # "toca" só como pedido, no começo da frase: "isso me toca", "o sino toca às 7" não (revisão do PR 21).
    "musica": re.compile(r"^\W*(?:(?:vision|vis[aã]o|hey|ei|ok|pode|por favor|a[ií]|e|ent[aã]o|agora|j[aá]|me)\W+)*"
                         r"(?:toca|tocar|toque|d[aá] play)\s+\w|\bno spotify\s*\W*$|"
                         r"\b(que|qual) m[uú]sica (est[aá] |t[aá] )?tocando\b|"
                         r"\b(pul[ae]|pular|volt[ae]|continu[ae]|paus[ae]|repet[ei])\w* (essa |esta |a |o )?"
                         r"(m[uú]sica|faixa|som|spotify)\b", re.IGNORECASE),
    "timer": re.compile(r"\b(timer|alarme|cron[oô]metro|temporizador|me avis[ae] (daqui|em|às|as|quando der))\b|"
                        r"\b(tv|televis[aã]o)\b.*\b(em|daqui a|daqui) \d+ ?(min|minutos?|h|horas?)\b",
                        re.IGNORECASE),  # "desliga a TV em 30 minutos": timer com ação
    # Só verbo + TV: "o que passa na TV hoje?" é pergunta, não pedido para mexer nela (revisão do PR 46).
    "tv": re.compile(r"\b(deslig|aument|abaix|diminu|sob[ei]|sub[ai]|mut[ae]|desmut|silenci|toc|coloc|p[oõ]e|"
                     r"bot[ae]|abr[ea]|mud[ae]|troc|volt[ae]|paus|apert)\w*\b.{0,60}\b(tv|televis[aã]o)\b|"
                     r"\b(volume|canal|som) da (tv|televis[aã]o)\b", re.IGNORECASE),
    # Só pedido explícito de pesquisa: "busca minha agenda" é da agenda, não da web.
    "web": re.compile(r"\bpesquis(a|e|ar|ando)\b|\b(na|pela) (internet|web|net)\b|\bno google\b|"
                      r"\b(busc|procur)(a|e|ar) (algumas|alguns|umas|uns|op[cç][oõ]es|pre[cç]os?)\b|"
                      r"\bquanto (custa|est[aá] custando)\b|\bnot[ií]cias?\b|\bmanchetes?\b", re.IGNORECASE),
    # Só pedido de verdade: "minha rotina hoje" ou "modo de usar" não ligam protocolo (revisão do PR 32).
    "protocolo": re.compile(r"\bprotocolo\b|\b(ativ|lig|inici|rod|execut)\w* (o |a )?(modo|rotina)\b",
                            re.IGNORECASE),
    "clima": re.compile(r"\b(clima|temperatura|previs[aã]o do tempo|vai (chover|fazer (frio|calor|sol))|"
                        r"t[aá] (frio|calor)|est[aá] (frio|calor)|quantos graus|guarda-chuva)\b", re.IGNORECASE),
    "tarefas": re.compile(r"\b(tarefa\w*|to-?do|pend[eê]ncia\w*|afazer\w*|fazeres|lista de (?:coisas a fazer|tarefas)|minha lista|risca\w*|j[aá] (?:paguei|fiz|comprei|liguei|resolvi|terminei)|(?:prioridade|vencimento) d[aeo]|me lembr[ae] de)\b", re.IGNORECASE),
    "memoria": re.compile(
        r"(\b(lembr[ae]|anot[ae]|guard[ae]) (que|isso|a[ií])\b|\bn[aã]o esque[cç]a\b|\bvoc[eê] (sabe|lembra)\b|"
        r"\b(esquece|apaga da mem[oó]ria)\b)",
        re.IGNORECASE,
    ),
}


# Grupos que, em PERGUNTA, não obrigam a chamar ferramenta logo de cara: "abre/fecha/volume" aparecem em
# pergunta comum ("como abrir uma conta no banco?"). Em pedido, obrigam (revisão do PR 20).
SEM_INSISTIR = {"pc", "musica", "tv"}


def detectar(texto: str) -> list[str]:
    return [grupo for grupo, p in PADROES.items() if p.search(texto)]


# O 9B tinha o vício de ANUNCIAR a ação ("Vou colocar na agenda...", "Já cancelo...") sem chamar a
# ferramenta, e às vezes imitava a frase de confirmação do sistema ("... Confirma?") sem nada pendente.
# Só futuro/presente ("vou marcar", "já cancelo", "estou buscando"); "já verifiquei" é resposta legítima.
ANUNCIO = re.compile(
    r"\b(vou|j[aá] vou|deixa eu|irei)\s+(te\s+)?(precisar\s+(de\s+)?)?(verificar|checar|olhar|consultar|criar|"
    r"marcar|colocar|agendar|cancelar|apagar|alterar|mudar|lan[cç]ar|registrar|anotar|guardar|buscar|procurar|"
    r"pesquisar|ler os detalhes|ler a p[aá]gina|abrir o (site|link|an[uú]ncio)|"
    r"acender|ligar|desligar|abrir|fechar|tocar|pausar|colocar um timer|programar|rodar|executar|listar|travar|"
    r"reiniciar|suspender)\b"
    r"|\bpreciso\s+(de\s+)?(rodar|executar|abrir)\b"
    r"|\bj[aá]\s+(crio|marco|coloco|agendo|cancelo|apago|altero|mudo|lan[cç]o|registro|anoto|guardo|acendo|ligo|"
    r"desligo)\b"
    r"|\bestou\s+(verificando|checando|olhando|consultando|criando|marcando|agendando|cancelando|apagando|"
    r"alterando|lan[cç]ando|buscando|procurando|pesquisando)\b"
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
