"""Cliente HTTP da API do Orbit (app pessoal do Felipe no Render).

ATENÇÃO: em 30/09/2026 o Orbit estava SUSPENSO no Render (`x-render-routing: suspend`), então
o formato exato das respostas ainda não foi visto. Este cliente é tolerante: aceita lista pura ou
envelopes ({data: [...]}) e procura campos por vários nomes. `jarvis teste orbit` grava o formato
real em data/orbit-formato.json para ajustar os nomes em CAMPOS abaixo.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any

import httpx

URL_PADRAO = "https://orbit-fdzy.onrender.com"

ROTAS = {
    "login": "/auth/login",
    "transacoes": "/finances/transactions",
    "categorias": "/finances/categories",
    "orcamentos": "/finances/budgets",
    "tarefas": "/todos/tasks",
}

# Nomes candidatos de cada campo, do mais provável ao menos provável.
CAMPOS = {
    "token": ["token", "accessToken", "access_token", "jwt"],
    "valor": ["amount", "value", "valor", "total"],
    "descricao": ["description", "descricao", "title", "name", "note"],
    "categoria": ["category", "categoryName", "categoria", "categoryId"],
    "subcategoria": ["subcategory", "subcategoryName", "subcategoria", "subcategoryId"],
    "data": ["date", "data", "createdAt", "occurredAt"],
    "tipo": ["type", "kind", "tipo"],
    "orcado": ["planned", "budget", "amount", "limit", "value"],
    "titulo": ["title", "name", "text", "description"],
    "vencimento": ["dueDate", "due", "due_date", "vencimento"],
    "prioridade": ["priority", "prioridade"],
    "concluida": ["done", "completed", "isDone", "status"],
    "id": ["id", "_id", "uuid"],
}


class ErroOrbit(Exception):
    pass


def campo(obj: dict[str, Any], nome: str, padrao: Any = None) -> Any:
    for chave in CAMPOS[nome]:
        if chave in obj and obj[chave] not in (None, ""):
            v = obj[chave]
            if isinstance(v, dict):  # ex.: category: {id, name}
                return v.get("name") or v.get("nome") or v.get("id")
            return v
    return padrao


def lista(resposta: Any) -> list[dict[str, Any]]:
    if isinstance(resposta, list):
        return resposta
    if isinstance(resposta, dict):
        for chave in ("data", "items", "results", "transactions", "tasks", "categories", "budgets"):
            v = resposta.get(chave)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return lista(v)
    return []


def numero(v: Any) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


class OrbitAPI:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        email: str | None = None,
        senha: str | None = None,
        cliente: httpx.AsyncClient | None = None,
        timeout_s: float = 90,
    ):
        self.url = (url or os.getenv("ORBIT_URL") or URL_PADRAO).rstrip("/")
        self.token = token if token is not None else os.getenv("ORBIT_TOKEN") or None
        self.email = email if email is not None else os.getenv("ORBIT_EMAIL") or None
        self.senha = senha if senha is not None else os.getenv("ORBIT_PASSWORD") or None
        self.http = cliente or httpx.AsyncClient(base_url=self.url, timeout=timeout_s)
        self._trava = asyncio.Lock()

    async def fechar(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ baixo nível

    def _checar(self, r: httpx.Response) -> None:
        if r.headers.get("x-render-routing", "").startswith("suspend"):
            raise ErroOrbit("O Orbit está SUSPENSO no Render. O Felipe precisa reativar o serviço no painel do Render.")
        if r.status_code == 401:
            raise ErroOrbit("O login do Orbit venceu ou está errado (401). Confira ORBIT_TOKEN ou e-mail/senha no .env.")
        if r.status_code == 429:
            raise ErroOrbit("O Orbit pediu para ir mais devagar (429). Tente de novo em um minuto.")
        if r.status_code >= 500:
            raise ErroOrbit(f"O Orbit respondeu erro {r.status_code} (pode estar acordando no Render).")
        if r.status_code >= 400:
            raise ErroOrbit(f"O Orbit recusou a requisição ({r.status_code}): {r.text[:200]}")

    async def login(self) -> str:
        if not (self.email and self.senha):
            raise ErroOrbit("Faltam credenciais do Orbit: preencha ORBIT_TOKEN, ou ORBIT_EMAIL e ORBIT_PASSWORD, no .env.")
        r = await self.http.post(ROTAS["login"], json={"email": self.email, "password": self.senha})
        self._checar(r)
        dados = r.json()
        token = campo(dados, "token") or (campo(dados.get("data", {}), "token") if isinstance(dados.get("data"), dict) else None)
        if not token:
            raise ErroOrbit(f"Login do Orbit sem token na resposta (chaves: {sorted(dados)[:10]}).")
        self.token = token
        return token

    async def _req(self, metodo: str, rota: str, **kw: Any) -> Any:
        async with self._trava:
            if not self.token:
                await self.login()
        for tentativa in range(2):
            r = await self.http.request(metodo, rota, headers={"Authorization": f"Bearer {self.token}"}, **kw)
            if r.status_code == 401 and tentativa == 0 and self.email and self.senha:
                async with self._trava:
                    await self.login()
                continue
            if r.status_code == 429 and tentativa == 0:
                await asyncio.sleep(min(float(r.headers.get("retry-after", 5)), 15))
                continue
            self._checar(r)
            return r.json() if r.content else {}
        self._checar(r)
        return {}

    # ------------------------------------------------------------------ alto nível

    async def transacoes(self, mes: str) -> list[dict[str, Any]]:
        return lista(await self._req("GET", ROTAS["transacoes"], params={"month": mes}))

    async def orcamentos(self, mes: str) -> list[dict[str, Any]]:
        return lista(await self._req("GET", ROTAS["orcamentos"], params={"month": mes}))

    async def categorias(self) -> list[dict[str, Any]]:
        return lista(await self._req("GET", ROTAS["categorias"]))

    async def lancar(self, valor: float, descricao: str, categoria: str | None, dia: date, mes: str) -> Any:
        corpo: dict[str, Any] = {
            "amount": valor,
            "description": descricao,
            "date": dia.isoformat(),
            "month": mes,
            "type": "expense",
        }
        if categoria:
            corpo["category"] = categoria
        return await self._req("POST", ROTAS["transacoes"], json=corpo)

    async def tarefas(self) -> list[dict[str, Any]]:
        return lista(await self._req("GET", ROTAS["tarefas"]))

    async def criar_tarefa(self, titulo: str, vencimento: str | None, prioridade: str | None) -> Any:
        corpo: dict[str, Any] = {"title": titulo}
        if vencimento:
            corpo["dueDate"] = vencimento
        if prioridade:
            corpo["priority"] = prioridade
        return await self._req("POST", ROTAS["tarefas"], json=corpo)
