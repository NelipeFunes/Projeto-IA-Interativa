"""Avaliação do Vision com servidores falsos e modelo de verdade.

    uv run python evals/run.py --modelos qwen3.5:4b,qwen3.5:9b [--suite base|ferramentas|todas] [--antigo]
        [--pensar] [--so id1,id2] [--repeticoes 2] [--avanco-min 7] [--com-atalhos] [--sobrescrever modelo.contexto=8192]

Por padrão roda o ambiente COMPLETO de produção (`ambiente.py`: as 53 ferramentas, com TV, Spotify, luzes, PC, clima e
web falsos), porque o tamanho do prompt manda na latência. `--antigo` volta ao ambiente reduzido de antes (agenda, memória
e Orbit), para comparar com `resultado-4b-vs-9b.md`.

`--avanco-min`: quantos minutos o relógio do assistente avança a cada fala (padrão 7). No uso de verdade os pedidos vêm
com minutos de intervalo; rodando todos no mesmo minuto o cache de prefixo do modelo parecia sempre quente e a latência
saía otimista.

Gera evals/resultado-<nome>.md e evals/saidas/<modelo>.json (um por modelo, gravado ao fim de cada caso: se a noite
for interrompida, o que já rodou não se perde).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tests"))
sys.path.insert(0, str(RAIZ / "evals"))

import ambiente  # noqa: E402
from casos import CASOS, Caso, Contexto  # noqa: E402
from casos_ferramentas import CASOS_FERRAMENTAS  # noqa: E402
from fakes import calendario_falso, orbit_falso  # noqa: E402

from vision import config, tempo  # noqa: E402
from vision.brain.agent import Agente, Resposta  # noqa: E402
from vision.montagem import criar_llm, criar_memorias  # noqa: E402
from vision.tools.agenda import Agenda  # noqa: E402
from vision.tools.base import Registro  # noqa: E402
from vision.tools.mcp_host import ConexaoMCP, HostMCP  # noqa: E402
from vision.tools.memoria import FerramentasMemoria  # noqa: E402
from vision.tools.orbit import FerramentasOrbit  # noqa: E402

PERFIL = "O Felipe estuda Engenharia, gosta de café e de música. Prefere respostas curtas."
PRAZO_FALA_S = 240  # uma fala que passa disso conta como falha (modelo grande demais para voz)


async def rodar_caso(cfg, llm, caso: Caso, producao: bool, avanco_min: float, com_atalhos: bool, metricas: list) -> dict:
    hoje = tempo.agora().date()
    agenda_srv = calendario_falso.criar_servidor(hoje)
    orbit_srv = orbit_falso.criar_servidor()
    pasta = Path(tempfile.mkdtemp(prefix="vision-eval-"))
    cfg.dados = pasta
    (pasta / "perfil.md").write_text(PERFIL, encoding="utf-8")
    host = ambiente.host_completo(agenda_srv, orbit_srv) if producao else HostMCP({
        "google-calendar": ConexaoMCP("google-calendar", agenda_srv, 20),
        "orbit": ConexaoMCP("orbit", orbit_srv, 20),
    })
    memorias = criar_memorias(cfg)
    for fato in caso.memorias:
        await memorias.lembrar(fato)
    respostas, tempos = [], []
    amb = None
    async with host:
        if producao:
            amb = ambiente.criar(cfg, host, memorias, pasta)
            registro = amb.registro
            if caso.preparar:
                caso.preparar(amb)
        else:
            registro = Registro()
            registro.adicionar(*Agenda(cfg, host).ferramentas(), *FerramentasMemoria(memorias).ferramentas(),
                               *FerramentasOrbit(host).ferramentas())
        base, passo = tempo.agora(), [0]

        def relogio():
            return base + timedelta(minutes=avanco_min * passo[0])

        agente = Agente(llm, registro, memorias, perfil=pasta / "perfil.md", top_k=3, similaridade_minima=0.25,
                        relogio=relogio, confirmacao=caso.confirmacao)
        if com_atalhos and amb is not None:
            agente.atalhos = amb.atalhos
        for i, fala in enumerate(caso.falas):
            passo[0] += 1
            sessao = "eval2" if caso.sessao_nova_na_ultima and i == len(caso.falas) - 1 else "eval"
            primeira: list[float] = []
            metricas.clear()
            t0 = time.perf_counter()
            try:
                r = await asyncio.wait_for(
                    agente.responder(fala, canal="voz", sessao=sessao,
                                     ao_texto=lambda _t: primeira.append(time.perf_counter()) if not primeira else None),
                    PRAZO_FALA_S)
            except Exception as e:  # noqa: BLE001 - uma fala que quebra não derruba a noite
                r = Resposta(f"[erro: {type(e).__name__}: {str(e)[:120]}]", [])
            tempos.append({
                "total": time.perf_counter() - t0, "primeira": (primeira[0] - t0) if primeira else None,
                "prefill": sum(m.prompt_s for m in metricas),
                "prompt_tokens": max((m.prompt_tokens for m in metricas), default=0), "chamadas_llm": len(metricas)})
            respostas.append(r)
        ctx = Contexto(hoje, respostas, agenda_srv, orbit_srv, memorias, amb)
        try:
            falhas = caso.checar(ctx)
        except Exception as e:  # noqa: BLE001
            falhas = [f"erro no checador: {type(e).__name__}: {e}"]
        erro = next((r.texto for r in respostas if r.texto.startswith("[erro:")), None)
        if erro:
            falhas.append(erro)
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


def _casos(suite: str) -> list[Caso]:
    return {"base": CASOS, "ferramentas": CASOS_FERRAMENTAS, "todas": [*CASOS, *CASOS_FERRAMENTAS]}[suite]


async def avaliar(modelo: str, pensar: bool, so: set[str] | None, repeticoes: int, suite: str, producao: bool,
                  avanco_min: float, com_atalhos: bool, sobrescrever: dict, salvar) -> dict:
    cfg = config.carregar(sobrescrever={"modelo.nome": modelo, "modelo.pensar": pensar, **sobrescrever})
    llm = criar_llm(cfg)
    metricas: list = []
    original = llm.conversar

    async def medido(*a, **k):
        r = await original(*a, **k)
        metricas.append(r)
        return r

    llm.conversar = medido
    t = time.perf_counter()
    try:
        await llm.carregar()
        await llm.conversar([{"role": "user", "content": "oi"}], None)  # aquece
    except Exception as e:  # noqa: BLE001
        if "think" in str(e).lower():  # modelo sem "thinking": não aceita o parâmetro
            llm.pensar = None
            await llm.conversar([{"role": "user", "content": "oi"}], None)
        else:
            raise
    carga = time.perf_counter() - t
    resultados = []
    avaliacao = {"modelo": modelo, "pensar": pensar, "carga_s": carga, "vram": "?", "resultados": resultados,
                 "suite": suite, "producao": producao, "avanco_min": avanco_min}
    for rep in range(repeticoes):
        for caso in _casos(suite):
            if so and caso.id not in so:
                continue
            r = await rodar_caso(cfg, llm, caso, producao, avanco_min, com_atalhos, metricas)
            r["rep"] = rep
            marca = "ok " if r["passou"] else "ERR"
            print(f"  {marca} [{rep + 1}] {caso.id:28} {r['tempos'][-1]['total']:5.1f}s  {'; '.join(r['falhas'])[:100]}",
                  flush=True)
            resultados.append(r)
            salvar(avaliacao)
    avaliacao["vram"] = await _vram()
    salvar(avaliacao)
    await llm.descarregar()
    return avaliacao


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
    ids = list(dict.fromkeys(r["id"] for r in avaliacoes[0]["resultados"]))
    reps = max(r.get("rep", 0) for r in avaliacoes[0]["resultados"]) + 1
    a0 = avaliacoes[0]
    ambiente_txt = "ambiente completo (53 ferramentas)" if a0.get("producao") else "ambiente reduzido (agenda, memória e Orbit)"
    linhas = ["# Avaliação do Vision", "",
              f"Gerado em {tempo.agora():%d/%m/%Y %H:%M} · {len(ids)} casos × {reps} rodada(s) · canal voz · {ambiente_txt} · "
              f"relógio avança {a0.get('avanco_min', 0):g} min por fala · memória com embeddinggemma real", ""]
    linhas += ["## Resumo", "", "| Modelo | Acertos | Tempo médio | p90 | 1ª palavra (média) | Prefill (média) | Insistências | VRAM |",
               "|---|---|---|---|---|---|---|---|"]
    for av in avaliacoes:
        rs = av["resultados"]
        totais = [t["total"] for r in rs for t in r["tempos"]]
        primeiras = [t["primeira"] for r in rs for t in r["tempos"] if t["primeira"] is not None]
        prefills = [t.get("prefill", 0) for r in rs for t in r["tempos"] if t.get("chamadas_llm")]
        p90 = sorted(totais)[int(0.9 * (len(totais) - 1))] if totais else 0
        linhas.append(
            f"| {_rotulo(av)} | **{sum(r['passou'] for r in rs)}/{len(rs)}** | {statistics.mean(totais):.1f}s | {p90:.1f}s | "
            f"{statistics.mean(primeiras) if primeiras else 0:.1f}s | {statistics.mean(prefills) if prefills else 0:.2f}s | "
            f"{sum(r['insistiu'] for r in rs)} | {av['vram']} |"
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
    linhas += ["", "## Caso a caso (uma marca por rodada; tempo médio da última fala)", "",
               "| Caso | " + " | ".join(_rotulo(a) for a in avaliacoes) + " |", "|---|" + "---|" * len(avaliacoes)]
    for cid in ids:
        cel = []
        for av in avaliacoes:
            rs = [r for r in av["resultados"] if r["id"] == cid]
            if not rs:
                cel.append("—")
                continue
            marcas = "".join("✅" if r["passou"] else "❌" for r in rs)
            cel.append(f"{marcas} {statistics.mean(r['tempos'][-1]['total'] for r in rs):.1f}s")
        linhas.append(f"| `{cid}` | " + " | ".join(cel) + " |")
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
    ap.add_argument("--pensar", action="store_true", help="avalia também o primeiro modelo com 'thinking' ligado")
    ap.add_argument("--so", help="ids de casos separados por vírgula")
    ap.add_argument("--nome", default=None, help="sufixo do arquivo de resultado")
    ap.add_argument("--repeticoes", type=int, default=1, help="rodadas por caso (o modelo varia um pouco)")
    ap.add_argument("--suite", choices=["base", "ferramentas", "todas"], default="todas")
    ap.add_argument("--antigo", action="store_true", help="ambiente reduzido de antes (agenda, memória e Orbit)")
    ap.add_argument("--avanco-min", type=float, default=7, help="minutos que o relógio avança a cada fala")
    ap.add_argument("--com-atalhos", action="store_true", help="liga os atalhos sem modelo (como no Vision de verdade)")
    ap.add_argument("--sobrescrever", action="append", default=[], metavar="CHAVE=VALOR",
                    help="muda uma chave do config só nesta avaliação (ex.: modelo.contexto=8192)")
    args = ap.parse_args()
    so = set(args.so.split(",")) if args.so else None
    sobrescrever = {}
    for item in args.sobrescrever:
        chave, _, valor = item.partition("=")
        sobrescrever[chave] = json.loads(valor) if valor[:1] in "0123456789[{\"-tfn" else valor
    producao = not args.antigo
    if args.antigo and args.suite != "base":
        print("--antigo só tem as ferramentas da suíte base: usando --suite base")
        args.suite = "base"
    configs = [(m, False) for m in args.modelos.split(",")]
    if args.pensar:
        configs.append((configs[0][0], True))
    avaliacoes = []
    saidas = RAIZ / "evals" / "saidas"
    saidas.mkdir(exist_ok=True)
    for modelo, pensar in configs:
        print(f"\n== {modelo}{' (pensando)' if pensar else ''} ==", flush=True)
        nome = modelo.replace(":", "-") + ("-pensando" if pensar else "")

        def salvar(av, nome=nome):
            (saidas / f"{nome}.json").write_text(json.dumps(av, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

        try:
            av = await avaliar(modelo, pensar, so, args.repeticoes, args.suite, producao, args.avanco_min,
                               args.com_atalhos, sobrescrever, salvar)
        except Exception as e:  # noqa: BLE001 - um modelo que não carrega não derruba os outros da noite
            print(f"  FALHOU {modelo}: {type(e).__name__}: {str(e)[:200]}", flush=True)
            continue
        avaliacoes.append(av)
    if not avaliacoes:
        return 1
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
