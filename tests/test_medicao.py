"""Medição das ferramentas (T-018, etapa 1): duração, resultado, cache simulado e privacidade."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import pytest

from vision import cli, medicao
from vision.medicao import Medidor
from vision.montagem import montar
from vision.tools.base import ErroFerramenta, Ferramenta, Registro, esquema, texto


def _ferramenta(nome: str, executar, prazo_s=None, escrita=False) -> Ferramenta:
    return Ferramenta(nome=nome, descricao="x", parametros=esquema({"q": texto("q")}), executar=executar,
                      prazo_s=prazo_s, escrita=escrita)


class Relogio:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _linhas(pasta):
    return [json.loads(linha) for a in sorted(pasta.glob("*.jsonl")) for linha in a.read_text(encoding="utf-8").splitlines()]


def test_registra_nome_duracao_resultado_e_so_a_impressao_dos_argumentos(tmp_path):
    m = Medidor(tmp_path / "med")
    m.registrar("web_buscar", {"consulta": "meu segredo de teste"}, 1.234, True)
    gravado = "".join(a.read_text(encoding="utf-8") for a in (tmp_path / "med").glob("*.jsonl"))
    assert "segredo" not in gravado  # o conteúdo dos argumentos não é gravado em texto
    d = json.loads(gravado.splitlines()[0])
    assert d["f"] == "web_buscar" and d["ms"] == 1234 and d["ok"] is True and len(d["h"]) == 8


def test_mesma_pergunta_tem_a_mesma_impressao_e_outra_pergunta_nao(tmp_path):
    m = Medidor(tmp_path / "med")
    assert m.impressao({"a": 1, "b": 2}) == m.impressao({"b": 2, "a": 1})
    assert m.impressao({"a": 1}) != m.impressao({"a": 2})


def test_o_sal_e_da_instalacao_e_sobrevive_a_reinicio(tmp_path):
    h1 = Medidor(tmp_path / "med").impressao({"a": 1})
    assert Medidor(tmp_path / "med").impressao({"a": 1}) == h1  # mesmo sal gravado
    assert Medidor(tmp_path / "outra").impressao({"a": 1}) != h1  # outra instalação, outro sal


def test_dois_medidores_sem_sal_acabam_com_o_mesmo_sal(tmp_path):
    a, b = Medidor(tmp_path / "med"), Medidor(tmp_path / "med")
    a._criar_pasta()
    assert a.impressao({"x": 1}) == b.impressao({"x": 1})  # o segundo reaproveita o sal que o primeiro criou
    assert [p.name for p in (tmp_path / "med").iterdir()] == ["sal.txt"]  # sem temporário sobrando


def test_args_none_nao_grava_impressao(tmp_path):
    Medidor(tmp_path / "med").registrar("tv_controle", None, 0.1, True)
    assert _linhas(tmp_path / "med")[0]["h"] == ""
    assert not (tmp_path / "med" / "sal.txt").exists()  # nem criou o sal


def test_disco_com_problema_nao_levanta_e_avisa_uma_vez(tmp_path, caplog):
    arquivo = tmp_path / "med"
    arquivo.write_text("sou um arquivo, não uma pasta", encoding="utf-8")
    m = Medidor(arquivo)
    with caplog.at_level(logging.WARNING, logger="vision.medicao"):
        for _ in range(3):
            m.registrar("x", {}, 0.1, True)  # não levanta
    assert len([r for r in caplog.records if "medição" in r.getMessage()]) == 1


def test_apaga_o_que_passou_dos_dias_e_repete_a_poda_quando_o_dia_muda(tmp_path):
    pasta = tmp_path / "med"
    pasta.mkdir()
    rel = Relogio(datetime(2026, 10, 14, 23, 30).timestamp())
    (pasta / "2026-08-01.jsonl").write_text("{}\n", encoding="utf-8")
    (pasta / "nada-a-ver.jsonl").write_text("{}\n", encoding="utf-8")
    m = Medidor(pasta, dias=30, relogio=rel)
    m.registrar("x", {}, 0.1, True)
    assert not (pasta / "2026-08-01.jsonl").exists()
    assert (pasta / "nada-a-ver.jsonl").exists()  # nome fora do padrão: não mexe
    (pasta / "2026-09-10.jsonl").write_text("{}\n", encoding="utf-8")  # velho só amanhã
    m.registrar("x", {}, 0.1, True)
    assert (pasta / "2026-09-10.jsonl").exists()  # mesmo dia: não varre de novo
    rel.t += 3600  # virou o dia (00:30 do dia 15): o núcleo ligado há semanas poda de novo
    m.registrar("x", {}, 0.1, True)
    assert (pasta / "2026-10-15.jsonl").exists()
    assert not (pasta / "2026-09-10.jsonl").exists()


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
    assert {(d["f"], d["ok"]) for d in medicao.ler(tmp_path / "med")} == {("boa", True), ("ruim", False),
                                                                            ("lenta", False)}


async def test_cancelamento_de_fora_propaga_e_nao_conta_como_falha(tmp_path):
    reg = Registro()
    reg.medidor = Medidor(tmp_path / "med")

    async def lenta(args):
        await asyncio.sleep(5)
        return "tarde"

    reg.adicionar(_ferramenta("lenta", lenta))  # sem prazo próprio: quem cancela é de fora (prazo do protocolo)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reg.rodar("lenta", {"q": "1"}), 0.05)
    assert medicao.ler(tmp_path / "med") == []


async def test_ferramenta_de_escrita_mede_sem_impressao(tmp_path):
    reg = Registro()
    reg.medidor = Medidor(tmp_path / "med")

    async def escreve(args):
        return "feito"

    reg.adicionar(_ferramenta("tv_controle", escreve, escrita=True))
    await reg.rodar("tv_controle", {"q": "desligar"})
    d = _linhas(tmp_path / "med")[0]
    assert d["f"] == "tv_controle" and d["h"] == ""


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


async def test_montagem_liga_o_medidor_e_o_config_desliga(cfg, host, memorias):
    async with montar(cfg, host=host, memorias=memorias) as j:
        assert isinstance(j.registro.medidor, Medidor)
    cfg.bruto["medicao"] = {"ativo": False}
    async with montar(cfg, host=host, memorias=memorias) as j:
        assert j.registro.medidor is None


def _grava(m: Medidor, ferramenta: str, args: dict | None, ms: int, ok: bool = True) -> None:
    m.registrar(ferramenta, args, ms / 1000, ok)


def _resumo(pasta, agora):
    return {r["ferramenta"]: r for r in medicao.resumir(medicao.ler(pasta, agora=agora))}


def test_cache_simulado_conta_acertos_e_economia(tmp_path):
    agora = datetime(2026, 10, 14, 12, 0).timestamp()
    rel = Relogio(agora)
    m = Medidor(tmp_path / "med", relogio=rel)
    _grava(m, "orbit_tarefas", {"x": 1}, 4000)   # falta: preenche
    rel.t += 60
    _grava(m, "orbit_tarefas", {"x": 1}, 3000)   # 1 min: acerto nas duas janelas
    rel.t += 1200
    _grava(m, "orbit_tarefas", {"x": 1}, 2000)   # 21 min do preenchimento: venceu a de 5 min, vale a de 1 h
    rel.t += 10
    _grava(m, "orbit_tarefas", {"x": 2}, 1000)   # outro argumento: falta
    _grava(m, "falha", {"x": 1}, 500, ok=False)
    rel.t += 5
    _grava(m, "falha", {"x": 1}, 500)            # a anterior falhou: não preencheu nada
    o = _resumo(tmp_path / "med", rel.t)["orbit_tarefas"]
    assert o["chamadas"] == 4 and o["repetidas"] == [1, 2] and o["economia_s"] == [3.0, 5.0]
    assert o["mediana_ms"] == 2500 and o["sem_erro"] == 1.0  # mediana de 4 valores: média dos dois do meio
    f = _resumo(tmp_path / "med", rel.t)["falha"]
    assert f["repetidas"] == [0, 0] and f["sem_erro"] == 0.5


def test_pergunta_a_cada_4_minutos_nao_acerta_todas_num_cache_de_5(tmp_path):
    """O acerto não renova o prazo: com validade de 5 min, a de 4 em 4 min acerta uma sim, uma não."""
    rel = Relogio(datetime(2026, 10, 14, 12, 0).timestamp())
    m = Medidor(tmp_path / "med", relogio=rel)
    for _ in range(7):  # t = 0, 4, 8, 12, 16, 20, 24 min
        _grava(m, "clima_ver", {"d": 1}, 800)
        rel.t += 240
    r = _resumo(tmp_path / "med", rel.t)["clima_ver"]
    assert r["repetidas"][0] == 3  # 4 min (acerto), 8 (falta), 12 (acerto), 16 (falta), 20 (acerto), 24 (falta)
    assert r["repetidas"][1] == 6  # com 1 h, todas as seis depois da primeira


def test_fronteira_de_5_minutos_e_incluida_e_escrita_fica_de_fora(tmp_path):
    rel = Relogio(datetime(2026, 10, 14, 12, 0).timestamp())
    m = Medidor(tmp_path / "med", relogio=rel)
    _grava(m, "a", {"x": 1}, 100)
    rel.t += 300
    _grava(m, "a", {"x": 1}, 100)      # exatamente 300 s: acerto
    _grava(m, "tv_controle", None, 100)
    _grava(m, "tv_controle", None, 100)  # sem impressão: nunca conta como repetida
    r = _resumo(tmp_path / "med", rel.t)
    assert r["a"]["repetidas"] == [1, 1] and r["tv_controle"]["repetidas"] == [0, 0]


def test_percentis(tmp_path):
    assert medicao._percentil([5, 1, 3], 0.5) == 3
    assert medicao._percentil([7], 0.95) == 7
    assert medicao._percentil(list(range(1, 101)), 0.95) == 96


def test_ler_ignora_linha_estragada_e_o_que_passou_da_janela(tmp_path):
    pasta = tmp_path / "med"
    pasta.mkdir()
    agora = datetime(2026, 10, 14, 12, 0).timestamp()
    (pasta / "2026-10-14.jsonl").write_text(
        'lixo\n{"t": %d, "f": "a", "ms": 5, "ok": true, "h": "abc"}\n{"t": 1, "f": "velha", "ms": 5, "ok": true, '
        '"h": "x"}\n[1,2]\n{"t": "x"}\n' % agora, encoding="utf-8")
    assert [d["f"] for d in medicao.ler(pasta, dias=7, agora=agora)] == ["a"]


def test_relatorio_sem_dados_e_com_varias_ferramentas_na_ordem_do_tempo_total(tmp_path):
    assert "Sem medições" in medicao.relatorio(tmp_path / "med")
    m = Medidor(tmp_path / "med")
    m.registrar("rapida", {"d": 1}, 0.1, True)
    m.registrar("lenta", {"d": 1}, 9.0, True)
    r = medicao.relatorio(tmp_path / "med")
    assert "2 chamadas" in r and r.index("lenta") < r.index("rapida")


def test_comando_medicao_valida_os_dias_e_imprime(cfg, monkeypatch, capsys):
    monkeypatch.setattr(cli.config, "carregar", lambda **_: cfg)
    with pytest.raises(SystemExit):
        cli.main(["medicao", "--dias", "0"])
    capsys.readouterr()
    assert cli.main(["medicao", "--dias", "3"]) == 0
    assert "Sem medições" in capsys.readouterr().out
    Medidor(cfg.dados / "medicoes").registrar("clima_ver", {"d": 1}, 0.5, True)
    assert cli.main(["medicao"]) == 0
    assert "clima_ver" in capsys.readouterr().out
