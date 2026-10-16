"""Processo de voz do Qwen3-TTS (faster-qwen3-tts). Roda no ambiente `servidor-voz/`, NÃO no do Vision: o Qwen3-TTS exige
`transformers` 5.15.1 e o XTTS exige `transformers` menor que 5, então os dois não convivem num ambiente só.

Fala com o Vision só por pipes (stdin/stdout): sem rede, sem porta. Este arquivo é autônomo de propósito (nada de
`import vision`): o outro ambiente não tem as dependências do Vision.

Protocolo
- Vision → processo (stdin), uma linha JSON por mensagem:
    {"op": "falar", "id": 7, "texto": "...", "pedaco": 8}   gera e manda o áudio em pedaços
    {"op": "cancelar", "id": 7}                              para a geração do pedido 7 no próximo pedaço
    {"op": "sair"}                                           encerra (fechar o stdin também encerra)
- processo → Vision (stdout), quadros binários `<tipo:1 byte><id:uint32><tamanho:uint32><carga>` (little-endian):
    b"P" pronto (carga: JSON {"taxa": 24000})      b"A" áudio (carga: float32 mono)
    b"F" fim do pedido (sem carga)                  b"E" erro do pedido (carga: texto UTF-8)
  Tudo o que a biblioteca imprimir vai para o stderr: o stdout é só o protocolo."""

from __future__ import annotations

import argparse
import json
import os
import queue
import struct
import sys
import tempfile
import threading

import numpy as np

CABECALHO = struct.Struct("<cII")
NIVEL_DA_REFERENCIA = 0.08  # RMS: o mesmo volume do Piper depois do acabamento (vision/voice/tts.py)
PICO_DA_REFERENCIA = 0.95


def nivelar_referencia(entrada: str, saida: str, alvo: float = NIVEL_DA_REFERENCIA) -> bool:
    """Grava em `saida` a gravação com o volume ajustado para RMS `alvo` (mono, teto de pico). O Qwen3-TTS fala no volume
    da gravação que clona: uma gravação baixa (RMS 0,02) dava uma voz baixa. Devolve False se não deu (silêncio, erro)."""
    import soundfile as sf

    audio, taxa = sf.read(entrada, dtype="float32")
    audio = audio if audio.ndim == 1 else audio.mean(axis=1)
    rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
    if rms < 1e-5:
        return False
    audio = audio * (alvo / rms)
    pico = float(np.abs(audio).max())
    if pico > PICO_DA_REFERENCIA:
        audio *= PICO_DA_REFERENCIA / pico
    sf.write(saida, audio, taxa, subtype="PCM_16")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", required=True, help="pasta do Qwen3-TTS-12Hz-0.6B-Base")
    ap.add_argument("--referencia", required=True, help="gravação de referência (.wav)")
    ap.add_argument("--texto-referencia", required=True, help="arquivo .txt com a transcrição exata da gravação")
    ap.add_argument("--idioma", default="Portuguese")
    ap.add_argument("--so-timbre", action="store_true", help="usa só o timbre da gravação (xvec): sem o sotaque dela")
    ap.add_argument("--nivel-referencia", type=float, default=NIVEL_DA_REFERENCIA,
                    help="RMS para o qual a gravação é ajustada antes de clonar (0 = não ajusta)")
    args = ap.parse_args()

    saida = sys.stdout.buffer
    sys.stdout = sys.stderr  # print() perdido da biblioteca não pode estragar o protocolo
    trava_saida = threading.Lock()

    def enviar(tipo: bytes, pedido: int, carga: bytes = b"") -> None:
        with trava_saida:
            saida.write(CABECALHO.pack(tipo, pedido, len(carga)) + carga)
            saida.flush()

    try:
        from faster_qwen3_tts import FasterQwen3TTS

        texto_ref = open(args.texto_referencia, encoding="utf-8").read().strip()
        referencia = args.referencia
        if args.nivel_referencia > 0:
            nivelada = os.path.join(tempfile.mkdtemp(prefix="vision-qwen3-"), "referencia.wav")
            if nivelar_referencia(args.referencia, nivelada, args.nivel_referencia):
                referencia = nivelada
        modelo = FasterQwen3TTS.from_pretrained(args.modelo)
        modelo.warmup()

        def gerar(texto: str, pedaco: int):
            return modelo.generate_voice_clone_streaming(
                text=texto, language=args.idioma, ref_audio=referencia, ref_text=texto_ref,
                chunk_size=pedaco, xvec_only=args.so_timbre)

        for _ in gerar("Oi.", 8):  # monta e guarda o prompt da gravação: o 1º pedido real não paga isso
            pass
    except BaseException as e:  # noqa: BLE001 - quem sobe o processo precisa saber por quê
        enviar(b"E", 0, f"{type(e).__name__}: {e}".encode("utf-8", "replace"))
        return 1
    enviar(b"P", 0, json.dumps({"taxa": 24000}).encode())

    pedidos: queue.Queue[dict | None] = queue.Queue()
    cancelados: set[int] = set()
    trava_cancelados = threading.Lock()

    def ler_stdin() -> None:
        for linha in sys.stdin:
            try:
                msg = json.loads(linha)
            except ValueError:
                continue
            if msg.get("op") == "cancelar":
                with trava_cancelados:
                    cancelados.add(int(msg.get("id", -1)))
            elif msg.get("op") == "sair":
                break
            else:
                pedidos.put(msg)
        pedidos.put(None)  # stdin fechado (o Vision morreu ou saiu): não deixa processo órfão

    threading.Thread(target=ler_stdin, daemon=True).start()

    def cancelado(pedido: int) -> bool:
        with trava_cancelados:
            return pedido in cancelados

    while (msg := pedidos.get()) is not None:
        pedido = int(msg.get("id", 0))
        gerador = None
        try:
            gerador = gerar(str(msg["texto"]), int(msg.get("pedaco", 8)))
            for audio, _taxa, _tempo in gerador:
                if cancelado(pedido):
                    break
                enviar(b"A", pedido, np.asarray(audio, dtype="<f4").reshape(-1).tobytes())
            enviar(b"F", pedido)
        except BaseException as e:  # noqa: BLE001 - o erro vai para o Vision, que fala com a reserva
            enviar(b"E", pedido, f"{type(e).__name__}: {e}".encode("utf-8", "replace"))
        finally:
            if gerador is not None:
                gerador.close()
            with trava_cancelados:
                cancelados.discard(pedido)
    return 0


if __name__ == "__main__":
    sys.exit(main())
