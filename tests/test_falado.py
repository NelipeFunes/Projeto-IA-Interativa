import pytest

from vision.voice.falado import frases, para_fala


@pytest.mark.parametrize(
    "entrada,saida",
    [
        ("às 14:30", "às 14 e 30"),
        ("às 14h30", "às 14 e 30"),
        ("às 14h", "às 14 horas"),
        ("das 8:00 às 12:00", "das 8 horas ao meio-dia"),
        ("às 1:00", "à 1 hora"),
        ("às 0:00", "à meia-noite"),
        ("amanhã 09:05", "amanhã 9 e 5"),
        ("sex 02/10 das 10:00 às 11:00", "sexta, dia 2 de outubro das 10 horas às 11 horas"),
        ("dia 12/10/2026", "dia 12 de outubro"),
        ("feriado em 12/10", "feriado em dia 12 de outubro"),
        ("gastou R$ 482,90", "gastou quatrocentos e oitenta e dois reais e noventa centavos"),
        ("R$ 1.250,00 no total", "mil duzentos e cinquenta reais no total"),
        ("R$ 1,00", "um real"),
        ("**Dentista** amanhã 🦷", "Dentista amanhã"),
        ("- Aula (id: ev_123)\n- Treino", "Aula. Treino"),
        ("1. **Imovelweb**, 21 casas: https://www.imovelweb.com.br/casas.html\n2. Veja a [OLX](https://olx.com.br/x)",
         "1. Imovelweb, 21 casas. 2. Veja a OLX"),
        ("No site (www.olx.com.br) tem mais.", "No site tem mais."),
        ("Motor 1.7L de 130 cv, faz 12 km/l e chega a 190 km/h.",
         "Motor 1,7 litro de 130 cavalos, faz 12 quilômetros por litro e chega a 190 quilômetros por hora."),
        ("Hoje 25°C, umidade 80%.", "Hoje 25 graus, umidade 80 por cento."),
        ("Casa de 120 m² por R$ 1.250,00.", "Casa de 120 metros quadrados por mil duzentos e cinquenta reais."),
        ("Faltam 1 km e 5 min.", "Faltam 1 quilômetro e 5 minutos."),
        ("12 x 8 dá 96", "12 vezes 8 dá 96"),
        ("Prédio nº 45, 2 Lugares", "Prédio número 45, 2 Lugares"),
        ("Ficou em 1º lugar no 2º andar.", "Ficou em 1º lugar no 2º andar."),
        ("Versão 1.2.3 de 02.10.2026", "Versão 1.2.3 de 02.10.2026"),
        ("Tração 4x4, tela 1920x1080", "Tração 4 por 4, tela 1920 por 1080"),
    ],
)
def test_para_fala(entrada, saida):
    assert para_fala(entrada) == saida


def test_corrige_jarvis_ouvido_errado():
    from vision.voice.stt import corrigir_nomes

    assert corrigir_nomes("Jarves, quanto eu gastei com iFood esse mês?") == "Jarvis, quanto eu gastei com iFood esse mês?"
    assert corrigir_nomes("ei jarvi qual minha agenda") == "ei Jarvis qual minha agenda"
    assert corrigir_nomes("o Jarvis está aqui") == "o Jarvis está aqui"


def test_frases():
    assert frases("Oi, Felipe. Hoje tem aula! E amanhã?") == ["Oi, Felipe.", "Hoje tem aula!", "E amanhã?"]


@pytest.mark.parametrize("entrada,saida", [
    ("Use 1/2 litro de leite.", "Use 1/2 litro de leite."),             # fração de quantidade, não 1º de fevereiro
    ("Falta 3/4 de xícara de açúcar.", "Falta 3/4 de xícara de açúcar."),
    ("Dê 1/2 hora de descanso.", "Dê 1/2 hora de descanso."),
    ("A prova é dia 1/2.", "A prova é dia 1 de fevereiro."),            # com "dia" na frente é data
    ("Reunião 5/10 às 14h.", "Reunião dia 5 de outubro às 14 horas."),
    ("Nasceu em 1/2/2001.", "Nasceu em dia 1 de fevereiro."),            # com ano é data
    ("Consulta 20/10 Unidade Centro.", "Consulta dia 20 de outubro Unidade Centro."),   # "Unidade" com maiúscula é nome
    ("Sáb 15/11 Gramado.", "Sáb dia 15 de novembro Gramado."),                          # "Gramado" não é "gramas"
    ("sex 16/10 Horas extras", "sexta, dia 16 de outubro Horas extras"),                # dia da semana na frente: data
    ("1/2 dúzia de ovos", "1/2 dúzia de ovos"),
    ("1/3 do total", "1/3 do total"),
    ("1/2 xicara de açúcar", "1/2 xicara de açúcar"),
])
def test_fracao_de_quantidade_nao_vira_data(entrada, saida):
    assert para_fala(entrada) == saida


@pytest.mark.parametrize("n, esperado", [
    (0, "zero"), (1, "um"), (2, "dois"), (10, "dez"), (15, "quinze"), (21, "vinte e um"), (99, "noventa e nove"), (100, "cem"),
    (101, "cento e um"), (110, "cento e dez"), (387, "trezentos e oitenta e sete"), (999, "novecentos e noventa e nove"),
    (1000, "mil"), (1001, "mil e um"), (1100, "mil e cem"), (1234, "mil duzentos e trinta e quatro"), (2000, "dois mil"),
    (2500, "dois mil e quinhentos"), (12345, "doze mil trezentos e quarenta e cinco"), (100000, "cem mil"),
    (1000000, "um milhão"), (1000100, "um milhão e cem"), (2000000, "dois milhões"), (2000001, "dois milhões e um"),
    (1234567, "um milhão duzentos e trinta e quatro mil quinhentos e sessenta e sete"), (1000000000, "um bilhão"),
])
def test_numeros_por_extenso(n, esperado):
    from vision.voice.falado import por_extenso

    assert por_extenso(n) == esperado


def test_por_extenso_bate_com_o_num2words_em_milhares_de_numeros():
    """Gabarito independente: o num2words (que o XTTS já traz) em português do Brasil, sem as vírgulas entre os grupos.
    Pulado (em silêncio) se o grupo xtts do projeto não estiver instalado: aí valem só os casos escritos à mão."""
    num2words = pytest.importorskip("num2words").num2words
    from vision.voice.falado import por_extenso

    # Até 1 milhão, tudo (20 mil seguidos e depois de 997 em 997). Acima, só os casos sem ambiguidade: acima de 1 milhão com
    # resto complicado ("um milhão e mil e quarenta e oito" ou "um milhão mil e quarenta e oito") as duas formas se usam.
    amostra = list(range(0, 20001)) + list(range(20000, 1_000_000, 997)) + [
        10**6, 10**6 + 1, 10**6 + 100, 2 * 10**6, 2 * 10**6 + 300, 10**9, 10**9 + 5, 3 * 10**9]
    for n in amostra:
        assert por_extenso(n) == num2words(n, lang="pt_BR").replace(",", ""), n


def test_fora_do_alcance_fica_em_digitos():
    from vision.voice.falado import por_extenso

    assert por_extenso(10**12) == "1000000000000" and por_extenso(-5) == "-5"


@pytest.mark.parametrize("texto, esperado", [
    ("custou R$ 1.234,56", "custou mil duzentos e trinta e quatro reais e cinquenta e seis centavos"),
    ("R$ 12.345,67", "doze mil trezentos e quarenta e cinco reais e sessenta e sete centavos"),
    ("R$ 1,00", "um real"),
    ("R$ 0,01", "um centavo"),
    ("R$ 0,50", "cinquenta centavos"),
    ("R$ 2.500,00.", "dois mil e quinhentos reais."),
    ("R$ 387,90.", "trezentos e oitenta e sete reais e noventa centavos."),
    ("R$ 1.000.000,00", "um milhão de reais"),
    ("R$ 2.000.000,00", "dois milhões de reais"),
    ("R$ 1.000.100,00", "um milhão e cem reais"),
])
def test_valores_em_reais_vao_por_extenso(texto, esperado):
    assert para_fala(texto) == esperado


def test_outros_numeros_ficam_em_digito():
    """Hora, porcentagem e número com ponto já saíam certos nos dois motores; não mexe (o gênero de 'duas mil' poderia errar)."""
    assert para_fala("às 14h30, dia 21, 18%, 22 graus, 2.000 pessoas") == "às 14 e 30, dia 21, 18 por cento, 22 graus, 2.000 pessoas"


@pytest.mark.parametrize("texto, esperado", [
    # revisão do PR 69: a troca por palavras não pode derrubar o que vinha depois e dependia dos dígitos
    ("Sai por 12 x R$ 83,25 sem juros.", "Sai por 12 vezes oitenta e três reais e vinte e cinco centavos sem juros."),
    ("3 x R$ 1.000,00", "3 vezes mil reais"),
    ("Seu saldo ficou em -R$ 1.250,00.", "Seu saldo ficou em menos mil duzentos e cinquenta reais."),
    ("saldo de - R$ 50,00", "saldo de até cinquenta reais"),  # hífen com espaços é travessão (vira "até", como sempre), não menos
    ("saldo de -R$ 0,50", "saldo de menos cinquenta centavos"),
])
def test_valores_com_parcela_e_sinal(texto, esperado):
    assert para_fala(texto) == esperado


@pytest.mark.parametrize("texto", ["Os preços estão em R$.", "convertido para R$.", "R$", "R$ ,50", "R$.5", "valores em R$ e US$."])
def test_cifrao_sem_numero_nao_derruba_a_fala(texto):
    """Antes: `R$.` levantava ValueError e o laço de voz não falava o resto da resposta."""
    assert "R$" in para_fala(texto)  # não levanta, e o texto fica como estava


def test_ponto_final_depois_do_valor_continua_la():
    assert para_fala("Gastou R$ 50. Depois parou.") == "Gastou cinquenta reais. Depois parou."


@pytest.mark.parametrize("texto, esperado", [
    ("O carro custa R$ 120 mil", "O carro custa cento e vinte mil reais"),
    ("R$ 1,5 milhão", "um vírgula cinco milhão de reais"),
    ("R$ 1,05 milhão", "um vírgula zero cinco milhão de reais"),
    ("R$ 2 milhões", "dois milhões de reais"),
    ("R$ 3 bilhões", "três bilhões de reais"),
    ("saldo -R$ 15 mil", "saldo menos quinze mil reais"),
])
def test_valor_grande_com_mil_milhao_bilhao(texto, esperado):
    assert para_fala(texto) == esperado
