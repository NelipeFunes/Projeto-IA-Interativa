"""Processo falso com o mesmo protocolo do vision/voice/qwen_worker.py (só biblioteca padrão), para testar o `VozQwen`.
O comportamento vem de FAKE_MODO: normal | lento | nao_sobe (FAKE_RUIDO: chiado cru nas pausas); e do texto do pedido: "quebra" manda erro, "morre" derruba
o processo no meio da fala. FAKE_LOG (arquivo) recebe uma linha JSON por mensagem recebida."""

import json
import math
import os
import queue
import random
import struct
import sys
import threading
import time
from array import array

CABECALHO = struct.Struct("<cII")
TAXA = 24000
saida = sys.stdout.buffer
modo = os.environ.get("FAKE_MODO", "normal")
log = os.environ.get("FAKE_LOG")
trava = threading.Lock()


def enviar(tipo: bytes, pedido: int, carga: bytes = b"") -> None:
    with trava:
        saida.write(CABECALHO.pack(tipo, pedido, len(carga)) + carga)
        saida.flush()


def anotar(msg: dict) -> None:
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg) + "\n")


def tom(segundos: float) -> list[float]:
    return [0.3 * math.sin(2 * math.pi * 220 * i / TAXA) for i in range(int(TAXA * segundos))]


RUIDO = float(os.environ.get("FAKE_RUIDO", "0"))
sorteio = random.Random(0)


def mudo(segundos: float) -> list[float]:
    return [sorteio.gauss(0, RUIDO) if RUIDO else 0.0 for _ in range(int(TAXA * segundos))]


if modo == "nao_sobe":
    enviar(b"E", 0, b"RuntimeError: sem placa de video (simulado)")
    sys.exit(1)
enviar(b"P", 0, json.dumps({"taxa": TAXA}).encode())

pedidos: queue.Queue = queue.Queue()
cancelados: set[int] = set()


def ler() -> None:
    for linha in sys.stdin:
        msg = json.loads(linha)
        anotar(msg)
        if msg.get("op") == "cancelar":
            cancelados.add(int(msg["id"]))
        elif msg.get("op") == "sair":
            break
        else:
            pedidos.put(msg)
    pedidos.put(None)


threading.Thread(target=ler, daemon=True).start()
while (msg := pedidos.get()) is not None:
    pid, texto = int(msg["id"]), msg["texto"]
    if "quebra" in texto:
        enviar(b"E", pid, b"RuntimeError: falha simulada")
        continue
    if modo == "lento":
        amostras = tom(0.25) * 40  # 10 s de som, em pedaços de 0,25 s, devagar
    else:  # 0,1 s mudo, fala, 1 s de pausa, fala, 0,5 s mudo
        amostras = mudo(0.1) + tom(0.4) + mudo(1.0) + tom(0.4) + mudo(0.5)
    passo = int(TAXA * 0.25)
    for i in range(0, len(amostras), passo):
        if pid in cancelados:
            break
        enviar(b"A", pid, array("f", amostras[i:i + passo]).tobytes())
        if "morre" in texto:
            os._exit(3)
        if modo == "lento":
            time.sleep(0.03)
    enviar(b"F", pid)
    cancelados.discard(pid)
