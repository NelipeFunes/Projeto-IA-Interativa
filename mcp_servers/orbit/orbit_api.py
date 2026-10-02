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
import time
import unicodedata
from datetime import date
from pathlib import Path
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


# Palavras que não distinguem uma tarefa de outra ("pagar o IPVA" = "Pagar IPVA").
_VAZIAS = {"os", "as", "de", "do", "da", "dos", "das", "um", "uma", "no", "na", "pra", "para", "tarefa"}
# "a", "e" e "o" só são artigo no meio: no fim são rótulo ("Treino A" e "Treino E" não são a mesma tarefa).
_VAZIAS_NO_MEIO = {"a", "e", "o"}


def _palavras(texto: str) -> set[str]:
    todas = _simples(texto).split()
    return {p for i, p in enumerate(todas)
            if p not in _VAZIAS and not (p in _VAZIAS_NO_MEIO and i < len(todas) - 1)}


def feita(t: dict[str, Any]) -> bool:
    return campo(t, "concluida") in (True, "done", "completed")


def achar_tarefa(tarefas: list[dict[str, Any]], busca: str, estado: str = "aberta") -> dict[str, Any]:
    """A tarefa que o Felipe disse. `estado`: "aberta" (concluir), "feita" (reabrir) ou "qualquer" (editar/apagar:
    as abertas primeiro).

    Revisão do PR 40: sem aproximação. Todas as palavras ditas precisam estar no título, e só uma tarefa pode
    servir: "Pagar IPVA 2025" não acha "Pagar IPVA 2026", e "IPVA do carro" não acha "Carro". Ambígua ou
    ausente: ErroOrbit com as opções (as parecidas viram sugestão), e nada muda."""
    alvo = _palavras(busca)
    if not alvo:
        raise ErroOrbit("Qual tarefa? Diga o título.")
    abertas = [t for t in tarefas if not feita(t)]
    feitas = [t for t in tarefas if feita(t)]
    grupos = {"aberta": [abertas], "feita": [feitas], "qualquer": [abertas, feitas]}[estado]
    for grupo in grupos:
        exatas = [t for t in grupo if _palavras(campo(t, "titulo", "")) == alvo]
        servem = exatas or [t for t in grupo if alvo <= _palavras(campo(t, "titulo", ""))]
        if len(servem) == 1:
            return servem[0]
        if len(servem) > 1:
            nomes = "; ".join(str(campo(t, "titulo", "?")) for t in servem[:5])
            raise ErroOrbit(f"Mais de uma tarefa serve para '{busca}': {nomes}. Qual delas?")
    onde = [t for g in grupos for t in g]
    parecidas = [str(campo(t, "titulo", "?")) for t in onde
                 if difflib.SequenceMatcher(None, " ".join(sorted(alvo)),
                                            " ".join(sorted(_palavras(campo(t, "titulo", ""))))).ratio() >= 0.6]
    qual = {"aberta": " aberta", "feita": " já feita", "qualquer": ""}[estado]
    sugestao = f" Parecidas: {'; '.join(parecidas[:3])}." if parecidas else ""
    raise ErroOrbit(f"Não achei uma tarefa{qual} '{busca}' no Orbit.{sugestao}")


def _id_seguro(id_tarefa: Any) -> str:
    """O id vai na URL: vazio, None, "." e ".." mudariam a rota (/todos/tasks/.. vira /todos)."""
    texto = "" if id_tarefa is None else str(id_tarefa).strip()
    if texto in ("", ".", "..", "None"):
        raise ErroOrbit("Tarefa sem id válido no Orbit.")
    return quote(texto, safe="")


ARQUIVO_ENV = Path(__file__).resolve().parents[2] / ".env"
ESPERA_DEPOIS_DE_FALHA_S = 600  # login recusado ou pedindo código: não tenta de novo a cada fala


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
        env_arquivo: Path | None = None,
    ):
        self.url = (url or os.getenv("ORBIT_URL") or URL_PADRAO).rstrip("/")
        self.token = token if token is not None else os.getenv("ORBIT_TOKEN") or None
        self.email = email if email is not None else os.getenv("ORBIT_EMAIL") or None
        self.senha = senha if senha is not None else os.getenv("ORBIT_PASSWORD") or None
        self.http = cliente or httpx.AsyncClient(base_url=self.url, timeout=timeout_s)
        self._trava = asyncio.Lock()
        # Revisão do PR 40: com o token vencido e o Orbit pedindo código, cada fala disparava um login (e um e-mail
        # de código novo, que podia invalidar o que o Felipe acabou de pedir pela tela). Falhou: espera 10 min.
        self._sem_login_ate = 0.0
        self._motivo_sem_login = ""
        self._ler_env = env_arquivo  # a tela de Conexões grava um token novo no .env com o servidor já rodando

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

    def _token_novo_do_env(self) -> bool:
        """Relê o .env: a tela pode ter gravado um login novo depois que este servidor subiu."""
        if self._ler_env is None:
            return False
        try:
            from dotenv import dotenv_values

            env = dotenv_values(self._ler_env, interpolate=False)
        except OSError:
            return False
        token = (env.get("ORBIT_TOKEN") or "").strip()
        if token and token != self.token:
            self.token = token
            self.email = (env.get("ORBIT_EMAIL") or self.email or "").strip() or None
            self.senha = env.get("ORBIT_PASSWORD") or self.senha
            self._sem_login_ate = 0.0
            return True
        return False

    async def _relogar(self) -> None:
        """Login de novo depois de um 401, com freio: uma falha vale por ESPERA_DEPOIS_DE_FALHA_S."""
        if self._token_novo_do_env():
            return
        if time.monotonic() < self._sem_login_ate:
            raise ErroOrbit(self._motivo_sem_login)
        try:
            await self.login()
        except ErroOrbit as e:
            self._sem_login_ate = time.monotonic() + ESPERA_DEPOIS_DE_FALHA_S
            self._motivo_sem_login = str(e)
            raise

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
                self._token_novo_do_env()
            if not self.token:
                await self._relogar()
        for tentativa in range(2):
            r = await self.http.request(metodo, rota, headers={"Authorization": f"Bearer {self.token}"}, **kw)
            if r.status_code == 401 and tentativa == 0 and (self.email and self.senha or self._ler_env):
                async with self._trava:
                    await self._relogar()
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
        return await self._req("PATCH", f"{ROTAS['tarefas']}/{_id_seguro(id_tarefa)}", json=campos)

    async def apagar_tarefa(self, id_tarefa: Any) -> Any:
        return await self._req("DELETE", f"{ROTAS['tarefas']}/{_id_seguro(id_tarefa)}")
