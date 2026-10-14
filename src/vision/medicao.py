"""Medição das ferramentas (T-018, etapa 1): quanto cada uma demora e quantas vezes a mesma pergunta se repete.

É a base para decidir o que vale pôr em cache e por quanto tempo (a validade de cada fonte sai destes números, não
de palpite). Uma linha por chamada em `data/medicoes/AAAA-MM-DD.jsonl`: horário, nome da ferramenta, duração,
se deu certo e uma **impressão curta dos argumentos** (SHA-256 com sal próprio desta instalação, 8 caracteres), só
para saber se a mesma pergunta voltou. Nunca grava o conteúdo dos argumentos nem o da resposta. Os arquivos
ficam fora do git e os de mais de `medicao.dias` dias são apagados.

Medir nunca atrapalha a conversa: qualquer erro de disco é engolido.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
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
        self._limpou = False

    def _sal_da_instalacao(self) -> str:
        if self._sal is None:
            arquivo = self.pasta / SAL
            try:
                self._sal = arquivo.read_text(encoding="utf-8").strip()
            except OSError:
                self._sal = ""
            if not self._sal:
                self._sal = secrets.token_hex(16)
                self.pasta.mkdir(parents=True, exist_ok=True)
                arquivo.write_text(self._sal, encoding="utf-8")
        return self._sal

    def impressao(self, args: dict[str, Any]) -> str:
        """Oito caracteres que identificam os argumentos sem revelá-los."""
        canonico = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256((self._sal_da_instalacao() + canonico).encode("utf-8")).hexdigest()[:8]

    def registrar(self, ferramenta: str, args: dict[str, Any], duracao_s: float, ok: bool) -> None:
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            agora = self.relogio()
            linha = {"t": int(agora), "f": ferramenta, "ms": int(duracao_s * 1000), "ok": bool(ok),
                     "h": self.impressao(args)}
            arquivo = self.pasta / f"{datetime.fromtimestamp(agora).date().isoformat()}.jsonl"
            with arquivo.open("a", encoding="utf-8") as f:
                f.write(json.dumps(linha, ensure_ascii=False) + "\n")
            if not self._limpou:
                self._limpou = True
                self._apagar_velhos(agora)
        except OSError as e:
            log.warning("não deu para gravar a medição: %s", e)

    def _apagar_velhos(self, agora: float) -> None:
        corte = datetime.fromtimestamp(agora).date() - timedelta(days=self.dias)
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
    """Por ferramenta: chamadas, % de acerto, mediana e p95 em ms, tempo total, e quantas chamadas repetiam uma
    anterior (mesmos argumentos) dentro de 5 min e de 1 h, só entre as que deram certo."""
    por_ferramenta: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in itens:
        por_ferramenta[d["f"]].append(d)
    linhas = []
    for nome, chamadas in por_ferramenta.items():
        ultimo_certo: dict[str, float] = {}
        repetidas = [0] * len(JANELAS_S)
        economia_ms = [0] * len(JANELAS_S)
        for d in chamadas:
            if d["ok"] and d["h"] in ultimo_certo:
                atraso = d["t"] - ultimo_certo[d["h"]]
                for i, janela in enumerate(JANELAS_S):
                    if atraso <= janela:
                        repetidas[i] += 1
                        economia_ms[i] += d["ms"]
            if d["ok"]:
                ultimo_certo[d["h"]] = d["t"]
        duracoes = [d["ms"] for d in chamadas]
        linhas.append({
            "ferramenta": nome, "chamadas": len(chamadas),
            "acertos": sum(1 for d in chamadas if d["ok"]) / len(chamadas),
            "mediana_ms": _percentil(duracoes, 0.5), "p95_ms": _percentil(duracoes, 0.95),
            "total_s": sum(duracoes) / 1000,
            "repetidas": repetidas, "economia_s": [e / 1000 for e in economia_ms],
        })
    linhas.sort(key=lambda r: r["total_s"], reverse=True)
    return linhas


def relatorio(pasta: Path, dias: int = 7, agora: float | None = None) -> str:
    itens = ler(pasta, dias, agora)
    if not itens:
        return f"Sem medições nos últimos {dias} dias (pasta {pasta.name}). Use o Vision um pouco e rode de novo."
    linhas = resumir(itens)
    cab = (f"Ferramentas nos últimos {dias} dias ({len(itens)} chamadas). 'Repetidas' = mesma pergunta que já tinha "
           f"dado certo antes, dentro de 5 min / 1 h; 'economia' = tempo que um cache teria poupado.\n")
    tabela = [f"{'ferramenta':<22}{'n':>5}{'ok':>6}{'mediana':>9}{'p95':>8}{'total':>8}   repetidas 5min/1h   economia 5min/1h"]
    for r in linhas:
        tabela.append(
            f"{r['ferramenta'][:21]:<22}{r['chamadas']:>5}{r['acertos']:>6.0%}{r['mediana_ms']:>7}ms{r['p95_ms']:>6}ms"
            f"{r['total_s']:>7.1f}s   {r['repetidas'][0]:>8} / {r['repetidas'][1]:<8}  "
            f"{r['economia_s'][0]:>6.1f}s / {r['economia_s'][1]:.1f}s")
    return cab + "\n".join(tabela)
