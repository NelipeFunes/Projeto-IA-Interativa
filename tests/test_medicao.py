"""Medição das ferramentas (T-018, etapa 1): duração, resultado, repetição e privacidade."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from vision import medicao
from vision.medicao import Medidor
from vision.tools.base import ErroFerramenta, Ferramenta, Registro, esquema, texto


def _ferramenta(nome: str, executar, prazo_s=None) -> Ferramenta:
    return Ferramenta(nome=nome, descricao="x", parametros=esquema({"q": texto("q")}), executar=executar,
                      prazo_s=prazo_s)


class Relogio:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_registra_nome_duracao_resultado_e_so_a_impressao_dos_argumentos(tmp_path):
    m = Medidor(tmp_path / "med")
    m.registrar("web_buscar", {"consulta": "meu segredo de teste"}, 1.234, True)
    texto_gravado = "".join(a.read_text(encoding="utf-8") for a in (tmp_path / "med").glob("*.jsonl"))
    assert "segredo" not in texto_gravado  # o conteúdo dos argumentos nunca é gravado
    d = json.loads(texto_gravado.splitlines()[0])
    assert d["f"] == "web_buscar" and d["ms"] == 1234 and d["ok"] is True and len(d["h"]) == 8


def test_mesma_pergunta_tem_a_mesma_impressao_e_outra_pergunta_nao(tmp_path):
    m = Medidor(tmp_path / "med")
    assert m.impressao({"a": 1, "b": 2}) == m.impressao({"b": 2, "a": 1})
    assert m.impressao({"a": 1}) != m.impressao({"a": 2})


def test_o_sal_e_da_instalacao_e_sobrevive_a_reinicio(tmp_path):
    h1 = Medidor(tmp_path / "med").impressao({"a": 1})
    assert Medidor(tmp_path / "med").impressao({"a": 1}) == h1  # mesmo sal gravado
    assert Medidor(tmp_path / "outra").impressao({"a": 1}) != h1  # outra instalação, outro sal


def test_disco_com_problema_nao_levanta(tmp_path):
    arquivo = tmp_path / "med"
    arquivo.write_text("sou um arquivo, não uma pasta", encoding="utf-8")
    Medidor(arquivo).registrar("x", {}, 0.1, True)  # não levanta


def test_apaga_o_que_passou_dos_dias(tmp_path):
    pasta = tmp_path / "med"
    pasta.mkdir()
    (pasta / "2020-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    (pasta / "nada-a-ver.jsonl").write_text("{}\n", encoding="utf-8")
    Medidor(pasta, dias=30).registrar("x", {}, 0.1, True)
    assert not (pasta / "2020-01-01.jsonl").exists()
    assert (pasta / "nada-a-ver.jsonl").exists()  # nome fora do padrão: não mexe


async def test_registro_mede_chamada_boa_erro_e_timeout(tmp_path):
    reg = Registro()
    reg.medidor = Medidor(tmp_path / "med")

    async def boa(args):
        return "ok"

    async def ruim(args):
        raise ErroFerramenta("sem login")

    async def lenta(args):
        await asyncio.sleep(5)
        return "tarde"

    reg.adicionar(_ferramenta("boa", boa), _ferramenta("ruim", ruim), _ferramenta("lenta", lenta, prazo_s=0.05))
    assert (await reg.rodar("boa", {"q": "1"}))[0] is True
    assert (await reg.rodar("ruim", {"q": "1"}))[0] is False
    assert (await reg.rodar("lenta", {"q": "1"}))[0] is False
    itens = medicao.ler(tmp_path / "med")
    assert {(d["f"], d["ok"]) for d in itens} == {("boa", True), ("ruim", False), ("lenta", False)}


async def test_ferramenta_inexistente_nao_e_medida_e_sem_medidor_funciona(tmp_path):
    reg = Registro()
    reg.medidor = Medidor(tmp_path / "med")
    assert (await reg.rodar("nao_existe", {}))[0] is False
    assert medicao.ler(tmp_path / "med") == []

    async def boa(args):
        return "ok"

    sem = Registro()
    sem.adicionar(_ferramenta("boa", boa))
    assert (await sem.rodar("boa", {"q": "x"}))[0] is True


async def test_medidor_quebrado_nao_derruba_a_ferramenta():
    class Quebrado:
        def registrar(self, *a):
            raise RuntimeError("boom")

    async def boa(args):
        return "ok"

    reg = Registro()
    reg.medidor = Quebrado()
    reg.adicionar(_ferramenta("boa", boa))
    assert await reg.rodar("boa", {"q": "x"}) == (True, "ok")


def _grava(m: Medidor, rel: Relogio, ferramenta: str, args: dict, ms: int, ok: bool = True) -> None:
    m.registrar(ferramenta, args, ms / 1000, ok)


def test_resumo_conta_repetidas_so_as_que_ja_tinham_dado_certo(tmp_path):
    agora = datetime(2026, 10, 14, 12, 0).timestamp()
    rel = Relogio(agora)
    m = Medidor(tmp_path / "med", relogio=rel)
    _grava(m, rel, "orbit_tarefas", {"x": 1}, 4000)        # primeira: não é repetida
    rel.t += 60
    _grava(m, rel, "orbit_tarefas", {"x": 1}, 3000)        # repetida em 1 min: cabe nas duas janelas
    rel.t += 1200
    _grava(m, rel, "orbit_tarefas", {"x": 1}, 2000)        # 20 min depois: só na janela de 1 h
    rel.t += 10
    _grava(m, rel, "orbit_tarefas", {"x": 2}, 1000)        # outro argumento: não repete
    _grava(m, rel, "falha", {"x": 1}, 500, ok=False)
    rel.t += 5
    _grava(m, rel, "falha", {"x": 1}, 500)                 # a anterior falhou: não conta como repetida
    linhas = {r["ferramenta"]: r for r in medicao.resumir(medicao.ler(tmp_path / "med", agora=rel.t))}
    o = linhas["orbit_tarefas"]
    assert o["chamadas"] == 4 and o["repetidas"] == [1, 2]
    assert o["economia_s"] == [3.0, 5.0]
    assert o["mediana_ms"] == 3000 and o["acertos"] == 1.0
    assert linhas["falha"]["repetidas"] == [0, 0] and linhas["falha"]["acertos"] == 0.5


def test_ler_ignora_linha_estragada_e_o_que_passou_da_janela(tmp_path):
    pasta = tmp_path / "med"
    pasta.mkdir()
    agora = datetime(2026, 10, 14, 12, 0).timestamp()
    (pasta / "2026-10-14.jsonl").write_text(
        'lixo\n{"t": %d, "f": "a", "ms": 5, "ok": true, "h": "abc"}\n{"t": 1, "f": "velha", "ms": 5, "ok": true, '
        '"h": "x"}\n[1,2]\n{"t": "x"}\n' % agora, encoding="utf-8")
    assert [d["f"] for d in medicao.ler(pasta, dias=7, agora=agora)] == ["a"]


def test_relatorio_sem_dados_e_com_dados(tmp_path):
    assert "Sem medições" in medicao.relatorio(tmp_path / "med")
    m = Medidor(tmp_path / "med")
    m.registrar("clima_ver", {"d": 1}, 0.8, True)
    r = medicao.relatorio(tmp_path / "med")
    assert "clima_ver" in r and "1 chamadas" in r
