"""O processo de verdade (src/vision/voice/qwen_worker.py), com um módulo falso `faster_qwen3_tts` no lugar do modelo.
Sem GPU. Cobre o que o processo falso de test_tts_qwen.py não cobre: o stdout protegido, a gravação nivelada, o teto de
passos e o fim quando o stdin fecha."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

from vision.voice import qwen_worker  # noqa: E402
from vision.voice.tts_qwen import CABECALHO, _ler_exato  # noqa: E402

MODULO_FALSO = '''
import json
import os

import numpy as np


class FasterQwen3TTS:
    @classmethod
    def from_pretrained(cls, caminho):
        print("print perdido da biblioteca")
        os.write(1, b"escrita nativa perdida no descritor 1\\n")
        return cls()

    def warmup(self):
        pass

    def generate_voice_clone_streaming(self, text, language, ref_audio, ref_text, chunk_size, xvec_only, max_new_tokens):
        with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "language": language, "ref_audio": ref_audio, "ref_text": ref_text,
                                "chunk": chunk_size, "xvec": xvec_only, "max": max_new_tokens}) + "\\n")
        for i in range(3):
            if i == 1:
                os.write(1, b"lixo nativo entre os quadros\\n")
            yield np.full(480, 0.1 * (i + 1), dtype=np.float32), 24000, {}
'''


def _quadro(proc):
    cab = _ler_exato(proc.stdout, CABECALHO.size)
    assert cab is not None, "o processo fechou o stdout"
    tipo, pedido, tamanho = CABECALHO.unpack(cab)
    assert tipo in (b"P", b"A", b"F", b"E") and tamanho < 1_000_000, f"quadro inválido: {tipo!r} {tamanho}"
    return tipo, pedido, (_ler_exato(proc.stdout, tamanho) if tamanho else b"")


def _enviar(proc, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode("ascii"))
    proc.stdin.flush()


def test_o_processo_de_verdade_protege_o_protocolo_nivela_a_gravacao_e_acaba_no_fim_do_stdin(tmp_path):
    (tmp_path / "modulo").mkdir()
    (tmp_path / "modulo" / "faster_qwen3_tts.py").write_text(MODULO_FALSO, encoding="utf-8")
    referencia = tmp_path / "voz.wav"
    t = np.arange(24000) / 24000
    sf.write(referencia, (0.03 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000)  # RMS ~0,02: baixa
    (tmp_path / "voz.txt").write_text("texto da gravação", encoding="utf-8")
    nivelada = tmp_path / ".voz-nivelada.wav"
    registro = tmp_path / "chamadas.jsonl"
    temporaria = tmp_path / "temporaria"  # a pasta temporária DESTA execução: nada pode ficar esquecido nela
    temporaria.mkdir()
    ambiente = {**os.environ, "PYTHONPATH": str(tmp_path / "modulo"), "STUB_LOG": str(registro),
                "TEMP": str(temporaria), "TMP": str(temporaria), "TMPDIR": str(temporaria)}
    proc = subprocess.Popen(
        [sys.executable, qwen_worker.__file__, "--modelo", str(tmp_path), "--referencia", str(referencia),
         "--texto-referencia", str(tmp_path / "voz.txt"), "--nivelada-em", str(nivelada)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ambiente)
    try:
        tipo, _, carga = _quadro(proc)  # o 1º quadro é o "pronto", apesar do que a biblioteca escreveu ao carregar
        assert tipo == b"P" and json.loads(carga) == {"taxa": 24000}

        _enviar(proc, {"op": "falar", "id": 7, "texto": "Oi mundo", "pedaco": 8})
        audios = []
        while (quadro := _quadro(proc))[0] == b"A":
            assert quadro[1] == 7
            audios.append(np.frombuffer(quadro[2], dtype="<f4"))
        assert quadro[0] == b"F" and quadro[1] == 7  # e o lixo escrito no meio não virou quadro nenhum
        assert len(audios) == 3 and all(a.size == 480 for a in audios)
        assert [round(float(a[0]), 2) for a in audios] == [0.1, 0.2, 0.3]

        chamadas = [json.loads(l) for l in registro.read_text(encoding="utf-8").splitlines()]
        aquecimento, pedido = chamadas
        assert aquecimento["text"] == "Oi." and pedido["text"] == "Oi mundo"
        assert pedido["ref_audio"] == str(nivelada) and pedido["ref_text"] == "texto da gravação"  # clonou a cópia nivelada
        assert pedido["max"] == qwen_worker.passos_maximos("Oi mundo") < 200  # teto proporcional, não os 2048 do padrão
        audio, _ = sf.read(nivelada, dtype="float32")
        assert float(np.sqrt(np.mean(audio**2))) == pytest.approx(qwen_worker.NIVEL_DA_REFERENCIA, rel=0.05)
        assert list(temporaria.iterdir()) == []  # a cópia nivelada foi para o destino fixo, nada ficou na pasta temporária

        proc.stdin.close()  # o Vision morreu ou saiu
        assert proc.wait(timeout=15) == 0  # o processo acaba sozinho (não fica órfão ocupando a placa)
        erro = proc.stderr.read().decode("utf-8", "replace")
        for desviado in ("print perdido da biblioteca", "escrita nativa perdida", "lixo nativo entre os quadros"):
            assert desviado in erro  # foi tudo para o stderr, que o Vision guarda em log
    finally:
        if proc.poll() is None:
            proc.kill()


def test_passos_maximos_cresce_com_o_texto_e_corta_a_divagacao():
    assert qwen_worker.passos_maximos("") == qwen_worker.PASSOS_FIXOS
    assert qwen_worker.passos_maximos("a" * 100) == 100 * 2.5 + qwen_worker.PASSOS_FIXOS
    assert qwen_worker.passos_maximos("a" * 180) < 2048  # um trecho do XTTS/Qwen (até 180 letras) nunca chega perto do padrão
