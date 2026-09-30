"""Avaliação do Jarvis com servidores falsos (agenda + Orbit) e modelo de verdade.

uv run python evals/run.py --modelos qwen3.5:4b,qwen3.5:9b [--pensar] [--so agenda_hoje,...]
Gera evals/resultado-<nome>.md e evals/saidas/<modelo>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "evals"))

from casos import CASOS, Caso, Contexto  # noqa: E402
from fakes import calendario_falso, orbit_falso  # noqa: E402

from jarvis import config, tempo  # noqa: E402
from jarvis.brain.agent import Agente  # noqa: E402
from jarvis.montagem import criar_llm, criar_memorias  # noqa: E402
from jarvis.tools.agenda import Agenda  # noqa: E402
from jarvis.tools.base import Registro  # noqa: E402
from jarvis.tools.mcp_host import ConexaoMCP, HostMCP  # noqa: E402
from jarvis.tools.memoria import FerramentasMemoria  # noqa: E402
from jarvis.tools.orbit import FerramentasOrbit  # noqa: E402


async def rodar_caso(cfg, llm, caso: Caso) -> dict:
    hoje = tempo.agora().date()
    agenda_srv = calendario_falso.criar_servidor(hoje)
    orbit_srv = orbit_falso.criar_servidor()
    host = HostMCP({
        "google-calendar": ConexaoMCP("google-calendar", agenda_srv, 20),
        "orbit": ConexaoMCP("orbit", orbit_srv, 20),
    })
    pasta = Path(tempfile.mkdtemp(prefix="jarvis-eval-"))
    cfg.dados = pasta
    memorias = criar_memorias(cfg)
    for fato in caso.memorias:
        await memorias.lembrar(fato)
    registro = Registro()
    registro.adicionar(*Agenda(cfg, host).ferramentas(), *FerramentasMemoria(memorias).ferramentas(),
                       *FerramentasOrbit(host).ferramentas())
    agente = Agente(llm, registro, memorias, top_k=3, similaridade_minima=0.25)
    respostas, tempos = [], []
    async with host:
        for i, fala in enumerate(caso.falas):
            sessao = "eval2" if caso.sessao_nova_na_ultima and i == len(caso.falas) - 1 else "eval"
            primeira: list[float] = []
            t0 = time.perf_counter()
            r = await agente.responder(fala, canal="voz", sessao=sessao,
                                       ao_texto=lambda _t: primeira.append(time.perf_counter()) if not primeira else None)
            tempos.append({"total": time.perf_counter() - t0, "primeira": (primeira[0] - t0) if primeira else None})
            respostas.append(r)
        ctx = Contexto(hoje, respostas, agenda_srv, orbit_srv, memorias)
        try:
            falhas = caso.checar(ctx)
        except Exception as e:  # noqa: BLE001
            falhas = [f"erro no checador: {type(e).__name__}: {e}"]
    memorias.fechar()
    return {
        "id": caso.id,
        "categoria": caso.categoria,
        "passou": not falhas,
        "falhas": falhas,
        "falas": caso.falas,
        "respostas": [r.texto for r in respostas],
        "ferramentas": [[(f["nome"], f["args"], f["ok"]) for f in r.ferramentas] for r in respostas],
        "insistiu": any(r.insistiu for r in respostas),
        "tempos": tempos,
    }


async def avaliar(modelo: str, pensar: bool, so: set[str] | None) -> dict:
    cfg = config.carregar(sobrescrever={"modelo.nome": modelo, "modelo.pensar": pensar})
    llm = criar_llm(cfg)
    t = time.perf_counter()
    await llm.conversar([{"role": "user", "content": "oi"}], None)  # aquece (carrega na VRAM)
    carga = time.perf_counter() - t
    resultados = []
    for caso in CASOS:
        if so and caso.id not in so:
            continue
        r = await rodar_caso(cfg, llm, caso)
        marca = "ok " if r["passou"] else "ERR"
        print(f"  {marca} {caso.id:28} {r['tempos'][-1]['total']:5.1f}s  {'; '.join(r['falhas'])[:110]}", flush=True)
        resultados.append(r)
    vram = await _vram()
    await llm.descarregar()
    return {"modelo": modelo, "pensar": pensar, "carga_s": carga, "vram": vram, "resultados": resultados}


async def _vram() -> str:
    import ollama

    try:
        ps = await ollama.AsyncClient().ps()
        return "; ".join(f"{m.model}: {m.size_vram / 2**30:.1f} GB VRAM de {m.size / 2**30:.1f} GB" for m in ps.models)
    except Exception:  # noqa: BLE001
        return "?"


def _rotulo(av: dict) -> str:
    return av["modelo"] + (" (pensando)" if av["pensar"] else "")


def relatorio(avaliacoes: list[dict]) -> str:
    linhas = ["# Avaliação do Jarvis", "",
              f"Gerado em {tempo.agora():%d/%m/%Y %H:%M} · {len(avaliacoes[0]['resultados'])} casos · canal voz · "
              "agenda e Orbit falsos · memória com embeddinggemma real", ""]
    linhas += ["## Resumo", "", "| Modelo | Acertos | Tempo médio | p90 | 1ª palavra (média) | Insistências | VRAM |",
               "|---|---|---|---|---|---|---|"]
    for av in avaliacoes:
        rs = av["resultados"]
        totais = [t["total"] for r in rs for t in r["tempos"]]
        primeiras = [t["primeira"] for r in rs for t in r["tempos"] if t["primeira"] is not None]
        p90 = sorted(totais)[int(0.9 * (len(totais) - 1))] if totais else 0
        linhas.append(
            f"| {_rotulo(av)} | **{sum(r['passou'] for r in rs)}/{len(rs)}** | {statistics.mean(totais):.1f}s | {p90:.1f}s | "
            f"{statistics.mean(primeiras):.1f}s | {sum(r['insistiu'] for r in rs)} | {av['vram']} |"
        )
    cats = sorted({r["categoria"] for r in avaliacoes[0]["resultados"]})
    linhas += ["", "## Por categoria", "", "| Categoria | " + " | ".join(_rotulo(a) for a in avaliacoes) + " |",
               "|---|" + "---|" * len(avaliacoes)]
    for cat in cats:
        cel = []
        for av in avaliacoes:
            rs = [r for r in av["resultados"] if r["categoria"] == cat]
            cel.append(f"{sum(r['passou'] for r in rs)}/{len(rs)}")
        linhas.append(f"| {cat} | " + " | ".join(cel) + " |")
    linhas += ["", "## Caso a caso", "", "| Caso | " + " | ".join(_rotulo(a) for a in avaliacoes) + " |",
               "|---|" + "---|" * len(avaliacoes)]
    for i, caso in enumerate(avaliacoes[0]["resultados"]):
        cel = []
        for av in avaliacoes:
            r = av["resultados"][i]
            cel.append(("✅" if r["passou"] else "❌") + f" {r['tempos'][-1]['total']:.1f}s")
        linhas.append(f"| `{caso['id']}` | " + " | ".join(cel) + " |")
    linhas += ["", "## Onde errou", ""]
    for av in avaliacoes:
        erros = [r for r in av["resultados"] if not r["passou"]]
        linhas.append(f"### {_rotulo(av)} — {len(erros)} erro(s)")
        for r in erros:
            linhas.append(f"- **{r['id']}** · \"{r['falas'][-1]}\"")
            linhas.append(f"  - falhas: {'; '.join(r['falhas'])}")
            linhas.append(f"  - ferramentas: {r['ferramentas'][-1]}")
            linhas.append(f"  - resposta: {r['respostas'][-1][:220]!r}")
        linhas.append("")
    return "\n".join(linhas)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelos", default="qwen3.5:4b,qwen3.5:9b")
    ap.add_argument("--pensar", action="store_true", help="avalia também o 4B com 'thinking' ligado")
    ap.add_argument("--so", help="ids de casos separados por vírgula")
    ap.add_argument("--nome", default=None, help="sufixo do arquivo de resultado")
    args = ap.parse_args()
    so = set(args.so.split(",")) if args.so else None
    configs = [(m, False) for m in args.modelos.split(",")]
    if args.pensar:
        configs.append((configs[0][0], True))
    avaliacoes = []
    for modelo, pensar in configs:
        print(f"\n== {modelo}{' (pensando)' if pensar else ''} ==", flush=True)
        av = await avaliar(modelo, pensar, so)
        avaliacoes.append(av)
        saidas = RAIZ / "evals" / "saidas"
        saidas.mkdir(exist_ok=True)
        nome = modelo.replace(":", "-") + ("-pensando" if pensar else "")
        (saidas / f"{nome}.json").write_text(json.dumps(av, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    nome = args.nome or "-vs-".join(m.split(":")[-1] for m, _ in configs[:2])
    arquivo = RAIZ / "evals" / f"resultado-{nome}.md"
    arquivo.write_text(relatorio(avaliacoes), encoding="utf-8")
    print(f"\nRelatório: {arquivo}")
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.WARNING)
    for n in ("httpx", "mcp"):
        logging.getLogger(n).setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
