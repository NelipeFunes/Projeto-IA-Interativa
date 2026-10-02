"""Clima pela Open-Meteo (grátis, sem conta e sem chave): agora e os próximos 7 dias.

A cidade vem de `clima.cidade` (no config local, fora do git: a cidade de quem usa é dado pessoal). As coordenadas
dela ficam em cache em data/cache/clima.json, e a previsão fica 15 min em memória.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
PREVISAO = "https://api.open-meteo.com/v1/forecast"
VALIDADE_S = 15 * 60
MAX_CIDADES = 20
CIDADE_VALIDA = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '\-]){0,59}$")  # letras, espaço, hífen e apóstrofo

# Códigos WMO da Open-Meteo, em português falável.
TEMPO = {
    0: "céu limpo", 1: "quase sem nuvens", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina com geada",
    51: "garoa fraca", 53: "garoa", 55: "garoa forte", 56: "garoa congelante", 57: "garoa congelante forte",
    61: "chuva fraca", 63: "chuva", 65: "chuva forte", 66: "chuva congelante", 67: "chuva congelante forte",
    71: "neve fraca", 73: "neve", 75: "neve forte", 77: "granizo fino",
    80: "pancadas de chuva fracas", 81: "pancadas de chuva", 82: "pancadas de chuva fortes",
    85: "pancadas de neve", 86: "pancadas de neve fortes",
    95: "trovoadas", 96: "trovoadas com granizo", 99: "trovoadas fortes com granizo",
}


class ErroClima(RuntimeError):
    pass


@dataclass
class Dia:
    data: date
    tempo: str
    maxima: float
    minima: float
    chuva_pct: int | None


@dataclass
class Previsao:
    cidade: str
    temperatura: float
    sensacao: float
    tempo: str
    umidade: int | None
    vento_kmh: float | None
    dias: list[Dia]

    def dia(self, d: date) -> Dia | None:
        return next((x for x in self.dias if x.data == d), None)


def _graus(v: float) -> str:
    return f"{round(v)} graus"


def descrever_agora(p: Previsao) -> str:
    texto = f"Em {p.cidade}, {_graus(p.temperatura)} agora, {p.tempo}"
    if abs(p.sensacao - p.temperatura) >= 3:
        texto += f", com sensação de {_graus(p.sensacao)}"
    return texto + "."


def descrever_dia(d: Dia, rotulo: str) -> str:
    texto = f"{rotulo.capitalize()}: {d.tempo}, máxima de {_graus(d.maxima)} e mínima de {_graus(d.minima)}"
    if d.chuva_pct is not None and d.chuva_pct >= 20:
        texto += f", {d.chuva_pct}% de chance de chuva"
    return texto + "."


class Clima:
    def __init__(self, pasta_cache: Path, cidade: str = "", transporte: httpx.AsyncBaseTransport | None = None,
                 relogio=time.monotonic):
        self.cidade = " ".join(str(cidade or "").split())
        self.arquivo = pasta_cache / "clima.json"
        self._transporte = transporte
        self._relogio = relogio
        self._previsoes: dict[str, tuple[float, Previsao]] = {}

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10, transport=self._transporte)

    def _coordenadas_guardadas(self) -> dict[str, Any]:
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
            return dados if isinstance(dados, dict) else {}
        except (OSError, ValueError):
            return {}

    async def _coordenadas(self, cidade: str) -> tuple[float, float, str]:
        chave = cidade.lower()
        guardadas = self._coordenadas_guardadas()
        if isinstance(g := guardadas.get(chave), dict) and {"lat", "lon", "nome"} <= g.keys():
            return float(g["lat"]), float(g["lon"]), str(g["nome"])
        try:
            async with self._cliente() as c:
                r = await c.get(GEOCODING, params={"name": cidade, "count": 1, "language": "pt", "format": "json"})
        except httpx.HTTPError as e:
            raise ErroClima(f"sem conexão com o serviço de clima ({type(e).__name__})") from e
        if r.status_code != 200:
            raise ErroClima(f"o serviço de clima respondeu {r.status_code}")
        try:
            achados = (r.json() or {}).get("results") or []
            a = achados[0] if achados else None
            lat, lon = (float(a["latitude"]), float(a["longitude"])) if a else (None, None)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ErroClima("resposta do serviço de clima fora do formato") from e
        if a is None:
            raise ErroClima(f"não achei a cidade '{cidade}'")
        nome = str(a.get("name") or cidade)
        guardadas[chave] = {"lat": lat, "lon": lon, "nome": nome}
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(guardadas, ensure_ascii=False), encoding="utf-8", newline="")
            os.replace(tmp, self.arquivo)
        except OSError as e:
            log.warning("não deu para guardar as coordenadas: %s", e)
        return lat, lon, nome

    async def previsao(self, cidade: str | None = None) -> Previsao:
        cidade = " ".join(str(cidade or "").split()) or self.cidade
        if not cidade:
            raise ErroClima("não sei a cidade: ponha clima.cidade no config local (ou diga a cidade)")
        if not CIDADE_VALIDA.match(cidade):
            raise ErroClima("isso não parece nome de cidade")
        chave = cidade.lower()
        if (guardada := self._previsoes.get(chave)) and self._relogio() - guardada[0] < VALIDADE_S:
            return guardada[1]
        lat, lon, nome = await self._coordenadas(cidade)
        params = {
            "latitude": lat, "longitude": lon, "timezone": "America/Sao_Paulo", "forecast_days": 7,
            "current": "temperature_2m,apparent_temperature,weather_code,relative_humidity_2m,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        }
        try:
            async with self._cliente() as c:
                r = await c.get(PREVISAO, params=params)
        except httpx.HTTPError as e:
            raise ErroClima(f"sem conexão com o serviço de clima ({type(e).__name__})") from e
        if r.status_code != 200:
            raise ErroClima(f"o serviço de clima respondeu {r.status_code}")
        try:
            p = _ler(r.json(), nome)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            raise ErroClima("resposta do serviço de clima fora do formato") from e
        if len(self._previsoes) >= MAX_CIDADES:
            self._previsoes.pop(next(iter(self._previsoes)))
        self._previsoes[chave] = (self._relogio(), p)
        return p


def _ler(dados: dict[str, Any], nome: str) -> Previsao:
    atual, diario = dados["current"], dados["daily"]
    dias = []
    chuva = diario.get("precipitation_probability_max") or []
    for i, d in enumerate(diario["time"]):
        dias.append(Dia(date.fromisoformat(d), TEMPO.get(int(diario["weather_code"][i]), "tempo variável"),
                        float(diario["temperature_2m_max"][i]), float(diario["temperature_2m_min"][i]),
                        int(chuva[i]) if i < len(chuva) and chuva[i] is not None else None))
    return Previsao(
        nome, float(atual["temperature_2m"]), float(atual.get("apparent_temperature", atual["temperature_2m"])),
        TEMPO.get(int(atual["weather_code"]), "tempo variável"),
        int(atual["relative_humidity_2m"]) if atual.get("relative_humidity_2m") is not None else None,
        float(atual["wind_speed_10m"]) if atual.get("wind_speed_10m") is not None else None, dias,
    )
