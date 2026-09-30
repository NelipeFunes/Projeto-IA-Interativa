"""`vision teste [tudo|ollama|agenda|orbit|voz]`: checagem do ambiente, com diagnóstico em português."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from typing import Any

from vision.config import Config

OK, ERRO, AVISO = "[ok]  ", "[ERRO]", "[aviso]"


def _p(marca: str, msg: str) -> None:
    print(f"{marca} {msg}")


async def _ollama(cfg: Config) -> bool:
    import ollama

    cli = ollama.AsyncClient(host=cfg.get("modelo.host"))
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as h:
            versao = (await h.get(f"{cfg.get('modelo.host')}/api/version")).json().get("version")
        _p(OK, f"Ollama no ar (versão {versao})")
    except Exception as e:  # noqa: BLE001
        _p(ERRO, f"Ollama não responde em {cfg.get('modelo.host')}: {e}. Abra o app Ollama.")
        return False
    instalados = {m.model for m in (await cli.list()).models}
    tudo_ok = True
    for nome in [cfg.get("modelo.nome"), cfg.get("memoria.modelo_embedding")]:
        achou = any(i == nome or i == f"{nome}:latest" for i in instalados)
        _p(OK if achou else ERRO, f"modelo {nome} {'baixado' if achou else 'FALTANDO: rode ollama pull ' + nome}")
        tudo_ok &= achou
    if not tudo_ok:
        return False
    t = time.perf_counter()
    r = await cli.chat(model=cfg.get("modelo.nome"), messages=[{"role": "user", "content": "Diga só: pronto."}],
                       think=False, keep_alive=cfg.get("modelo.manter_carregado", "30m"))
    total = time.perf_counter() - t
    tps = (r.eval_count or 0) / max((r.eval_duration or 1) / 1e9, 1e-6)
    carga = (r.load_duration or 0) / 1e9
    _p(OK, f"resposta em {total:.1f}s (carregar modelo: {carga:.1f}s, {tps:.0f} tokens/s)")
    for m in (await cli.ps()).models:
        if m.model.startswith(cfg.get("modelo.nome")):
            na_gpu = m.size_vram / max(m.size, 1)
            marca = OK if na_gpu > 0.99 else AVISO
            _p(marca, f"{m.model}: {m.size_vram / 2**30:.1f} GB na VRAM ({na_gpu:.0%} na GPU)")
    if shutil.which("nvidia-smi"):
        saida = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if saida:
            usado, total_mb = (int(x) for x in saida.split(","))
            _p(OK if total_mb - usado > 700 else AVISO, f"VRAM: {usado} de {total_mb} MB em uso")
    return True


async def _agenda(cfg: Config) -> bool:
    from vision.google_login import dias_desde_login
    from vision.tools.mcp_host import HostMCP

    s = cfg.get("mcp.google-calendar") or {}
    cred = cfg.caminho((s.get("env") or {}).get("GOOGLE_OAUTH_CREDENTIALS", "data/google-oauth.json"))
    if not cred.exists():
        _p(ERRO, f"falta {cred} — siga docs/guia-google-cloud.md")
        return False
    _p(OK, "credenciais OAuth encontradas")
    dias = dias_desde_login(cfg)
    if dias is None:
        _p(AVISO, "nunca rodou 'vision google-login' por aqui")
    else:
        _p(OK if dias < 6 else AVISO, f"último login há {dias:.1f} dia(s) (vence com 7)")
    host = HostMCP.da_config(cfg)
    host.conexoes = {k: v for k, v in host.conexoes.items() if k == "google-calendar"}
    async with host:
        from vision import tempo
        from vision.tools.agenda import Agenda

        try:
            saida = await Agenda(cfg, host).listar({"data_inicio": tempo.agora().date().isoformat()})
            _p(OK, "agenda respondeu:\n" + "\n".join("        " + linha for linha in saida.splitlines()[:8]))
            return True
        except Exception as e:  # noqa: BLE001
            _p(ERRO, str(e))
            return False


def _formato(itens: list[dict[str, Any]]) -> dict[str, Any]:
    if not itens:
        return {"quantidade": 0}
    chaves: dict[str, str] = {}
    for it in itens[:20]:
        for k, v in it.items():
            chaves.setdefault(k, type(v).__name__)
    return {"quantidade": len(itens), "campos": chaves, "exemplos": itens[:2]}


async def _orbit(cfg: Config) -> bool:
    sys.path.insert(0, str(cfg.raiz / "mcp_servers" / "orbit"))
    from orbit_api import ErroOrbit, OrbitAPI

    api = OrbitAPI(timeout_s=100)
    if not (api.token or (api.email and api.senha)):
        _p(ERRO, "sem credenciais: copie .env.example para .env e preencha ORBIT_TOKEN ou ORBIT_EMAIL/ORBIT_PASSWORD")
        return False
    _p(OK, f"credenciais presentes ({'token' if api.token else 'e-mail e senha'}); falando com {api.url} "
           "(pode levar ~1 min se o Render estiver dormindo)")
    from datetime import datetime

    mes = datetime.now().strftime("%Y-%m")
    descoberto: dict[str, Any] = {"quando": datetime.now().isoformat(timespec="seconds"), "mes": mes}
    ok = True
    try:
        if not api.token:
            await api.login()
            _p(OK, "login feito")
        for nome, coro in [
            ("transacoes", api.transacoes(mes)),
            ("orcamentos", api.orcamentos(mes)),
            ("categorias", api.categorias()),
            ("tarefas", api.tarefas()),
        ]:
            try:
                itens = await coro
                descoberto[nome] = _formato(itens)
                campos = ", ".join(descoberto[nome].get("campos", {}))
                _p(OK, f"{nome}: {len(itens)} item(ns); campos: {campos or '-'}")
            except ErroOrbit as e:
                descoberto[nome] = {"erro": str(e)}
                _p(ERRO, f"{nome}: {e}")
                ok = False
    except ErroOrbit as e:
        _p(ERRO, str(e))
        ok = False
    finally:
        await api.fechar()
    arquivo = cfg.dados / "orbit-formato.json"
    arquivo.write_text(json.dumps(descoberto, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _p(OK if ok else AVISO, f"formato salvo em {arquivo} (use para ajustar CAMPOS em mcp_servers/orbit/orbit_api.py)")
    return ok


async def _voz(cfg: Config) -> bool:
    from vision.voice import diagnostico_voz

    return await diagnostico_voz.rodar(cfg, _p, OK, ERRO, AVISO)


async def diagnosticar(cfg: Config, parte: str = "tudo") -> int:
    partes = ["ollama", "agenda", "orbit", "voz"] if parte == "tudo" else [parte]
    if parte == "tudo" and not (cfg.get("mcp.orbit") or {}).get("ativo", True):
        partes.remove("orbit")
        print("(orbit desligado no config.yaml: pulado)")
    funcoes = {"ollama": _ollama, "agenda": _agenda, "orbit": _orbit, "voz": _voz}
    falhas = []
    for p in partes:
        print(f"\n== {p} ==")
        try:
            if not await funcoes[p](cfg):
                falhas.append(p)
        except Exception as e:  # noqa: BLE001
            _p(ERRO, f"{type(e).__name__}: {e}")
            falhas.append(p)
    print("\n" + ("Tudo certo." if not falhas else f"Com problema: {', '.join(falhas)}"))
    return 1 if falhas else 0
