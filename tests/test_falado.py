import pytest

from jarvis.voice.falado import frases, para_fala


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
    ],
)
def test_para_fala(entrada, saida):
    assert para_fala(entrada) == saida


def test_corrige_jarvis_ouvido_errado():
    from jarvis.voice.stt import corrigir_nomes

    assert corrigir_nomes("Jarves, quanto eu gastei com iFood esse mês?") == "Jarvis, quanto eu gastei com iFood esse mês?"
    assert corrigir_nomes("ei jarvi qual minha agenda") == "ei Jarvis qual minha agenda"
    assert corrigir_nomes("o Jarvis está aqui") == "o Jarvis está aqui"


def test_frases():
    assert frases("Oi, Felipe. Hoje tem aula! E amanhã?") == ["Oi, Felipe.", "Hoje tem aula!", "E amanhã?"]
