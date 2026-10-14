"""Cache único (T-018, etapa 2): validade, tamanho, arquivo, versão, estatísticas e os usos migrados."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import httpx

from vision import cache as modulo_cache
from vision import tempo
from vision.cache import Cache
from vision.clima import VALIDADE_S, Clima
from vision.web import VALIDADE_MANCHETES_S, Web


class Relogio:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


# ------------------------------------------------------------------ o módulo

def test_valor_vale_ate_a_validade_e_depois_some():
    rel = Relogio()
    c = Cache(3600, relogio=rel)
    c.guardar("a", {"x": 1})
    rel.t = 3599
    assert c.pegar("a") == {"x": 1}
    rel.t = 3600  # exatamente a validade ainda vale (como o cache da web e o da agenda sempre fizeram)
    assert c.pegar("a") == {"x": 1}
    rel.t = 3601
    assert c.pegar("a") is None
    assert c.pegar("nunca-guardada") is None


def test_validade_so_desta_leitura_e_validade_nenhuma():
    rel = Relogio()
    c = Cache(6 * 3600, relogio=rel)
    c.guardar("manchetes", ["a"])
    rel.t = 1801
    assert c.pegar("manchetes", validade_s=1800) is None  # mesma entrada, validade menor só nesta leitura
    assert c.pegar("manchetes") == ["a"]
    sempre = Cache(None, relogio=rel)
    sempre.guardar("k", 1)
    rel.t = 10**9
    assert sempre.pegar("k") == 1


def test_valores_falsos_nao_se_confundem_com_falta():
    c = Cache(60, relogio=Relogio())
    for chave, valor in {"zero": 0, "vazio": "", "lista": [], "dic": {}, "falso": False}.items():
        c.guardar(chave, valor)
        assert c.pegar(chave) == valor and c.pegar(chave) is not None


def test_acima_do_maximo_saem_as_mais_velhas():
    rel = Relogio()
    c = Cache(3600, maximo=2, relogio=rel)
    c.guardar("a", 1)
    rel.t = 10
    c.guardar("b", 2)
    rel.t = 20
    c.guardar("c", 3)
    assert c.pegar("a") is None and c.pegar("b") == 2 and c.pegar("c") == 3 and len(c) == 2


def test_apagar_e_limpar_por_prefixo():
    c = Cache(60, relogio=Relogio())
    for k in ("busca:a", "busca:b", "ler:x"):
        c.guardar(k, 1)
    c.apagar("ler:x")
    assert c.pegar("ler:x") is None and len(c) == 2
    assert c.limpar("busca:a") == 1 and len(c) == 1
    assert c.limpar() == 1 and len(c) == 0 and c.limpar() == 0


def test_pegar_velho_devolve_o_valor_vencido_com_a_idade():
    rel = Relogio()
    c = Cache(60, relogio=rel)
    c.guardar("a", "dado")
    rel.t = 1200
    assert c.pegar("a") is None  # venceu
    assert c.pegar_velho("a") == ("dado", 1200)  # mas dá para dizer "dado de 20 min atrás" se a fonte falhar
    assert c.pegar_velho("outra") is None


def test_estatisticas_contam_acertos_e_faltas():
    c = Cache(60, relogio=Relogio(), nome="teste")
    assert c.estatisticas() == {"nome": "teste", "acertos": 0, "faltas": 0, "taxa": None, "entradas": 0}
    c.guardar("a", 1)
    c.pegar("a")
    c.pegar("a")
    c.pegar("b")
    e = c.estatisticas()
    assert (e["acertos"], e["faltas"], e["entradas"]) == (2, 1, 1) and abs(e["taxa"] - 2 / 3) < 1e-9


# ------------------------------------------------------------------ em arquivo

def test_arquivo_sobrevive_a_reinicio_e_nao_aparece_sem_pedir(tmp_path):
    rel = Relogio(1000)
    c = Cache(3600, arquivo=tmp_path / "c.json", relogio=rel)
    c.guardar("a", {"x": 1})
    assert Cache(3600, arquivo=tmp_path / "c.json", relogio=rel).pegar("a") == {"x": 1}
    Cache(3600, relogio=rel).guardar("b", 1)  # só em memória: nada vai para o disco
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]


def test_arquivo_estragado_ou_de_outro_formato_vira_vazio(tmp_path):
    (tmp_path / "c.json").write_text("{nao é json", encoding="utf-8")
    assert Cache(60, arquivo=tmp_path / "c.json").pegar("a") is None
    (tmp_path / "c.json").write_text("[1, 2]", encoding="utf-8")
    assert len(Cache(60, arquivo=tmp_path / "c.json")) == 0
    (tmp_path / "c.json").write_text(json.dumps({"a": "texto", "b": {"quando": "x", "valor": 1},
                                                 "c": {"valor": 1}, "d": {"quando": 5}}), encoding="utf-8")
    assert len(Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio(6))) == 0  # nenhuma entrada completa


def test_arquivo_no_formato_antigo_da_web_continua_valendo(tmp_path):
    """O web.json de antes ({chave: {quando, valor}}) é lido sem perder as buscas já guardadas."""
    antigo = {"busca:fusca": {"quando": 1000.0, "valor": {"resposta": "r", "fonte": "tavily", "resultados": []}}}
    (tmp_path / "web.json").write_text(json.dumps(antigo), encoding="utf-8")
    c = Cache(6 * 3600, arquivo=tmp_path / "web.json", relogio=Relogio(1060))
    assert c.pegar("busca:fusca")["fonte"] == "tavily"


def test_outra_versao_do_formato_e_ignorada(tmp_path):
    novo = Cache(60, arquivo=tmp_path / "c.json", versao=2, relogio=Relogio())
    novo.guardar("a", 1)
    assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["a"]["v"] == 2
    assert Cache(60, arquivo=tmp_path / "c.json", versao=2, relogio=Relogio()).pegar("a") == 1
    assert Cache(60, arquivo=tmp_path / "c.json", versao=3, relogio=Relogio()).pegar("a") is None  # atualização do Vision
    assert Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio()).pegar("a") is None  # versão 1 não entende a 2


def test_apagar_e_limpar_tambem_vao_para_o_arquivo(tmp_path):
    c = Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio())
    c.guardar("a", 1)
    c.guardar("b", 2)
    c.apagar("a")
    assert Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio()).pegar("a") is None
    c.limpar()
    assert Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio()).pegar("b") is None


def test_erro_de_disco_e_valor_que_nao_vira_json_nao_levantam_e_avisam_uma_vez(tmp_path, monkeypatch, caplog):
    def quebra(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(modulo_cache, "gravar_json", quebra)
    c = Cache(60, arquivo=tmp_path / "c.json", relogio=Relogio(), nome="x")
    with caplog.at_level(logging.WARNING, logger="vision.cache"):
        c.guardar("a", 1)
        c.guardar("b", 2)
    assert c.pegar("a") == 1 and c.pegar("b") == 2  # continua valendo em memória
    assert len([r for r in caplog.records if "não deu para gravar" in r.getMessage()]) == 1
    monkeypatch.undo()
    d = Cache(60, arquivo=tmp_path / "d.json", relogio=Relogio())
    d.guardar("obj", object())  # não é JSON: não levanta
    assert d.pegar("obj") is not None


def test_so_memoria_aceita_qualquer_valor_inclusive_tuplas():
    c = Cache(60, relogio=Relogio(), maximo=1)
    c.guardar("janela", (1, 2, [{"e": 1}]))
    assert c.pegar("janela") == (1, 2, [{"e": 1}])


# ------------------------------------------------------------------ os usos migrados

async def test_web_manchetes_valem_30_min_e_buscas_valem_a_validade_do_config(tmp_path):
    rel = Relogio(5000.0)
    chamadas = {"busca": 0, "noticias": 0}

    def ddg(consulta, maximo):
        chamadas["busca"] += 1
        return [{"title": "T", "href": "https://exemplo.com/a", "body": "b"}]

    def noticias(tema, maximo):
        chamadas["noticias"] += 1
        return [{"title": "Manchete", "url": "https://exemplo.com/n", "source": "J", "date": "", "body": ""}]

    w = Web(tmp_path / "cache", chave="", buscar_ddg=ddg, validade_h=6)
    w._noticias_ddg = noticias
    w.cache.relogio = rel
    await w.buscar("fusca")
    await w.noticias("tecnologia")
    rel.t += VALIDADE_MANCHETES_S + 1  # 30 min e 1 s
    assert (await w.buscar("fusca")).do_cache and chamadas["busca"] == 1  # a busca (6 h) ainda vale
    await w.noticias("tecnologia")
    assert chamadas["noticias"] == 2  # a manchete (30 min) venceu
    rel.t += 6 * 3600
    await w.buscar("fusca")
    assert chamadas["busca"] == 2
    assert [p.name for p in (tmp_path / "cache").iterdir()] == ["web.json"]  # buscas e manchetes no mesmo arquivo


async def test_web_le_o_arquivo_de_antes_e_marca_do_cache(tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "web.json").write_text(json.dumps({"busca:fusca": {"quando": 1000.0, "valor": {
        "resposta": "boxer", "fonte": "tavily", "resultados": [
            {"titulo": "T", "url": "https://exemplo.com/a", "trecho": "b"}]}}}), encoding="utf-8")
    w = Web(tmp_path / "cache", chave="", buscar_ddg=lambda c, m: [])
    w.cache.relogio = Relogio(1060)
    b = await w.buscar("fusca")
    assert b.do_cache and b.resposta == "boxer"  # a marca de "veio do cache" continua


async def test_clima_previsao_vale_15_min_e_so_em_memoria(tmp_path):
    rel = Relogio(100.0)
    chamadas: list[str] = []
    hoje = tempo.agora().date()

    def responder(req: httpx.Request) -> httpx.Response:
        chamadas.append(req.url.path)
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "Cidade", "latitude": 1.0, "longitude": 2.0}]})
        return httpx.Response(200, json={
            "current": {"temperature_2m": 18.4, "apparent_temperature": 14.9, "weather_code": 2,
                        "relative_humidity_2m": 80, "wind_speed_10m": 9.0},
            "daily": {"time": [(hoje + timedelta(days=i)).isoformat() for i in range(3)],
                      "weather_code": [2, 63, 0], "temperature_2m_max": [27.2, 22.0, 30.0],
                      "temperature_2m_min": [13.6, 15.0, 16.0], "precipitation_probability_max": [10, 80, 0]}})

    clima = Clima(tmp_path / "cache", "Cidade", transporte=httpx.MockTransport(responder), relogio=rel)
    await clima.previsao()
    rel.t += VALIDADE_S - 1
    await clima.previsao()
    assert chamadas.count("/v1/forecast") == 1
    rel.t += 2  # passou de 15 min
    await clima.previsao()
    assert chamadas.count("/v1/forecast") == 2 and chamadas.count("/v1/search") == 1  # coordenadas continuam guardadas
