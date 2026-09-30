"""Memória de longo prazo: SQLite + embeddings do Ollama.

É o jeito de "alimentar o Vision aos poucos": cada fato vira uma linha que você pode abrir,
editar ou apagar (data/memoria.db). A busca é por similaridade de cosseno em numpy, que para
algumas milhares de memórias é instantânea.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from vision import tempo

# Prefixos recomendados pelo EmbeddingGemma para busca assimétrica.
PREFIXO_CONSULTA = "task: search result | query: "
PREFIXO_DOCUMENTO = "title: none | text: "


class Embedder(Protocol):
    async def __call__(self, textos: list[str]) -> np.ndarray: ...


class EmbedderOllama:
    def __init__(self, modelo: str, host: str, na_cpu: bool = True):
        import ollama

        self.modelo = modelo
        self.cliente = ollama.AsyncClient(host=host)
        self.opcoes = {"num_gpu": 0} if na_cpu else {}

    async def __call__(self, textos: list[str]) -> np.ndarray:
        r = await self.cliente.embed(model=self.modelo, input=textos, options=self.opcoes, keep_alive="30m")
        v = np.asarray(r.embeddings, dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True).clip(min=1e-9)


@dataclass
class Memoria:
    id: int
    texto: str
    categoria: str
    criado_em: str
    similaridade: float = 0.0


class Memorias:
    def __init__(self, arquivo: Path, embedder: Embedder, duplicata: float = 0.90):
        self.arquivo = arquivo
        self.embedder = embedder
        self.duplicata = duplicata
        self._lock = asyncio.Lock()
        self._db = sqlite3.connect(arquivo, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS memorias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                categoria TEXT NOT NULL DEFAULT 'geral',
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL,
                embedding BLOB NOT NULL
            )"""
        )
        self._db.commit()
        self._ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self._matriz: np.ndarray | None = None
        self._recarregar()

    def _recarregar(self) -> None:
        linhas = self._db.execute("SELECT id, embedding FROM memorias ORDER BY id").fetchall()
        if not linhas:
            self._ids, self._matriz = np.zeros(0, dtype=np.int64), None
            return
        self._ids = np.array([r[0] for r in linhas], dtype=np.int64)
        self._matriz = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in linhas])

    def _por_id(self, ids: list[int]) -> dict[int, tuple[str, str, str]]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = self._db.execute(f"SELECT id, texto, categoria, criado_em FROM memorias WHERE id IN ({q})", ids)
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def total(self) -> int:
        return len(self._ids)

    def todas(self) -> list[Memoria]:
        rows = self._db.execute("SELECT id, texto, categoria, criado_em FROM memorias ORDER BY id").fetchall()
        return [Memoria(*r) for r in rows]

    async def _vetor(self, texto: str, consulta: bool) -> np.ndarray:
        prefixo = PREFIXO_CONSULTA if consulta else PREFIXO_DOCUMENTO
        return (await self.embedder([prefixo + texto]))[0]

    async def buscar(self, consulta: str, k: int = 4, minimo: float = 0.0) -> list[Memoria]:
        if self._matriz is None or not consulta.strip():
            return []
        q = await self._vetor(consulta, consulta=True)
        sims = self._matriz @ q
        ordem = np.argsort(-sims)[:k]
        escolhidos = [(int(self._ids[i]), float(sims[i])) for i in ordem if sims[i] >= minimo]
        dados = self._por_id([i for i, _ in escolhidos])
        return [Memoria(i, *dados[i], similaridade=s) for i, s in escolhidos if i in dados]

    async def lembrar(self, texto: str, categoria: str = "geral") -> tuple[str, Memoria]:
        """Guarda um fato. Se já existir um quase igual, atualiza em vez de duplicar."""
        texto = texto.strip()
        v = await self._vetor(texto, consulta=False)
        agora = tempo.agora().isoformat(timespec="seconds")
        async with self._lock:
            if self._matriz is not None:
                sims = self._matriz @ v
                i = int(np.argmax(sims))
                if sims[i] >= self.duplicata:
                    mid = int(self._ids[i])
                    self._db.execute(
                        "UPDATE memorias SET texto=?, categoria=?, atualizado_em=?, embedding=? WHERE id=?",
                        (texto, categoria, agora, v.tobytes(), mid),
                    )
                    self._db.commit()
                    self._recarregar()
                    return "atualizada", Memoria(mid, texto, categoria, agora, float(sims[i]))
            cur = self._db.execute(
                "INSERT INTO memorias (texto, categoria, criado_em, atualizado_em, embedding) VALUES (?,?,?,?,?)",
                (texto, categoria, agora, agora, v.tobytes()),
            )
            self._db.commit()
            self._recarregar()
            return "nova", Memoria(int(cur.lastrowid or 0), texto, categoria, agora)

    async def esquecer(self, memoria_id: int) -> bool:
        async with self._lock:
            cur = self._db.execute("DELETE FROM memorias WHERE id=?", (memoria_id,))
            self._db.commit()
            self._recarregar()
            return cur.rowcount > 0

    def fechar(self) -> None:
        self._db.close()

    # Usado pelo agente para montar o prompt e pelas ferramentas.
    @staticmethod
    def formatar(memorias: list[Memoria]) -> str:
        return "\n".join(f"- [{m.id}] {m.texto}" for m in memorias)

    def exportar(self) -> list[dict[str, Any]]:
        return [m.__dict__ for m in self.todas()]
