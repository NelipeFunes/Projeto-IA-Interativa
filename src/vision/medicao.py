"""Medição das ferramentas (T-018, etapa 1): quanto cada uma demora e quantas vezes a mesma pergunta se repete.

É a base para decidir o que vale pôr em cache e por quanto tempo (a validade de cada fonte sai destes números, não
de palpite). Uma linha por chamada em `data/medicoes/AAAA-MM-DD.jsonl`: horário, nome da ferramenta, duração,
se a ferramenta terminou sem erro e uma **impressão curta dos argumentos** (SHA-256 com sal desta instalação, 8
caracteres), só para saber se a mesma pergunta voltou. O conteúdo dos argumentos e o da resposta não são gravados
em texto. Atenção: a impressão não identifica uma pergunta de texto livre, mas **pode ser adivinhada para argumento
simples** (um sim/não, um nome de app), e o sal fica na mesma pasta; o que protege tudo isto é estar em `data/`, fora
do git. Ferramentas de escrita (ligar, apagar, lançar) gravam sem impressão: cache de escrita não existe.
Os arquivos de mais de `medicao.dias` dias são apagados.

Medir nunca atrapalha a conversa: qualquer erro de disco é engolido (e avisado uma vez).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAL = "sal.txt"
# Janelas para contar "a mesma pergunta de novo": o que um cache de 5 min e de 1 h teria respondido sem a fonte.
JANELAS_S = (300, 3600)


class Medidor:
    def __init__(self, pasta: Path, dias: int = 30, relogio=time.time):
        self.pasta = pasta
        self.dias = max(1, int(dias))
        self.relogio = relogio
        self._sal: str | None = None
        self._pasta_pronta = False
        self._limpeza_em: date | None = None
        self._avisou = False

    def _criar_pasta(self) -> None:
        if not self._pasta_pronta:
            self.pasta.mkdir(parents=True, exist_ok=True)
            self._pasta_pronta = True

    def _sal_da_instalacao(self) -> str:
        if self._sal is None:
            arquivo = self.pasta / SAL
            self._sal = self._ler_sal(arquivo)
            if not self._sal:
                self._criar_pasta()
                tmp = self.pasta / f"sal.{secrets.token_hex(4)}.tmp"
                tmp.write_text(secrets.token_hex(16), encoding="utf-8")
                try:
                    os.link(tmp, arquivo)  # só cria se ainda não existe: dois processos acabam com o mesmo sal
                except FileExistsError:
                    pass
                finally:
                    tmp.unlink(missing_ok=True)
                self._sal = self._ler_sal(arquivo)
        return self._sal

    @staticmethod
    def _ler_sal(arquivo: Path) -> str:
        try:
            return arquivo.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def impressao(self, args: dict[str, Any]) -> str:
        """Oito caracteres que identificam os argumentos sem mostrá-los."""
        canonico = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256((self._sal_da_instalacao() + canonico).encode("utf-8")).hexdigest()[:8]

    def registrar(self, ferramenta: str, args: dict[str, Any] | None, duracao_s: float, ok: bool) -> None:
        """`args=None`: não grava impressão (ferramentas de escrita)."""
        try:
            self._criar_pasta()
            agora = self.relogio()
            hoje = datetime.fromtimestamp(agora).date()
            linha = {"t": int(agora), "f": ferramenta, "ms": int(duracao_s * 1000), "ok": bool(ok),
                     "h": "" if args is None else self.impressao(args)}
            with (self.pasta / f"{hoje.isoformat()}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
            if self._limpeza_em != hoje:  # uma vez por dia, mesmo com o núcleo ligado por semanas
                self._limpeza_em = hoje
                self._apagar_velhos(hoje)
        except OSError as e:
            if not self._avisou:
                self._avisou = True
                log.warning("não deu para gravar a medição (aviso único): %s", e)

    def _apagar_velhos(self, hoje: date) -> None:
        corte = hoje - timedelta(days=self.dias)
        for arquivo in self.pasta.glob("*.jsonl"):
            try:
                if date.fromisoformat(arquivo.stem) < corte:
                    arquivo.unlink()
            except (ValueError, OSError):
                continue


def ler(pasta: Path, dias: int = 7, agora: float | None = None) -> list[dict[str, Any]]:
    """As chamadas dos últimos `dias` dias, em ordem de horário. Linha estragada é ignorada."""
    agora = time.time() if agora is None else agora
    desde = agora - dias * 86400
    itens: list[dict[str, Any]] = []
    for arquivo in sorted(pasta.glob("*.jsonl")):
        try:
            linhas = arquivo.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for linha in linhas:
            try:
                d = json.loads(linha)
                if isinstance(d, dict) and float(d["t"]) >= desde and isinstance(d["f"], str):
                    itens.append({"t": float(d["t"]), "f": d["f"], "ms": int(d["ms"]), "ok": bool(d["ok"]),
                                  "h": str(d["h"])})
            except (ValueError, KeyError, TypeError):
                continue
    itens.sort(key=lambda d: d["t"])
    return itens


def _percentil(valores: list[int], p: float) -> int:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(p * len(ordenados)))]


def resumir(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Por ferramenta: chamadas, % que terminou sem erro, mediana e p95 em ms, tempo total, e quantas chamadas um
    cache de 5 min e um de 1 h teriam respondido sozinhas. Cada janela é um cache simulado: a primeira chamada
    preenche, as seguintes dentro da validade são acertos e **não renovam** o prazo (uma pergunta a cada 4 min
    acerta uma de cada duas num cache de 5 min, não todas). Só entram chamadas que terminaram sem erro e que têm
    impressão (as de escrita não têm)."""
    por_ferramenta: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in itens:
        por_ferramenta[d["f"]].append(d)
    linhas = []
    for nome, chamadas in por_ferramenta.items():
        preenchido: list[dict[str, float]] = [{} for _ in JANELAS_S]
        acertos = [0] * len(JANELAS_S)
        economia_ms = [0] * len(JANELAS_S)
        for d in chamadas:
            if not d["ok"] or not d["h"]:
                continue
            for i, janela in enumerate(JANELAS_S):
                desde = preenchido[i].get(d["h"])
                if desde is not None and d["t"] - desde <= janela:
                    acertos[i] += 1
                    economia_ms[i] += d["ms"]
                else:  # falta (ou venceu): vai à fonte e preenche de novo
                    preenchido[i][d["h"]] = d["t"]
        duracoes = [d["ms"] for d in chamadas]
        linhas.append({
            "ferramenta": nome, "chamadas": len(chamadas),
            "sem_erro": sum(1 for d in chamadas if d["ok"]) / len(chamadas),
            "mediana_ms": int(statistics.median(duracoes)), "p95_ms": _percentil(duracoes, 0.95),
            "total_s": sum(duracoes) / 1000,
            "repetidas": acertos, "economia_s": [e / 1000 for e in economia_ms],
        })
    linhas.sort(key=lambda r: r["total_s"], reverse=True)
    return linhas


def relatorio(pasta: Path, dias: int = 7, agora: float | None = None) -> str:
    itens = ler(pasta, dias, agora)
    if not itens:
        return f"Sem medições nos últimos {dias} dias (pasta {pasta.name}). Use o Vision um pouco e rode de novo."
    linhas = resumir(itens)
    cab = (f"Ferramentas nos últimos {dias} dias ({len(itens)} chamadas). 'sem erro' = a ferramenta terminou sem "
           f"levantar erro (não quer dizer que o resultado foi útil). 'Repetidas' = chamadas que um cache de 5 min / "
           f"1 h teria respondido sozinho (cache simulado, só leituras); 'economia' = tempo que ele teria poupado.\n")
    tabela = [f"{'ferramenta':<22}{'n':>5}{'sem erro':>9}{'mediana':>9}{'p95':>8}{'total':>8}   "
              "repetidas 5min/1h   economia 5min/1h"]
    for r in linhas:
        tabela.append(
            f"{r['ferramenta'][:21]:<22}{r['chamadas']:>5}{r['sem_erro']:>9.0%}{r['mediana_ms']:>7}ms{r['p95_ms']:>6}ms"
            f"{r['total_s']:>7.1f}s   {r['repetidas'][0]:>8} / {r['repetidas'][1]:<8}  "
            f"{r['economia_s'][0]:>6.1f}s / {r['economia_s'][1]:.1f}s")
    return cab + "\n".join(tabela)
