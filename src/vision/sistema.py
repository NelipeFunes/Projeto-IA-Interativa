"""Diagnóstico do PC, como o relatório de status do JARVIS: processador, memória, placa de vídeo, disco e há quanto
tempo o PC está ligado. Também diz quando algo passou do ponto (placa quente, disco quase cheio, memória no fim),
para o núcleo avisar sem ninguém perguntar."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Passou disso, vira alerta.
GPU_QUENTE_C = 85
CPU_QUENTE_C = 90
MEMORIA_CHEIA_PCT = 95
DISCO_CHEIO_PCT = 95
SEM_JANELA = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


@dataclass
class Placa:
    nome: str
    uso_pct: int
    temperatura_c: int | None
    vram_usada_mb: int
    vram_total_mb: int


@dataclass
class Estado:
    cpu_pct: float
    memoria_pct: float
    memoria_usada_gb: float
    memoria_total_gb: float
    disco_livre_gb: float
    disco_pct: float
    ligado_ha_s: float
    placas: list[Placa] = field(default_factory=list)


def _placas() -> list[Placa]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        saida = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=SEM_JANELA, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    placas = []
    for linha in saida.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) != 5:
            continue
        try:
            temp = int(partes[2]) if partes[2].isdigit() else None
            placas.append(Placa(partes[0], int(partes[1]), temp, int(partes[3]), int(partes[4])))
        except ValueError:
            continue
    return placas


def ler_estado(disco: Path | None = None) -> Estado:
    """Leitura bloqueante (~1 s, por causa do uso do processador): chame em thread."""
    import psutil

    disco = disco or Path(Path.home().anchor or "/")
    mem = psutil.virtual_memory()
    uso_disco = psutil.disk_usage(str(disco))
    return Estado(
        cpu_pct=psutil.cpu_percent(interval=0.5),
        memoria_pct=mem.percent, memoria_usada_gb=mem.used / 2**30, memoria_total_gb=mem.total / 2**30,
        disco_livre_gb=uso_disco.free / 2**30, disco_pct=uso_disco.percent,
        ligado_ha_s=time.time() - psutil.boot_time(), placas=_placas(),
    )


async def estado() -> Estado:
    return await asyncio.to_thread(ler_estado)


def _tempo_ligado(s: float) -> str:
    dias, resto = divmod(int(s), 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    if dias:
        return f"{dias} dia{'s' if dias > 1 else ''} e {horas} hora{'s' if horas != 1 else ''}"
    if horas:
        return f"{horas} hora{'s' if horas > 1 else ''} e {minutos} minuto{'s' if minutos != 1 else ''}"
    return f"{minutos} minuto{'s' if minutos != 1 else ''}"


def _br(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def relatorio(e: Estado) -> str:
    partes = [f"Processador em {round(e.cpu_pct)}%",
              f"memória em {round(e.memoria_pct)}% ({_br(e.memoria_usada_gb)} de {e.memoria_total_gb:.0f} GB)"]
    for p in e.placas:
        temp = f", {p.temperatura_c}°C" if p.temperatura_c is not None else ""
        partes.append(f"placa de vídeo em {p.uso_pct}%{temp}, {_br(p.vram_usada_mb / 1024)} de "
                      f"{p.vram_total_mb / 1024:.0f} GB de memória de vídeo")
    partes.append(f"{e.disco_livre_gb:.0f} GB livres no disco")
    texto = "; ".join(partes) + f". Ligado há {_tempo_ligado(e.ligado_ha_s)}."
    if alertas_ := alertas(e):
        texto += " Atenção: " + " ".join(alertas_)
    else:
        texto += " Tudo dentro do normal."
    return texto


def alertas(e: Estado) -> list[str]:
    lista = []
    for p in e.placas:
        if p.temperatura_c is not None and p.temperatura_c >= GPU_QUENTE_C:
            lista.append(f"a placa de vídeo está a {p.temperatura_c}°C.")
    if e.memoria_pct >= MEMORIA_CHEIA_PCT:
        lista.append(f"a memória está em {round(e.memoria_pct)}%.")
    if e.disco_pct >= DISCO_CHEIO_PCT:
        lista.append(f"o disco está quase cheio, só {e.disco_livre_gb:.0f} GB livres.")
    return lista


class Vigia:
    """Para o núcleo: cada alerta é avisado uma vez a cada `intervalo_s` (não repete a cada checagem)."""

    def __init__(self, intervalo_s: float = 3600, relogio=time.monotonic):
        self.intervalo_s = intervalo_s
        self.relogio = relogio
        self._ultimo: dict[str, float] = {}

    def novos(self, e: Estado) -> list[str]:
        agora = self.relogio()
        saida = []
        for alerta in alertas(e):
            tipo = alerta.split(" está")[0]  # "a placa de vídeo", "a memória", "o disco"
            if agora - self._ultimo.get(tipo, -1e18) >= self.intervalo_s:
                self._ultimo[tipo] = agora
                saida.append(alerta)
        return saida
