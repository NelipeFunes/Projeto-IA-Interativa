"""Carrega config.yaml e .env e resolve os caminhos do projeto."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    bruto: dict[str, Any]
    raiz: Path = RAIZ
    dados: Path = field(default_factory=lambda: RAIZ / "data")
    modelos: Path = field(default_factory=lambda: RAIZ / "modelos")

    def get(self, chave: str, padrao: Any = None) -> Any:
        """Acesso por caminho pontilhado: cfg.get("modelo.nome")."""
        atual: Any = self.bruto
        for parte in chave.split("."):
            if not isinstance(atual, dict) or parte not in atual:
                return padrao
            atual = atual[parte]
        return atual

    def caminho(self, relativo: str) -> Path:
        p = Path(os.path.expandvars(relativo))
        return p if p.is_absolute() else self.raiz / p


def carregar(arquivo: Path | None = None, sobrescrever: dict[str, Any] | None = None) -> Config:
    load_dotenv(RAIZ / ".env")
    arquivo = arquivo or RAIZ / "config.yaml"
    bruto = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    for chave, valor in (sobrescrever or {}).items():
        _definir(bruto, chave, valor)
    cfg = Config(bruto=bruto)
    cfg.dados.mkdir(exist_ok=True)
    return cfg


def _definir(d: dict[str, Any], chave: str, valor: Any) -> None:
    partes = chave.split(".")
    for parte in partes[:-1]:
        d = d.setdefault(parte, {})
    d[partes[-1]] = valor
