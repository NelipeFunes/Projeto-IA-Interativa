"""Cache único do Vision (T-018, etapa 2): valores com hora, validade, tamanho máximo e, se pedir, em arquivo.

Agenda, buscas na web, manchetes, clima e a lista de programas do PC usam este módulo, com as mesmas regras:
- um valor vale `validade_s` segundos (quem lê pode pedir outra validade para aquele valor);
- acima de `maximo` entradas, saem as mais velhas;
- arquivo estragado, apagado ou de outra versão do formato vale cache vazio, nunca erro;
- gravação em disco atômica (um arquivo pela metade nunca fica no lugar do bom);
- o relógio é injetável (os testes não esperam de verdade).

Em memória, o relógio certo é `time.monotonic` (não sofre com ajuste da hora do Windows); em arquivo é `time.time`,
que sobrevive a reinício. Quem cria o cache escolhe.

Fora daqui (de propósito): credenciais com validade (token do Spotify, sessão da TV, sessão da piscina), que não
são cache e nunca vão para `data/cache/`, e a lista de luzes da Alexa, que é configuração refeita por comando.
Texto de terceiros que sai daqui (páginas lidas, buscas) continua sendo conteúdo externo para quem o usa: o cache
não muda isso (a `Busca` vinda dele sai com `do_cache=True`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vision.arquivos import gravar_json, ler_json

log = logging.getLogger(__name__)

VERSAO_PADRAO = 1


class Cache:
    def __init__(self, validade_s: float | None, *, arquivo: Path | None = None, maximo: int = 200,
                 relogio: Callable[[], float] = time.time, versao: int = VERSAO_PADRAO, nome: str = "cache"):
        """`validade_s=None`: nunca vence por tempo (só sai por falta de espaço ou por `apagar`).
        `arquivo`: guarda em JSON (as entradas sobrevivem a reinício); sem ele, só em memória."""
        self.validade_s = validade_s
        self.arquivo = arquivo
        self.maximo = max(1, int(maximo))
        self.relogio = relogio
        self.versao = versao
        self.nome = nome
        self.acertos = 0
        self.faltas = 0
        self._itens: dict[str, dict[str, Any]] = {}
        self._avisou = False
        if arquivo is not None:
            dados = ler_json(arquivo)
            if isinstance(dados, dict):
                self._itens = {str(k): v for k, v in dados.items()
                               if isinstance(v, dict) and "valor" in v and isinstance(v.get("quando"), int | float)
                               and v.get("v", VERSAO_PADRAO) == versao}

    # ------------------------------------------------------------------ leitura

    def _idade(self, item: dict[str, Any]) -> float:
        return self.relogio() - float(item["quando"])

    def pegar(self, chave: str, validade_s: float | None = None) -> Any | None:
        """O valor guardado, ou None se não há ou venceu. `validade_s`: outra validade só para esta leitura (as
        manchetes valem 30 min no mesmo arquivo em que as buscas valem 6 h)."""
        item = self._itens.get(chave)
        limite = self.validade_s if validade_s is None else validade_s
        if item is None or (limite is not None and self._idade(item) > limite):
            self.faltas += 1
            return None
        self.acertos += 1
        return item["valor"]

    def pegar_velho(self, chave: str) -> tuple[Any, float] | None:
        """(valor, idade em segundos) mesmo vencido: para responder "dado de 20 min atrás" quando a fonte falha."""
        item = self._itens.get(chave)
        return None if item is None else (item["valor"], max(0.0, self._idade(item)))

    def estatisticas(self) -> dict[str, Any]:
        total = self.acertos + self.faltas
        return {"nome": self.nome, "acertos": self.acertos, "faltas": self.faltas,
                "taxa": (self.acertos / total) if total else None, "entradas": len(self._itens)}

    def __len__(self) -> int:
        return len(self._itens)

    # ------------------------------------------------------------------ escrita

    def guardar(self, chave: str, valor: Any) -> None:
        item: dict[str, Any] = {"quando": self.relogio(), "valor": valor}
        if self.versao != VERSAO_PADRAO:
            item["v"] = self.versao
        self._itens[chave] = item
        if len(self._itens) > self.maximo:  # tira os mais velhos
            sobra = len(self._itens) - self.maximo
            for velha in sorted(self._itens, key=lambda k: self._itens[k]["quando"])[:sobra]:
                del self._itens[velha]
        self._salvar()

    def apagar(self, chave: str) -> None:
        if self._itens.pop(chave, None) is not None:
            self._salvar()

    def limpar(self, prefixo: str = "") -> int:
        """Apaga as entradas cuja chave começa com `prefixo` (tudo, sem ele). Devolve quantas saíram."""
        saem = [k for k in self._itens if k.startswith(prefixo)]
        for k in saem:
            del self._itens[k]
        if saem:
            self._salvar()
        return len(saem)

    def _salvar(self) -> None:
        if self.arquivo is None:
            return
        try:
            gravar_json(self.arquivo, self._itens)
        except (OSError, TypeError, ValueError) as e:  # disco cheio, arquivo em uso, valor que não vira JSON
            if not self._avisou:
                self._avisou = True
                log.warning("não deu para gravar o cache %s (aviso único): %s", self.nome, e)
