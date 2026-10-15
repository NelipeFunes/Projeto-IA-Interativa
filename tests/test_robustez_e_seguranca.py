"""Robustez e segurança (achados da varredura de 15/10): ambiente dos filhos, rede local, janela, tarefas soltas, logs,
retenção de conversas e cache de página."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fakes.llm_falso import LLMFalso

from vision import envs, nucleo, server, tv
from vision.arquivos import limitar_log, podar_datados
from vision.tools.mcp_host import HostMCP

# Valores montados na hora (nada com cara de senha escrito no arquivo: os scanners de segredo reprovam o PR).
SEGREDOS = {nome: "-".join(["valor", nome.lower()]) for nome in
            ("ORBIT_PASSWORD", "ORBIT_TOKEN", "TAVILY_API_KEY", "GITHUB_TOKEN", "MINHA_SENHA", "AWS_SECRET_ACCESS_KEY")}
SEGREDOS["ORBIT_EMAIL"] = "eu@" + "exemplo.com"


# ------------------------------------------------------------------ ambiente dos processos filhos

def test_servidor_mcp_recebe_so_o_basico_e_o_que_o_config_declara():
    base = {"PATH": "c:/x", "SystemRoot": "C:/Windows", "TEMP": "c:/t", "HTTPS_PROXY": "http://proxy:3128", **SEGREDOS}
    env = envs.para_servidor_mcp({"GOOGLE_OAUTH_CREDENTIALS": "c:/data/google-oauth.json", "ENABLED_TOOLS": "a,b"},
                                 {k.upper(): v for k, v in base.items()})
    assert env["PATH"] == "c:/x" and env["HTTPS_PROXY"] == "http://proxy:3128" and env["SYSTEMROOT"] == "C:/Windows"
    assert env["GOOGLE_OAUTH_CREDENTIALS"] == "c:/data/google-oauth.json" and env["ENABLED_TOOLS"] == "a,b"
    assert not (set(SEGREDOS) & set(env))  # nenhum segredo do Vision passa


def test_referencia_com_cifrao_copia_a_variavel_do_vision_e_ignora_a_que_nao_existe():
    origem = {"PATH": "p", **SEGREDOS}
    env = envs.para_servidor_mcp({"ORBIT_EMAIL": "$ORBIT_EMAIL", "ORBIT_PASSWORD": "$ORBIT_PASSWORD", "ORBIT_URL": "$ORBIT_URL",
                                  "LITERAL": "texto com $ no meio"}, origem)
    assert env["ORBIT_EMAIL"] == SEGREDOS["ORBIT_EMAIL"] and env["ORBIT_PASSWORD"] == SEGREDOS["ORBIT_PASSWORD"]
    assert "ORBIT_URL" not in env  # não existe no Vision: não vai vazio
    assert env["LITERAL"] == "texto com $ no meio"


def test_comando_do_pc_perde_o_que_parece_segredo_e_mantem_o_resto():
    env = envs.sem_segredos({"PATH": "p", "JAVA_HOME": "j", "EDITOR": "vim", **SEGREDOS})
    assert env == {"PATH": "p", "JAVA_HOME": "j", "EDITOR": "vim", "ORBIT_EMAIL": SEGREDOS["ORBIT_EMAIL"]}  # e-mail não é segredo


def test_da_config_orbit_recebe_as_credenciais_dele_e_o_google_nao(cfg, monkeypatch):
    for k, v in SEGREDOS.items():
        monkeypatch.setenv(k, v)
    host = HostMCP.da_config(cfg)
    google, orbit = host.conexoes["google-calendar"].alvo.env, host.conexoes["orbit"].alvo.env
    assert not (set(SEGREDOS) & {k.upper() for k in google})
    assert "GOOGLE_OAUTH_CREDENTIALS" in google and "ENABLED_TOOLS" in google
    assert orbit["ORBIT_PASSWORD"] == SEGREDOS["ORBIT_PASSWORD"] and orbit["ORBIT_TOKEN"] == SEGREDOS["ORBIT_TOKEN"]
    assert "TAVILY_API_KEY" not in orbit and "GITHUB_TOKEN" not in orbit and "AWS_SECRET_ACCESS_KEY" not in orbit


async def test_pc_roda_comando_sem_herdar_segredos(monkeypatch, tmp_path):
    from vision.tools.pc import PC

    for k, v in SEGREDOS.items():
        monkeypatch.setenv(k, v)
    visto = {}

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def falso(*cmd, **kw):
        visto.update(kw)
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", falso)
    await PC(tmp_path)._rodar("powershell.exe", "-Command", "echo oi")
    assert "ORBIT_PASSWORD" not in visto["env"] and "TAVILY_API_KEY" not in visto["env"] and "PATH" in visto["env"]


# ------------------------------------------------------------------ só a rede local

@pytest.mark.parametrize("valor", ["255.255.255.255", "240.0.0.1", "198.18.0.1", "192.0.2.1", "100.64.0.1", "169.254.1.1",
                                   "172.15.0.1", "172.32.0.1", "11.0.0.1", "192.167.1.1", "0.0.0.0", "224.0.0.1"])
def test_ip_fora_das_tres_redes_locais_e_recusado(valor):
    assert tv.ip_valido(valor) is None


@pytest.mark.parametrize("valor", ["10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254", "192.168.0.1",
                                   "192.168.255.254"])
def test_ip_das_tres_redes_locais_vale(valor):
    assert tv.ip_valido(valor) == valor


# ------------------------------------------------------------------ janela e tarefas soltas

def _janela(cfg):
    (cfg.dados / "logs").mkdir(parents=True, exist_ok=True)
    return nucleo.Janela(cfg, "http://127.0.0.1:1/x", "token")


def test_janela_que_nao_sobe_vira_log_e_nao_derruba_quem_pediu(cfg, monkeypatch, caplog):
    j = _janela(cfg)

    def quebra():
        raise FileNotFoundError("Vision.exe sumiu")

    monkeypatch.setattr(j, "_iniciar", quebra)
    with caplog.at_level(logging.ERROR, logger="vision.nucleo"):
        assert j._subir() is False
        j.enviar("mostrar")  # antes a exceção subia até o handler do WebSocket
    assert "não consegui abrir a janela" in caplog.text


async def test_supervisor_continua_vivo_quando_a_janela_nao_sobe_e_desiste_depois_de_5_quedas(cfg, monkeypatch):
    j = _janela(cfg)
    tentativas = []

    def quebra():
        tentativas.append(1)
        raise OSError("bloqueada pelo antivírus")

    monkeypatch.setattr(j, "_iniciar", quebra)
    real_sleep = asyncio.sleep

    async def rapido(_s):
        await real_sleep(0)

    monkeypatch.setattr(nucleo.asyncio, "sleep", rapido)
    tarefa = asyncio.create_task(j.supervisionar())
    for _ in range(50):
        await real_sleep(0)
    j.encerrando = True
    await tarefa
    assert j.desistiu and 5 <= len(tentativas) <= 7  # tentou, contou as quedas e parou de insistir (sem morrer)


async def test_tarefa_solta_do_nucleo_loga_a_falha(monkeypatch, caplog):
    n = object.__new__(nucleo.Nucleo)
    n._avulsas = set()

    async def falha():
        raise RuntimeError("deu ruim")

    with caplog.at_level(logging.ERROR, logger="vision.nucleo"):
        n._agendar(falha())
        await asyncio.sleep(0.05)
    assert "tarefa em segundo plano falhou" in caplog.text and "deu ruim" in caplog.text and not n._avulsas


async def test_comando_da_tela_que_falha_vai_para_o_log(caplog):
    async def texto(_t):
        raise RuntimeError("o modelo caiu")

    tarefas: set = set()
    with caplog.at_level(logging.ERROR, logger="vision.server"):
        server._comando({"tipo": "texto", "texto": "oi"}, server.Controle(texto=texto), tarefas)
        await asyncio.sleep(0.05)
    assert "comando da tela falhou" in caplog.text and not tarefas


async def test_painel_atualiza_a_agenda_uma_vez_por_volta(monkeypatch):
    n = object.__new__(nucleo.Nucleo)
    chamadas = []

    async def atualizar():
        chamadas.append(1)

    async def painel():
        return {}

    n._atualizar_agenda = atualizar
    n.painel = painel
    n.barramento = SimpleNamespace(assinantes=[object()], publicar=lambda p: None)
    monkeypatch.setattr(nucleo, "ATUALIZAR_PAINEL_S", 0.01)
    t = asyncio.create_task(n._atualizar_painel())
    await asyncio.sleep(0.055)
    t.cancel()
    esperado = len(chamadas)
    assert 3 <= esperado <= 7  # uma por volta de ~10 ms (antes eram duas seguidas: o dobro)


# ------------------------------------------------------------------ logs e retenção

def test_log_grande_vira_ponto_um_e_o_ponto_um_vira_ponto_dois(tmp_path):
    log = tmp_path / "janela.log"
    log.write_bytes(b"a" * 50)
    limitar_log(log, maximo_bytes=100)
    assert log.exists() and not (tmp_path / "janela.log.1").exists()  # pequeno: não mexe
    log.write_bytes(b"b" * 200)
    limitar_log(log, maximo_bytes=100, copias=2)
    assert not log.exists() and (tmp_path / "janela.log.1").read_bytes() == b"b" * 200
    log.write_bytes(b"c" * 200)
    limitar_log(log, maximo_bytes=100, copias=2)
    assert (tmp_path / "janela.log.1").read_bytes() == b"c" * 200 and (tmp_path / "janela.log.2").read_bytes() == b"b" * 200
    log.write_bytes(b"d" * 200)
    limitar_log(log, maximo_bytes=100, copias=2)
    assert (tmp_path / "janela.log.2").read_bytes() == b"c" * 200  # o mais velho saiu
    limitar_log(tmp_path / "nao-existe.log")  # não levanta


def test_poda_de_arquivos_datados(tmp_path):
    hoje = date(2026, 10, 15)
    for dias in (0, 30, 89, 90, 91, 400):
        (tmp_path / f"{hoje - timedelta(days=dias)}.jsonl").write_text("x", encoding="utf-8")
    (tmp_path / "notas.txt").write_text("x", encoding="utf-8")  # nome fora do padrão: fica
    assert podar_datados(tmp_path, 90, hoje) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [f"{hoje - timedelta(days=d)}.jsonl" for d in (0, 30, 89, 90)] + ["notas.txt"])
    assert podar_datados(tmp_path, 0, hoje) == 0 and podar_datados(tmp_path / "nada", 90, hoje) == 0  # 0 = não apaga


async def test_montagem_poda_as_conversas_antigas(cfg, host, memorias):
    from vision.montagem import montar

    pasta = cfg.dados / "conversas"
    pasta.mkdir()
    (pasta / "2020-01-01.jsonl").write_text("{}", encoding="utf-8")
    recente = pasta / f"{date.today()}.jsonl"
    recente.write_text("{}", encoding="utf-8")
    async with montar(cfg, llm=LLMFalso([]), host=host, memorias=memorias):
        pass
    assert not (pasta / "2020-01-01.jsonl").exists() and recente.exists()


# ------------------------------------------------------------------ cache de página

async def test_pagina_lida_nao_enche_o_cache_com_centenas_de_milhares_de_caracteres(tmp_path):
    from vision.web import MAX_PAGINA_EM_CACHE, Web

    html = "<html><body>" + "<p>texto da página muito longo </p>" * 5_000 + "</body></html>"
    w = Web(tmp_path / "cache", chave="", buscar_ddg=lambda c, m: [],
            transporte=httpx.MockTransport(lambda req: httpx.Response(200, text=html, headers={"content-type": "text/html"})))
    w._publico = lambda url: True
    url = "https://exemplo.com/pagina"
    w._links[url] = None
    texto = await w.ler(url)
    assert len(texto) > MAX_PAGINA_EM_CACHE  # quem leu pela primeira vez recebe tudo (a ferramenta mostra o começo)
    assert len(w.cache.pegar("ler:" + url)["texto"]) == MAX_PAGINA_EM_CACHE
    assert os.path.getsize(tmp_path / "cache" / "web.json") < 3 * MAX_PAGINA_EM_CACHE
