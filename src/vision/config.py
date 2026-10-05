"""Carrega config.yaml e .env e resolve os caminhos do projeto."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
# O que você muda pela tela de ajustes (vision/ajustes.py): em data/, fora do git, por cima do config.yaml.
AJUSTES_LOCAIS = "config-local.yaml"
# Só estas chaves saem do arquivo local (as mesmas de vision/ajustes.py): o resto é ignorado.
CHAVES_AJUSTAVEIS = {"assistente.nome", "voz.voz_piper", "voz.velocidade_fala", "voz.conversa_silencio_max_s",
                     "voz.microfone", "clima.cidade"}  # a cidade é dado pessoal: só no arquivo local
# O interruptor de cada conexão na tela de Conexões (vision/conexoes.py): só aceita true/false.
CHAVES_LIGA_DESLIGA = {"mcp.google-calendar.ativo", "spotify.ativo", "alexa.ativo", "mcp.wispr.ativo",
                       "web.ativo", "mcp.orbit.ativo", "tv.ativo"}
CHAVES_AJUSTAVEIS |= CHAVES_LIGA_DESLIGA
# Gravadas pela tela de Conexões (não pela de Ajustes). O IP da TV é da sua rede: só no arquivo local.
CHAVES_DE_CONEXAO = {"tv.ip"}


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


def manter_carregado(cfg: Config) -> str | int:
    """`modelo.manter_carregado` como o Ollama aceita: "30m", "-1m" ou um número de segundos (negativo = sempre).
    "-1" como texto (sem unidade) o Ollama recusa ("missing unit"): número puro vira int (02/10)."""
    valor = cfg.get("modelo.manter_carregado", "30m")
    if isinstance(valor, bool):
        return "30m"
    try:
        numero = float(valor) if isinstance(valor, int | float) else float(str(valor).strip())
    except ValueError:
        return str(valor).strip() or "30m"
    return int(numero) if math.isfinite(numero) else -1  # "inf" = sempre (int(inf) levantaria OverflowError)


def carregar(arquivo: Path | None = None, sobrescrever: dict[str, Any] | None = None) -> Config:
    # Sem interpolação: uma senha com "${" (gravada pela tela de Conexões) chega como foi digitada.
    load_dotenv(RAIZ / ".env", interpolate=False)
    arquivo = arquivo or RAIZ / "config.yaml"
    bruto = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    if os.environ.get("VISION_SEM_AJUSTES") != "1":  # os testes não herdam o que você mudou na janela
        for chave, valor in ler_ajustes(RAIZ / "data" / AJUSTES_LOCAIS).items():
            if chave == "clima.cidade" and not (isinstance(valor, str) and len(valor) <= 60):
                continue
            if chave == "tv.ip" and not _ip_local(valor):
                continue
            if chave in CHAVES_LIGA_DESLIGA and not isinstance(valor, bool):
                continue
            valido = not (chave == "voz.voz_piper" and not _nome_de_arquivo(valor))
            if (chave in CHAVES_AJUSTAVEIS or chave in CHAVES_DE_CONEXAO) and valido:
                _definir(bruto, chave, valor)
    for chave, valor in (sobrescrever or {}).items():
        _definir(bruto, chave, valor)
    cfg = Config(bruto=bruto)
    cfg.dados.mkdir(exist_ok=True)
    return cfg


def ler_ajustes(arquivo: Path) -> dict[str, Any]:
    """{"voz.voz_piper": ..., ...}. Arquivo ausente ou estragado: nenhum ajuste (vale o config.yaml)."""
    try:
        dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return {str(k): v for k, v in dados.items()} if isinstance(dados, dict) else {}


def _ip_local(valor: Any) -> bool:
    """tv.ip: só um IPv4 da rede local (a TV). A regra é a mesma do cliente da TV (vision/tv.py)."""
    from vision.tv import ip_valido

    return ip_valido(valor) is not None


def _nome_de_arquivo(valor: Any) -> bool:
    return isinstance(valor, str) and bool(valor) and not any(c in valor for c in "/\\:") and ".." not in valor


def salvar_ajustes(cfg: Config, mudancas: dict[str, Any]) -> None:
    """Grava as mudanças em data/config-local.yaml e já aplica no cfg em memória."""
    arquivo = cfg.dados / AJUSTES_LOCAIS
    todos = {**ler_ajustes(arquivo), **mudancas}
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(yaml.safe_dump(todos, allow_unicode=True, sort_keys=True), encoding="utf-8")
    temporario.replace(arquivo)  # nunca fica pela metade
    for chave, valor in mudancas.items():
        _definir(cfg.bruto, chave, valor)


def _definir(d: dict[str, Any], chave: str, valor: Any) -> None:
    partes = chave.split(".")
    for parte in partes[:-1]:
        d = d.setdefault(parte, {})
    d[partes[-1]] = valor
