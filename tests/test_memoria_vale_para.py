"""Memória com `vale_para`: o fato aparece nas perguntas em que ele importa, mesmo sem palavra em comum."""

from __future__ import annotations

import sqlite3

import numpy as np

from conftest import EmbedderFalso
from vision.memory.store import Memorias, assuntos, documentos
from vision.tools.memoria import FerramentasMemoria


class EmbedderQueAnota(EmbedderFalso):
    def __init__(self):
        self.textos: list[str] = []

    async def __call__(self, textos):
        self.textos.extend(textos)
        return await super().__call__(textos)


async def test_dica_entra_no_embedding_e_acha_o_fato_pelo_assunto(tmp_path):
    m = Memorias(tmp_path / "m.db", EmbedderFalso())
    await m.lembrar("O Felipe tem uma moto.", "geral")
    sem = await m.buscar("casas de aluguel com garagem", k=1, minimo=0.2)
    assert not sem  # sem a dica, nenhuma palavra em comum
    await m.lembrar("O Felipe tem uma moto.", "geral", vale_para="aluguel de casas, garagem, oficina")
    com = await m.buscar("casas de aluguel com garagem", k=1, minimo=0.2)
    assert com and com[0].texto == "O Felipe tem uma moto."
    assert com[0].vale_para == "aluguel de casas, garagem, oficina"
    assert m.total() == 1  # a dica nova atualizou o mesmo fato
    assert Memorias.formatar(com) == (f"- [{com[0].id}] O Felipe tem uma moto. "
                                      "(pesa em: aluguel de casas, garagem, oficina)")
    m.fechar()


async def test_atualizar_sem_dica_mantem_a_antiga(tmp_path):
    m = Memorias(tmp_path / "m.db", EmbedderFalso())
    await m.lembrar("O Felipe tem uma moto.", vale_para="garagem")
    tipo, mem = await m.lembrar("O Felipe tem uma moto!")
    assert tipo == "atualizada" and mem.vale_para == "garagem"
    assert (await m.buscar("garagem", k=1, minimo=0.2))[0].id == mem.id
    m.fechar()


def test_assuntos_e_documentos():
    assert assuntos(" moradia,  vaga de garagem ; Moradia, oficina.") == ["moradia", "vaga de garagem", "oficina"]
    assert documentos("Fato.") == ["Fato."]
    assert documentos("Fato.", "moradia, garagem") == ["Fato.", "moradia: Fato.", "garagem: Fato."]


async def test_banco_antigo_ganha_a_coluna(tmp_path):
    arq = tmp_path / "velho.db"
    db = sqlite3.connect(arq)
    db.execute("CREATE TABLE memorias (id INTEGER PRIMARY KEY AUTOINCREMENT, texto TEXT NOT NULL, categoria TEXT "
               "NOT NULL DEFAULT 'geral', criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL, embedding BLOB NOT NULL)")
    v = (await EmbedderFalso()(["x: corre às quintas"]))[0]
    db.execute("INSERT INTO memorias (texto, categoria, criado_em, atualizado_em, embedding) VALUES (?,?,?,?,?)",
               ("O Felipe corre às quintas.", "rotina", "2026-09-30", "2026-09-30", v.astype(np.float32).tobytes()))
    db.commit()
    db.close()
    m = Memorias(arq, EmbedderFalso())
    assert m.todas()[0].vale_para == ""
    assert (await m.buscar("corre quintas", k=1))[0].texto == "O Felipe corre às quintas."
    m.fechar()


async def test_pergunta_repetida_nao_vai_ao_ollama(tmp_path):
    e = EmbedderQueAnota()
    m = Memorias(tmp_path / "m.db", e)
    await m.lembrar("O Felipe corre às quintas.")
    antes = len(e.textos)
    await m.buscar("Quando eu corro?")
    await m.buscar("quando eu  corro?")
    assert len(e.textos) == antes + 1
    m.fechar()


async def test_ferramenta_passa_a_dica(memorias):
    f = FerramentasMemoria(memorias)
    await f.lembrar({"fato": "O Felipe tem uma moto.", "vale_para": "garagem, oficina"})
    assert memorias.todas()[0].vale_para == "garagem, oficina"
    params = next(x for x in f.ferramentas() if x.nome == "guardar_memoria").parametros["properties"]
    assert "vale_para" in params
