"""Memória de longo prazo: SQLite + embeddings do Ollama.

É o jeito de "alimentar o Vision aos poucos": cada fato vira uma linha que você pode abrir,
editar ou apagar (data/memoria.db). A busca é por similaridade de cosseno em numpy, que para
algumas milhares de memórias é instantânea.

Cada fato pode dizer em que assuntos ele importa (`vale_para`). "Tem um carro" não se parece com "casas de
aluguel"; mas "aluguel de casa: o Felipe tem um carro" se parece. Cada assunto vira um vetor a mais, e o fato
vale pelo vetor mais parecido com a pergunta. Calibrado em 02/10 com o embeddinggemma: com os assuntos, os
pedidos que importam ficaram em 0,35–0,53 e os outros em até 0,21 (sem eles, a casa dava 0,17).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from vision import tempo

# Prefixos recomendados pelo EmbeddingGemma para busca assimétrica.
PREFIXO_CONSULTA = "task: search result | query: "
PREFIXO_DOCUMENTO = "title: none | text: "
CONSULTAS_EM_CACHE = 128
MAX_ASSUNTOS = 12


def assuntos(vale_para: str) -> list[str]:
    """"moradia, vaga de garagem; oficina" → ["moradia", "vaga de garagem", "oficina"] (sem repetir)."""
    vistos: dict[str, None] = {}
    for item in re.split(r"[,;\n]", vale_para):
        item = " ".join(item.split()).strip(" .")
        if item and item.lower() not in {v.lower() for v in vistos}:
            vistos[item] = None
    return list(vistos)[:MAX_ASSUNTOS]


def documentos(texto: str, vale_para: str = "") -> list[str]:
    """O que vira embedding: o fato sozinho e, para cada assunto, "assunto: fato"."""
    return [texto, *(f"{a}: {texto}" for a in assuntos(vale_para))]


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
    vale_para: str = ""


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
        colunas = {r[1] for r in self._db.execute("PRAGMA table_info(memorias)")}
        if "vale_para" not in colunas:  # banco de antes de 02/10
            self._db.execute("ALTER TABLE memorias ADD COLUMN vale_para TEXT NOT NULL DEFAULT ''")
        if "embedding_assuntos" not in colunas:  # um vetor por assunto, empilhados (NULL = sem assuntos)
            self._db.execute("ALTER TABLE memorias ADD COLUMN embedding_assuntos BLOB")
        self._db.commit()
        self._consultas: OrderedDict[str, np.ndarray] = OrderedDict()
        self._ids: np.ndarray = np.zeros(0, dtype=np.int64)
        self._matriz: np.ndarray | None = None  # um vetor por fato (o fato puro): busca e duplicatas
        self._assuntos: np.ndarray | None = None  # vetores "assunto: fato" de todos os fatos
        self._dono: np.ndarray = np.zeros(0, dtype=np.int64)  # para cada linha de _assuntos, a linha do fato
        self._recarregar()

    def _recarregar(self) -> None:
        linhas = self._db.execute("SELECT id, embedding, embedding_assuntos FROM memorias ORDER BY id").fetchall()
        if not linhas:
            self._ids, self._matriz, self._assuntos = np.zeros(0, dtype=np.int64), None, None
            self._dono = np.zeros(0, dtype=np.int64)
            return
        self._ids = np.array([r[0] for r in linhas], dtype=np.int64)
        self._matriz = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in linhas])
        dim = self._matriz.shape[1]
        blocos, donos = [], []
        for i, r in enumerate(linhas):
            if r[2]:
                bloco = np.frombuffer(r[2], dtype=np.float32).reshape(-1, dim)
                blocos.append(bloco)
                donos.extend([i] * len(bloco))
        self._assuntos = np.vstack(blocos) if blocos else None
        self._dono = np.array(donos, dtype=np.int64)

    def _por_id(self, ids: list[int]) -> dict[int, tuple[str, str, str, str]]:
        if not ids:
            return {}
        q = ",".join("?" * len(ids))
        rows = self._db.execute(
            f"SELECT id, texto, categoria, criado_em, vale_para FROM memorias WHERE id IN ({q})", ids)
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}

    def total(self) -> int:
        return len(self._ids)

    def todas(self) -> list[Memoria]:
        rows = self._db.execute(
            "SELECT id, texto, categoria, criado_em, vale_para FROM memorias ORDER BY id").fetchall()
        return [Memoria(r[0], r[1], r[2], r[3], vale_para=r[4]) for r in rows]

    async def _consulta(self, texto: str) -> np.ndarray:
        # Cada pergunta passa por aqui (o prompt busca memórias a cada turno): a repetida não vai ao Ollama.
        chave = " ".join(texto.lower().split())
        if (v := self._consultas.get(chave)) is not None:
            self._consultas.move_to_end(chave)
            return v
        v = (await self.embedder([PREFIXO_CONSULTA + texto]))[0]
        self._consultas[chave] = v
        if len(self._consultas) > CONSULTAS_EM_CACHE:
            self._consultas.popitem(last=False)
        return v

    async def _documentos(self, texto: str, vale_para: str) -> tuple[np.ndarray, np.ndarray | None]:
        """(vetor do fato, vetores dos assuntos ou None), numa chamada só ao Ollama."""
        v = await self.embedder([PREFIXO_DOCUMENTO + d for d in documentos(texto, vale_para)])
        return v[0], (v[1:] if len(v) > 1 else None)

    async def buscar(self, consulta: str, k: int = 4, minimo: float = 0.0) -> list[Memoria]:
        if self._matriz is None or not consulta.strip():
            return []
        q = await self._consulta(consulta)
        sims = self._matriz @ q
        if self._assuntos is not None:
            np.maximum.at(sims, self._dono, self._assuntos @ q)  # cada fato vale pelo vetor mais parecido
        ordem = np.argsort(-sims)[:k]
        escolhidos = [(int(self._ids[i]), float(sims[i])) for i in ordem if sims[i] >= minimo]
        dados = self._por_id([i for i, _ in escolhidos])
        return [Memoria(i, t, c, q_, similaridade=s, vale_para=vp)
                for i, s in escolhidos if i in dados for t, c, q_, vp in [dados[i]]]

    async def lembrar(self, texto: str, categoria: str = "geral", vale_para: str = "") -> tuple[str, Memoria]:
        """Guarda um fato. Se já existir um quase igual, atualiza em vez de duplicar.

        `vale_para`: assuntos em que o fato muda a resposta ("moradia, vaga de garagem"), separados por vírgula.
        Entram na busca, não no texto que o modelo lê. Ao atualizar sem assuntos, ficam os antigos."""
        texto, vale_para = texto.strip(), ", ".join(assuntos(vale_para))
        agora = tempo.agora().isoformat(timespec="seconds")
        async with self._lock:
            mid = None
            if self._matriz is not None:
                fato = (await self.embedder([PREFIXO_DOCUMENTO + texto]))[0]
                sims = self._matriz @ fato  # duplicata pelo fato puro: outra dica não faz dele outro fato
                i = int(np.argmax(sims))
                if sims[i] >= self.duplicata:
                    mid, sim = int(self._ids[i]), float(sims[i])
                    if not vale_para:
                        vale_para = self._db.execute("SELECT vale_para FROM memorias WHERE id=?", (mid,)).fetchone()[0]
            fato, vetores = await self._documentos(texto, vale_para)
            blob = vetores.astype(np.float32).tobytes() if vetores is not None else None
            if mid is not None:
                self._db.execute(
                    "UPDATE memorias SET texto=?, categoria=?, atualizado_em=?, embedding=?, embedding_assuntos=?, "
                    "vale_para=? WHERE id=?",
                    (texto, categoria, agora, fato.tobytes(), blob, vale_para, mid),
                )
                self._db.commit()
                self._recarregar()
                return "atualizada", Memoria(mid, texto, categoria, agora, sim, vale_para)
            cur = self._db.execute(
                "INSERT INTO memorias (texto, categoria, criado_em, atualizado_em, embedding, embedding_assuntos, "
                "vale_para) VALUES (?,?,?,?,?,?,?)",
                (texto, categoria, agora, agora, fato.tobytes(), blob, vale_para),
            )
            self._db.commit()
            self._recarregar()
            return "nova", Memoria(int(cur.lastrowid or 0), texto, categoria, agora, vale_para=vale_para)

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
        """Com os assuntos à vista: só "tem um carro" no prompt, o 4B não pensava em pedir garagem (02/10)."""
        return "\n".join(f"- [{m.id}] {m.texto}" + (f" (pesa em: {m.vale_para})" if m.vale_para else "")
                         for m in memorias)

    def exportar(self) -> list[dict[str, Any]]:
        return [m.__dict__ for m in self.todas()]
