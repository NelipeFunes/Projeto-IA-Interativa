"""Cache único (T-018, etapa 2): validade, tamanho, arquivo, versão, estatísticas e os usos migrados."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import timedelta

import httpx

from vision import cache as modulo_cache
from vision import tempo
from vision.cache import Cache
from vision.clima import MAX_CIDADES, VALIDADE_S, Clima
from vision.web import VALIDADE_MANCHETES_S, Noticia, Web


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
    d.guardar("obj", object())  # não é JSON: não levanta e não é guardado
    assert d.pegar("obj") is None


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


# ------------------------------------------------------------------ revisão do PR 58

def test_valor_que_nao_vira_json_nao_trava_a_gravacao_das_outras_entradas(tmp_path, caplog):
    c = Cache(3600, arquivo=tmp_path / "c.json", relogio=Relogio(1000))
    c.guardar("antes", {"x": 1})
    with caplog.at_level(logging.WARNING, logger="vision.cache"):
        c.guardar("ruim", {"texto": json.loads('"abc \\ud83d fim"')})  # substituto Unicode solto (vem de JSON de terceiros)
        c.guardar("depois", {"x": 2})
    assert c.pegar("ruim") is None
    de_novo = Cache(3600, arquivo=tmp_path / "c.json", relogio=Relogio(1000))
    assert de_novo.pegar("antes") == {"x": 1} and de_novo.pegar("depois") == {"x": 2}  # o arquivo continua sendo gravado
    assert not list(tmp_path.glob("*.tmp"))  # e nenhum temporário ficou para trás


def test_arquivo_cache_e_compacto_e_nao_deixa_temporario(tmp_path):
    c = Cache(3600, arquivo=tmp_path / "c.json", relogio=Relogio(1000))
    c.guardar("a", {"x": 1})
    texto = (tmp_path / "c.json").read_text(encoding="utf-8")
    assert "\n" not in texto.strip() and [p.name for p in tmp_path.iterdir()] == ["c.json"]


def test_carimbo_invalido_ou_do_futuro_vale_vencido(tmp_path):
    (tmp_path / "c.json").write_text(
        '{"nan": {"quando": NaN, "valor": 1}, "inf": {"quando": Infinity, "valor": 1}, "bool": {"quando": true, '
        '"valor": 1}, "futuro": {"quando": 5000, "valor": 1}, "bom": {"quando": 900, "valor": 1}}', encoding="utf-8")
    c = Cache(3600, arquivo=tmp_path / "c.json", relogio=Relogio(1000))
    assert len(c) == 2  # NaN, infinito e true nem entram
    assert c.pegar("futuro") is None  # hora do Windows mexida: carimbo no futuro não vale "fresco para sempre"
    assert c.pegar("bom") == 1


def test_renovar_uma_chave_a_poe_como_a_mais_recente_mesmo_com_relogio_parado():
    c = Cache(3600, maximo=2, relogio=Relogio(10))
    c.guardar("a", 1)
    c.guardar("b", 2)
    c.guardar("a", 3)  # a renovada
    c.guardar("c", 4)
    assert c.pegar("a") == 3 and c.pegar("c") == 4 and c.pegar("b") is None


def test_cache_vazio_continua_sendo_cache_e_contagem_manual():
    c = Cache(60, relogio=Relogio())
    assert bool(c) is True and (c or Cache(60)) is c
    c.guardar("a", 1)
    assert c.pegar("a", contar=False) == 1 and c.pegar("z", contar=False) is None
    assert (c.acertos, c.faltas) == (0, 0)
    c.contar_acerto(True)
    c.contar_acerto(False)
    assert (c.acertos, c.faltas) == (1, 1)


async def test_apps_do_pc_ficam_10_min_e_lista_vazia_nao_e_guardada(tmp_path):
    from vision.tools.pc import CACHE_APPS_S, PC

    rel = Relogio(0)
    p = PC(tmp_path / "logs")
    p._cache_apps.relogio = rel
    saidas = [json.dumps([{"Name": "Bloco de Notas", "AppID": "notepad"}]), "[]",
              json.dumps([{"Name": "Calculadora", "AppID": "calc"}])]
    chamadas: list[tuple] = []

    async def falso(*cmd, prazo=20):
        chamadas.append(cmd)
        return 0, saidas[len(chamadas) - 1]

    p._rodar = falso
    assert (await p.apps())[0]["id"] == "notepad"
    await p.apps()
    assert len(chamadas) == 1  # 2ª vez do cache
    rel.t = CACHE_APPS_S + 1  # passou de 10 min
    assert await p.apps() == []  # a lista veio vazia...
    assert (await p.apps())[0]["id"] == "calc" and len(chamadas) == 3  # ...e não foi guardada: buscou de novo


async def test_clima_guarda_no_maximo_as_cidades_mais_recentes(tmp_path):
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

    rel = Relogio(0)
    clima = Clima(tmp_path / "cache", "Aaa", transporte=httpx.MockTransport(responder), relogio=rel)
    nomes = [chr(65 + i) * 3 for i in range(MAX_CIDADES + 1)]  # 21 cidades: Aaa, Bbb, ...
    for nome in nomes:
        rel.t += 1
        await clima.previsao(nome)
    assert len(clima._previsoes) == MAX_CIDADES
    antes = chamadas.count("/v1/forecast")
    await clima.previsao(nomes[-1])  # a mais recente continua guardada
    assert chamadas.count("/v1/forecast") == antes
    await clima.previsao(nomes[0])  # a mais velha saiu: vai à rede de novo
    assert chamadas.count("/v1/forecast") == antes + 1


async def test_web_busca_sem_resultado_e_pagina_vazia_nao_sao_guardadas(tmp_path):
    w = Web(tmp_path / "cache", chave="", buscar_ddg=lambda c, m: [])
    b = await w.buscar("nada encontrado")
    assert not b.resultados and w.cache.pegar("busca:nada encontrado") is None
    url = "https://exemplo.com/p"
    w._links[url] = None

    async def vazia(_url):
        return ""

    w._baixar = vazia
    assert await w.ler(url) == "" and w.cache.pegar("ler:" + url) is None


async def test_web_entrada_de_formato_errado_no_arquivo_e_descartada_sem_derrubar_a_busca(tmp_path):
    (tmp_path / "cache").mkdir()
    certa = {"quando": 1000.0, "valor": {"resposta": "", "fonte": "x", "resultados": []}}
    errados = {"busca:lista": {"quando": 1000.0, "valor": ["isso", "nao", "e", "dict"]},
               "busca:extra": {"quando": 1000.0, "valor": {"resultados": [{"titulo": "T", "url": "u", "trecho": "t",
                                                                           "campo_novo": 1}]}},
               "busca:soma": {"quando": 1000.0, "valor": {"resultados": ["nao e dict"]}},
               "ler:https://exemplo.com/p": {"quando": 1000.0, "valor": {"texto": 5}},
               "busca:certa": certa}
    (tmp_path / "cache" / "web.json").write_text(json.dumps(errados), encoding="utf-8")
    w = Web(tmp_path / "cache", chave="", buscar_ddg=lambda c, m: [
        {"title": "T", "href": "https://exemplo.com/a", "body": "b"}])
    w.cache.relogio = Relogio(1060)
    for consulta in ("lista", "extra", "soma"):
        b = await w.buscar(consulta)  # não levanta: busca de novo
        assert not b.do_cache and b.fonte == "duckduckgo"
        assert w.cache.pegar("busca:" + consulta)["fonte"] == "duckduckgo"  # e a entrada boa substituiu a errada
    w._links["https://exemplo.com/p"] = None

    async def baixar(_url):
        return "texto da página"

    w._baixar = baixar
    assert await w.ler("https://exemplo.com/p") == "texto da página"
    assert (await w.buscar("certa")).do_cache  # a entrada certa continua valendo


async def test_web_manchetes_com_e_sem_o_carimbo_interno_antigo(tmp_path):
    (tmp_path / "cache").mkdir()
    itens = [asdict(Noticia("Manchete", "https://exemplo.com/n", "J", "", ""))]
    arquivo = {"noticias:velha": {"quando": 1000.0, "valor": {"quando": 1000.0, "itens": itens}},  # formato antigo
               "noticias:nova": {"quando": 1000.0, "valor": {"itens": itens}}}  # formato atual
    (tmp_path / "cache" / "web.json").write_text(json.dumps(arquivo), encoding="utf-8")
    chamadas: list[str] = []
    w = Web(tmp_path / "cache", chave="", buscar_ddg=lambda c, m: [])
    w._noticias_ddg = lambda t, m: chamadas.append(t) or []
    w.cache.relogio = Relogio(1000 + 60)
    assert [n.titulo for n in await w.noticias("velha")] == ["Manchete"]
    assert [n.titulo for n in await w.noticias("nova")] == ["Manchete"]
    assert chamadas == []  # as duas vieram do cache
    w.cache.relogio = Relogio(1000 + VALIDADE_MANCHETES_S + 1)
    assert await w.noticias("nova") == [] and len(chamadas) == 1  # venceu: foi à rede
