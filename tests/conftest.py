from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "tests"))

from fakes.calendario_falso import criar_servidor  # noqa: E402

from vision import config, tempo  # noqa: E402
from vision.memory.store import Memorias  # noqa: E402
from vision.tools.mcp_host import ConexaoMCP, HostMCP  # noqa: E402


class EmbedderFalso:
    """Bag-of-words com hashing: parecido o bastante para testar a lógica da memória sem Ollama."""

    async def __call__(self, textos: list[str]) -> np.ndarray:
        out = np.zeros((len(textos), 256), dtype=np.float32)
        for i, t in enumerate(textos):
            t = t.split(":", 2)[-1].lower()
            for palavra in "".join(c if c.isalnum() else " " for c in t).split():
                out[i, hash(palavra) % 256] += 1
        return out / np.linalg.norm(out, axis=1, keepdims=True).clip(min=1e-9)


@pytest.fixture
def cfg(tmp_path):
    c = config.carregar()
    c.dados = tmp_path / "data"
    c.dados.mkdir()
    return c


@pytest.fixture
def servidor_agenda():
    return criar_servidor(tempo.agora().date())


@pytest.fixture
async def host(servidor_agenda):
    h = HostMCP({"google-calendar": ConexaoMCP("google-calendar", servidor_agenda, 10)})
    async with h:
        yield h


@pytest.fixture
def memorias(tmp_path):
    m = Memorias(tmp_path / "mem.db", EmbedderFalso(), duplicata=0.9)
    yield m
    m.fechar()
