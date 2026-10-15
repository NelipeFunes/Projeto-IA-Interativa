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
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
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

PERFIL = ("O Felipe estuda Engenharia, trabalha com programação e gosta de café, de música e de corrida. Prefere "
          "respostas curtas e diretas. Costuma acordar cedo, treina de manhã e estuda à noite. Tem um cachorro e mora "
          "numa cidade pequena. Não gosta de ser interrompido quando está estudando.")
# Memórias neutras para o assistente ter o que buscar a cada pedido, como no uso de verdade (onde a seção de memórias
# muda quase toda vez). Nenhuma fala disso nos casos: quem testa memória usa as do próprio caso.
MEMORIAS_NEUTRAS = [
    "O Felipe prefere café sem açúcar.", "O Felipe corre às terças e quintas de manhã.", "O Felipe tem um cachorro pequeno.",
    "O Felipe prefere reuniões curtas e no começo do dia.", "O Felipe estuda Cálculo às quartas à noite.",
    "O Felipe não come carne vermelha às segundas.", "O Felipe gosta de rock clássico.", "O Felipe usa fone de ouvido no trabalho.",
    "O Felipe quer viajar para a praia no fim do ano.", "O Felipe está aprendendo violão.", "O Felipe prefere luz quente à noite.",
    "O Felipe lê um capítulo antes de dormir.", "O Felipe faz backup do computador aos domingos.",
    "O Felipe gosta de pizza de calabresa.", "O Felipe prefere comprar em lojas com entrega rápida.",
    "O Felipe tem consulta no dentista duas vezes por ano.", "O Felipe usa o notebook no sofá aos sábados.",
    "O Felipe quer trocar a cadeira do escritório.", "O Felipe gosta de documentários de ciência.", "O Felipe bebe dois litros de água por dia.",
]
PRAZO_FALA_S = 240  # uma fala que passa disso conta como falha (modelo grande demais para voz)
JANELA_DO_RELOGIO_MIN = 13 * 60  # das 10h às 22h59: o relógio sintético nunca cruza a meia-noite


def medir(llm, metricas: list) -> None:
    """Cada chamada ao modelo entra em `metricas` com as mensagens que foram enviadas (`r.mensagens`): os verificadores
    de memória e de hora conferem o que o modelo RECEBEU, não só o que ele fez."""
    original = llm.conversar

    async def medido(*a, **k):
        mensagens = list(a[0]) if a else None  # a lista cresce depois; os dicts não mudam
        r = await original(*a, **k)
        r.mensagens = mensagens
        metricas.append(r)
        return r

    llm.conversar = medido


def dia_base() -> datetime:
    """Hoje às 10h. Criado uma vez por execução: uma noite que passa da meia-noite não troca o dia dos modelos seguintes."""
    return datetime.now(tempo.FUSO).replace(hour=10, minute=0, second=0, microsecond=0)


class Relogio:
    """Relógio sintético da avaliação: um só para todos os casos, no dia `base` às 10h, avançando `avanco_min` minutos a
    cada fala e dando a volta às 10h depois de 13 horas. Assim casos seguidos nunca caem no mesmo minuto (o que deixava
    o cache de prefixo do modelo sempre quente) e a avaliação nunca cruza a meia-noite, qualquer que seja o avanço (o
    "hoje" do prompt, do calendário falso e dos checadores é o mesmo)."""

    def __init__(self, avanco_min: float, base: datetime | None = None):
        if not 0 <= avanco_min <= 120:
            raise ValueError("avanço do relógio: de 0 a 120 minutos por fala")
        self.base = base or dia_base()
        self.avanco = avanco_min
        self.n = 0

    def agora(self) -> datetime:
        return self.base + timedelta(minutes=(self.avanco * self.n) % JANELA_DO_RELOGIO_MIN)

    def avancar(self) -> None:
        self.n += 1


async def rodar_caso(cfg, llm, caso: Caso, producao: bool, relogio: Relogio, com_atalhos: bool, metricas: list,
                     banco_semeado: Path | None) -> dict:
    hoje = relogio.agora().date()
    agenda_srv = calendario_falso.criar_servidor(hoje)
    orbit_srv = orbit_falso.criar_servidor()
    pasta = Path(tempfile.mkdtemp(prefix="vision-eval-"))
    cfg.dados = pasta
    (pasta / "perfil.md").write_text(PERFIL, encoding="utf-8")
    if banco_semeado is not None:
        shutil.copy(banco_semeado, pasta / "memoria.db")
    host = ambiente.host_completo(agenda_srv, orbit_srv) if producao else HostMCP({
        "google-calendar": ConexaoMCP("google-calendar", agenda_srv, 20),
        "orbit": ConexaoMCP("orbit", orbit_srv, 20),
    })
    memorias = amb = None
    respostas, tempos = [], []
    erro_ambiente = False
    enviados: list = []
    try:
        memorias = criar_memorias(cfg)
        for fato in caso.memorias:
            await memorias.lembrar(fato)
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
            agente = Agente(llm, registro, memorias, perfil=pasta / "perfil.md",
                            turnos_historico=int(cfg.get("conversa.turnos_no_historico", 6)),
                            top_k=int(cfg.get("memoria.top_k", 3)),
                            similaridade_minima=float(cfg.get("memoria.similaridade_minima", 0.25)),
                            relogio=relogio.agora, confirmacao=caso.confirmacao)
            if com_atalhos and amb is not None:
                agente.atalhos = amb.atalhos
            for i, fala in enumerate(caso.falas):
                relogio.avancar()
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
                    "prefill_1a_volta": metricas[0].prompt_s if metricas else 0.0,
                    "carga": sum(m.carga_s for m in metricas),  # load_duration do Ollama: uns ms com o modelo carregado; segundos = recarregou
                    "prompt_tokens": max((m.prompt_tokens for m in metricas), default=0), "chamadas_llm": len(metricas)})
                respostas.append(r)
                enviados.append([getattr(m, "mensagens", None) for m in metricas])
            ctx = Contexto(hoje, respostas, agenda_srv, orbit_srv, memorias, amb, agora=relogio.agora(), enviados=enviados)
            try:
                falhas = caso.checar(ctx)
            except Exception as e:  # noqa: BLE001
                falhas = [f"erro no checador: {type(e).__name__}: {e}"]
            erro = next((r.texto for r in respostas if r.texto.startswith("[erro:")), None)
            if erro:
                falhas.append(erro)
    except Exception as e:  # noqa: BLE001 - falha de preparação (embedder fora do ar, `preparar`): é do caso, não do modelo
        falhas = [f"erro preparando o caso: {type(e).__name__}: {str(e)[:150]}"]
        erro_ambiente = True  # o caso não chegou a rodar o modelo: fica fora das médias de tempo
        if not respostas:
            respostas = [Resposta("[sem resposta]", [])]
            tempos = [{"total": 0.0, "primeira": None, "prefill": 0.0, "prefill_1a_volta": 0.0, "carga": 0.0,
                       "prompt_tokens": 0, "chamadas_llm": 0}]
    finally:
        if amb is not None:
            amb.fechar()  # os timers criados no caso viram tarefas asyncio: não deixa acumular a noite toda
        if memorias is not None:
            memorias.fechar()
        shutil.rmtree(pasta, ignore_errors=True)
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
        "erro_ambiente": erro_ambiente,
    }


async def _semear_memorias(cfg) -> Path:
    """Cria uma vez só o banco de memórias neutras (cada caso recebe uma cópia: 20 embeddings por caso custariam horas)."""
    pasta = Path(tempfile.mkdtemp(prefix="vision-eval-memorias-"))
    cfg.dados = pasta
    memorias = criar_memorias(cfg)
    try:
        for fato in MEMORIAS_NEUTRAS:
            await memorias.lembrar(fato)
    finally:
        memorias.fechar()
    return pasta / "memoria.db"


def _casos(suite: str) -> list[Caso]:
    return {"base": CASOS, "ferramentas": CASOS_FERRAMENTAS, "todas": [*CASOS, *CASOS_FERRAMENTAS]}[suite]


async def avaliar(modelo: str, pensar: bool, so: set[str] | None, repeticoes: int, suite: str, producao: bool,
                  avanco_min: float, com_atalhos: bool, sobrescrever: dict, salvar, base: datetime | None = None) -> dict:
    cfg = config.carregar(sobrescrever={"modelo.nome": modelo, "modelo.pensar": pensar, **sobrescrever})
    llm = criar_llm(cfg)
    metricas: list = []
    medir(llm, metricas)
    relogio = Relogio(avanco_min, base)
    banco: Path | None = None
    agora_original = tempo.agora
    tempo.agora = relogio.agora  # agenda, clima e timers falsos veem o mesmo relógio que o prompt
    avaliacao: dict = {}
    try:
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
        banco = await _semear_memorias(cfg) if producao else None
        resultados = []
        avaliacao.update({"modelo": modelo, "pensar": pensar, "pensar_efetivo": llm.pensar, "carga_s": carga,
                          "vram": "?", "resultados": resultados, "suite": suite, "producao": producao,
                          "avanco_min": avanco_min})
        for rep in range(repeticoes):
            for caso in _casos(suite):
                if so and caso.id not in so:
                    continue
                r = await rodar_caso(cfg, llm, caso, producao, relogio, com_atalhos, metricas, banco)
                r["rep"] = rep
                marca = "ok " if r["passou"] else "ERR"
                print(f"  {marca} [{rep + 1}] {caso.id:28} {r['tempos'][-1]['total']:5.1f}s  "
                      f"{'; '.join(r['falhas'])[:100]}", flush=True)
                resultados.append(r)
                salvar(avaliacao)
        avaliacao["vram"] = await _vram()
        salvar(avaliacao)
    finally:
        tempo.agora = agora_original
        if banco is not None:
            shutil.rmtree(banco.parent, ignore_errors=True)
        try:
            await llm.descarregar()  # também quando algo deu errado: o próximo modelo da noite não divide a placa
        except Exception:  # noqa: BLE001
            pass
    return avaliacao


async def _vram() -> str:
    import ollama

    try:
        ps = await ollama.AsyncClient().ps()
        return "; ".join(f"{m.model}: {m.size_vram / 2**30:.1f} GB VRAM de {m.size / 2**30:.1f} GB" for m in ps.models)
    except Exception:  # noqa: BLE001
        return "?"


def _rotulo(av: dict) -> str:
    if av["pensar"] and av.get("pensar_efetivo") is None:
        return av["modelo"] + " (sem thinking: igual ao normal)"  # o modelo não aceita o parâmetro
    return av["modelo"] + (" (pensando)" if av["pensar"] else "")


def relatorio(avaliacoes: list[dict]) -> str:
    ids = list(dict.fromkeys(r["id"] for r in avaliacoes[0]["resultados"]))
    reps = max(r.get("rep", 0) for r in avaliacoes[0]["resultados"]) + 1
    a0 = avaliacoes[0]
    producao = bool(a0.get("producao"))
    ambiente_txt = "ambiente completo (53 ferramentas)" if producao else "ambiente reduzido (agenda, memória e Orbit)"
    if producao:
        detalhe = (f"relógio sintético avança {a0.get('avanco_min', 0):g} min por fala · memória com embeddinggemma real e "
                   f"{len(MEMORIAS_NEUTRAS)} memórias neutras · confirmação: casos base em 'todas' (como a avaliação antiga)"
                   + (", casos de ferramentas em 'sensiveis' (a de produção)" if a0.get("suite") != "base" else ""))
    else:
        detalhe = "memória com embeddinggemma real, sem memórias neutras · confirmação 'todas'"
    linhas = ["# Avaliação do Vision", "",
              f"Gerado em {tempo.agora():%d/%m/%Y %H:%M} · {len(ids)} casos × {reps} rodada(s) · canal voz · {ambiente_txt} · "
              f"{detalhe}", ""]
    linhas += ["## Resumo", "", "| Modelo | Acertos | Tempo médio | p90 | 1ª palavra (média) | Prefill (média) | Insistências | Erros do ambiente | VRAM |",
               "|---|---|---|---|---|---|---|---|---|"]
    for av in avaliacoes:
        rs = av["resultados"]
        rodaram = [r for r in rs if not r.get("erro_ambiente")]  # falha de preparação não entra nas médias de tempo
        totais = [t["total"] for r in rodaram for t in r["tempos"]]
        primeiras = [t["primeira"] for r in rodaram for t in r["tempos"] if t["primeira"] is not None]
        prefills = [t.get("prefill", 0) for r in rodaram for t in r["tempos"] if t.get("chamadas_llm")]
        p90 = sorted(totais)[int(0.9 * (len(totais) - 1))] if totais else 0
        linhas.append(
            f"| {_rotulo(av)} | **{sum(r['passou'] for r in rs)}/{len(rs)}** | {statistics.mean(totais) if totais else 0:.1f}s | {p90:.1f}s | "
            f"{statistics.mean(primeiras) if primeiras else 0:.1f}s | {statistics.mean(prefills) if prefills else 0:.2f}s | "
            f"{sum(r['insistiu'] for r in rs)} | {sum(bool(r.get('erro_ambiente')) for r in rs)} | {av['vram']} |"
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


def ler_sobrescrever(itens: list[str]) -> dict:
    """`CHAVE=VALOR` -> {chave: valor}; o valor é JSON quando der ("8192", "true") e texto puro quando não ("-1m", "todas")."""
    saida: dict = {}
    for item in itens:
        chave, igual, valor = item.partition("=")
        if not igual or not chave.strip():
            raise ValueError(f"--sobrescrever espera CHAVE=VALOR, recebi {item!r}")
        try:
            saida[chave.strip()] = json.loads(valor)
        except ValueError:
            saida[chave.strip()] = valor
    return saida


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
    try:
        sobrescrever = ler_sobrescrever(args.sobrescrever)
        Relogio(args.avanco_min)  # valida o avanço antes de gastar a noite
    except ValueError as e:
        ap.error(str(e))
    producao = not args.antigo
    if args.antigo and args.suite != "base":
        print("--antigo só tem as ferramentas da suíte base: usando --suite base")
        args.suite = "base"
    configs = [(m, False) for m in args.modelos.split(",")]
    if args.pensar:
        configs.append((configs[0][0], True))
    avaliacoes = []
    base = dia_base()  # o mesmo dia para todos os modelos da noite
    saidas = RAIZ / "evals" / "saidas"
    saidas.mkdir(exist_ok=True)
    for modelo, pensar in configs:
        print(f"\n== {modelo}{' (pensando)' if pensar else ''} ==", flush=True)
        nome = modelo.replace(":", "-") + ("-pensando" if pensar else "")

        def salvar(av, nome=nome):
            """Atômico (uma noite interrompida no meio da escrita não deixa o JSON truncado) e à prova de arquivo preso
            por antivírus ou outro processo: tenta de novo e, se não der, só avisa. Gravar nunca derruba a avaliação."""
            tmp = saidas / f"{nome}.json.tmp"
            for tentativa in range(3):
                try:
                    tmp.write_text(json.dumps(av, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                    os.replace(tmp, saidas / f"{nome}.json")
                    return
                except OSError as e:
                    if tentativa == 2:
                        print(f"  aviso: não consegui gravar {nome}.json ({type(e).__name__}); sigo sem salvar", flush=True)
                    else:
                        time.sleep(0.5)

        try:
            av = await avaliar(modelo, pensar, so, args.repeticoes, args.suite, producao, args.avanco_min,
                               args.com_atalhos, sobrescrever, salvar, base)
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
