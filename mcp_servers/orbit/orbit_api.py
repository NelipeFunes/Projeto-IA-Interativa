"""Cliente HTTP da API do Orbit (app pessoal do Felipe no Render).

Em 30/09/2026 o Orbit estava SUSPENSO no Render, e o formato das respostas foi escrito às cegas: este cliente é
tolerante (lista pura ou envelope {data: [...]}, campos por vários nomes). Em 02/10, com ele de volta, as rotas e
os campos das tarefas foram conferidos no código do front: `/todos/tasks` (GET, POST), `/todos/tasks/{id}` (PATCH,
DELETE), com `title`, `priority` (high/medium/low), `due` (não `dueDate`: a data das tarefas criadas se perdia) e
`done`. O login devolve `accessToken` ou, quando o Orbit quer confirmar por e-mail, `challengeToken` (e o código
vai por `/auth/code/verify`). Não há refresh token: o accessToken vencido pede login de novo.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import unicodedata
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

URL_PADRAO = "https://orbit-fdzy.onrender.com"

ROTAS = {
    "login": "/auth/login",
    "codigo": "/auth/code/verify",
    "transacoes": "/finances/transactions",
    "categorias": "/finances/categories",
    "orcamentos": "/finances/budgets",
    "tarefas": "/todos/tasks",
}

# Nomes candidatos de cada campo, do mais provável ao menos provável.
CAMPOS = {
    "token": ["accessToken", "token", "access_token", "jwt"],
    "valor": ["amount", "value", "valor", "total"],
    "descricao": ["description", "descricao", "title", "name", "note"],
    "categoria": ["category", "categoryName", "categoria", "categoryId"],
    "subcategoria": ["subcategory", "subcategoryName", "subcategoria", "subcategoryId"],
    "data": ["date", "data", "createdAt", "occurredAt"],
    "tipo": ["type", "kind", "tipo"],
    "orcado": ["planned", "budget", "amount", "limit", "value"],
    "titulo": ["title", "name", "text", "description"],
    "vencimento": ["due", "dueDate", "due_date", "vencimento"],
    "prioridade": ["priority", "prioridade"],
    "concluida": ["done", "completed", "isDone", "status"],
    "id": ["id", "_id", "uuid"],
}


PRIORIDADES = {"baixa": "low", "media": "medium", "média": "medium", "alta": "high"}
NOME_PRIORIDADE = {"low": "baixa", "medium": "média", "high": "alta"}


class ErroOrbit(Exception):
    pass


class PrecisaCodigo(ErroOrbit):
    """O Orbit mandou um código para o e-mail e só entrega o acesso com ele (tela de Conexões)."""

    def __init__(self, desafio: str, email_mascarado: str):
        super().__init__("O Orbit pediu o código que mandou para o seu e-mail: conecte em Ajustes → Conexões.")
        self.desafio = desafio
        self.email_mascarado = email_mascarado


def _simples(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto).lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c) and (c.isalnum() or c.isspace())).split())


def achar_tarefa(tarefas: list[dict[str, Any]], busca: str) -> dict[str, Any]:
    """A tarefa que o Felipe disse ("pagar o IPVA" acha "Pagar IPVA"). Ambígua ou ausente: ErroOrbit com as opções,
    para o modelo perguntar em vez de mexer na tarefa errada."""
    alvo = _simples(busca)
    if not alvo:
        raise ErroOrbit("Qual tarefa? Diga o título.")
    titulos = [(t, _simples(campo(t, "titulo", ""))) for t in tarefas]
    exatas = [t for t, s in titulos if s == alvo]
    if len(exatas) == 1:
        return exatas[0]
    contem = [t for t, s in titulos if alvo in s or (s and s in alvo)]
    if len(contem) == 1:
        return contem[0]
    candidatas = contem or [t for t, s in titulos if difflib.SequenceMatcher(None, alvo, s).ratio() >= 0.75]
    if len(candidatas) == 1:
        return candidatas[0]
    if not candidatas:
        raise ErroOrbit(f"Não achei a tarefa '{busca}' no Orbit.")
    nomes = "; ".join(str(campo(t, "titulo", "?")) for t in candidatas[:5])
    raise ErroOrbit(f"Mais de uma tarefa parece com '{busca}': {nomes}. Qual delas?")


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
            raise ErroOrbit("O login do Orbit venceu ou está errado (401): reconecte em Ajustes → Conexões.")
        if r.status_code == 429:
            raise ErroOrbit("O Orbit pediu para ir mais devagar (429). Tente de novo em um minuto.")
        if r.status_code >= 500:
            raise ErroOrbit(f"O Orbit respondeu erro {r.status_code} (pode estar acordando no Render).")
        if r.status_code >= 400:
            raise ErroOrbit(f"O Orbit recusou a requisição ({r.status_code}): {r.text[:200]}")

    async def login(self) -> str:
        """O accessToken; PrecisaCodigo se o Orbit mandou um código para o e-mail."""
        if not (self.email and self.senha):
            raise ErroOrbit("O Orbit está sem login: conecte em Ajustes → Conexões.")
        r = await self.http.post(ROTAS["login"], json={"email": self.email, "password": self.senha})
        if r.status_code in (400, 401, 403) and not r.headers.get("x-render-routing", "").startswith("suspend"):
            raise ErroOrbit("O Orbit recusou o e-mail ou a senha: confira em Ajustes → Conexões.")
        self._checar(r)
        return self._token_da_resposta(r.json())

    async def confirmar_codigo(self, desafio: str, codigo: str) -> str:
        r = await self.http.post(ROTAS["codigo"], json={"challengeToken": desafio, "code": codigo})
        if r.status_code in (400, 401, 403):
            raise ErroOrbit("O Orbit não aceitou o código (errado ou vencido). Confira o e-mail e tente de novo.")
        self._checar(r)
        return self._token_da_resposta(r.json())

    def _token_da_resposta(self, dados: Any) -> str:
        if not isinstance(dados, dict):
            raise ErroOrbit("Login do Orbit com resposta inesperada.")
        if dados.get("challengeToken") and not campo(dados, "token"):
            raise PrecisaCodigo(str(dados["challengeToken"]), str(dados.get("maskedEmail") or "o seu e-mail"))
        token = campo(dados, "token") or (campo(dados["data"], "token") if isinstance(dados.get("data"), dict) else None)
        if not token:
            raise ErroOrbit(f"Login do Orbit sem token na resposta (chaves: {sorted(dados)[:10]}).")
        self.token = str(token)
        return self.token

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
        corpo: dict[str, Any] = {"title": titulo, "priority": prioridade or "medium", "done": False}
        if vencimento:
            corpo["due"] = vencimento
        return await self._req("POST", ROTAS["tarefas"], json=corpo)

    async def mudar_tarefa(self, id_tarefa: Any, campos: dict[str, Any]) -> Any:
        return await self._req("PATCH", f"{ROTAS['tarefas']}/{quote(str(id_tarefa), safe='')}", json=campos)

    async def apagar_tarefa(self, id_tarefa: Any) -> Any:
        return await self._req("DELETE", f"{ROTAS['tarefas']}/{quote(str(id_tarefa), safe='')}")
