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
        ("gastou R$ 482,90", "gastou 482 reais e 90 centavos"),
        ("R$ 1.250,00 no total", "1250 reais no total"),
        ("R$ 1,00", "um real"),
        ("**Dentista** amanhã 🦷", "Dentista amanhã"),
        ("- Aula (id: ev_123)\n- Treino", "Aula. Treino"),
        ("1. **Imovelweb**, 21 casas: https://www.imovelweb.com.br/casas.html\n2. Veja a [OLX](https://olx.com.br/x)",
         "1. Imovelweb, 21 casas. 2. Veja a OLX"),
        ("No site (www.olx.com.br) tem mais.", "No site tem mais."),
        ("Motor 1.7L de 130 cv, faz 12 km/l e chega a 190 km/h.",
         "Motor 1,7 litro de 130 cavalos, faz 12 quilômetros por litro e chega a 190 quilômetros por hora."),
        ("Hoje 25°C, umidade 80%.", "Hoje 25 graus, umidade 80 por cento."),
        ("Casa de 120 m² por R$ 1.250,00.", "Casa de 120 metros quadrados por 1250 reais."),
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
