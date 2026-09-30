import pytest

from jarvis.brain import confirmacao, intencao
from jarvis.tools.memoria import FerramentasMemoria


async def test_lembrar_buscar_esquecer(memorias):
    f = FerramentasMemoria(memorias)
    await f.lembrar({"fato": "O Felipe tem uma cachorra chamada Luna.", "categoria": "pessoa"})
    await f.lembrar({"fato": "O Felipe estuda Cálculo às terças.", "categoria": "estudo"})
    assert memorias.total() == 2
    achadas = await memorias.buscar("cachorra Luna", k=1)
    assert "Luna" in achadas[0].texto
    assert "Luna" in await f.descrever_esquecer({"consulta": "cachorra Luna"})
    await f.esquecer({"consulta": "cachorra Luna"})
    assert memorias.total() == 1


async def test_quase_duplicata_atualiza(memorias):
    await memorias.lembrar("O Felipe tem uma cachorra chamada Luna.")
    tipo, _ = await memorias.lembrar("O Felipe tem uma cachorra chamada Luna!")
    assert tipo == "atualizada" and memorias.total() == 1


async def test_memoria_persiste_entre_sessoes(tmp_path):
    from conftest import EmbedderFalso

    from jarvis.memory.store import Memorias

    m1 = Memorias(tmp_path / "m.db", EmbedderFalso())
    await m1.lembrar("O Felipe corre às quintas.")
    m1.fechar()
    m2 = Memorias(tmp_path / "m.db", EmbedderFalso())
    assert (await m2.buscar("quando o Felipe corre", k=1))[0].texto == "O Felipe corre às quintas."
    m2.fechar()


@pytest.mark.parametrize(
    "frase,esperado",
    [
        ("sim", "sim"), ("Sim, pode.", "sim"), ("pode sim, Jarvis", "sim"), ("beleza", "sim"), ("isso mesmo!", "sim"),
        ("não", "nao"), ("Não.", "nao"), ("cancela", "nao"), ("deixa pra lá", "nao"),
        ("não, às 17h", "outro"), ("na verdade muda pra sexta", "outro"), ("qual minha agenda?", "outro"),
    ],
)
def test_classificar_confirmacao(frase, esperado):
    assert confirmacao.classificar(frase) == esperado


@pytest.mark.parametrize("frase", ["isso", "Claro.", "Certo.", "Beleza.", "Vai.", "ok", "isso mesmo", "Pode."])
def test_por_voz_fala_solta_da_tv_nao_confirma(frase):
    assert confirmacao.classificar(frase) == "sim"  # digitado vale
    assert confirmacao.classificar(frase, estrito=True) == "outro"  # por voz, não


@pytest.mark.parametrize("frase", ["Sim.", "Sim, pode.", "Pode sim.", "Confirmo.", "Pode criar.", "Sim, Vision",
                                   "Sim, por favor.", "Sim, claro."])
def test_por_voz_confirmacao_explicita_vale(frase):
    assert confirmacao.classificar(frase, estrito=True) == "sim"


@pytest.mark.parametrize("frase", ["Sim, cancela", "Sim mas às 17h", "sim, vou ali buscar"])
def test_por_voz_sim_seguido_de_outra_coisa_nao_confirma(frase):
    assert confirmacao.classificar(frase, estrito=True) == "outro"


@pytest.mark.parametrize(
    "frase,grupo",
    [
        ("qual minha agenda de hoje?", "agenda"),
        ("o que eu tenho amanhã?", "agenda"),
        ("marca dentista sexta às 10", "agenda"),
        ("quanto eu gastei com ifood esse mês?", "financas"),
        ("lembra que eu prefiro café sem açúcar", "memoria"),
        ("quais minhas tarefas?", "tarefas"),
    ],
)
def test_intencao(frase, grupo):
    assert grupo in intencao.detectar(frase)


def test_conversa_fiada_sem_intencao():
    assert intencao.detectar("me conta uma piada") == []
