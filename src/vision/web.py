"""Busca na web: Tavily (API feita para IA, 1.000 créditos grátis por mês) e DuckDuckGo de reserva.

A chave da Tavily fica em `TAVILY_API_KEY` no `.env` (fora do git). Sem chave, com a cota estourada ou com a
Tavily fora do ar, a busca cai no DuckDuckGo (sem conta; devolve só trechos curtos).

Cache em `data/cache/`: a mesma busca dentro de algumas horas não gasta crédito de novo. Pode apagar a pasta
quando quiser.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from vision import tempo

log = logging.getLogger(__name__)

TAVILY = "https://api.tavily.com"
# 401 chave errada; 429 rápido demais; 432/433 cota do plano estourada.
COTA_ESTOURADA = {429, 432, 433}
MAX_PAGINA = 1_500_000  # bytes baixados de uma página no máximo (o texto útil cabe com folga)
MAX_HTML = 400_000  # caracteres de HTML analisados
LINKS_LEMBRADOS = 300
# O que nunca sai do PC dentro de uma consulta: e-mail, telefone, CPF (a regra do prompt é a 1ª barreira).
# Telefone e CPF só com a pontuação de sempre: "2005 2010 2015" ou um código de 11 dígitos passam intactos.
DADO_PESSOAL = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"
    r"|(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"
    r"|(?<![\d)])(?:\+?55\s?)?\(\d{2}\)\s?9?\d{4}-?\d{4}(?!\d)"
    r"|(?<![\d-])9\d{4}-\d{4}(?![\d-])"  # sem DDD, só celular: "1500-2000" é faixa, não telefone
)


class ErroWeb(Exception):
    pass


@dataclass
class Resultado:
    titulo: str
    url: str
    trecho: str

    @property
    def site(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")


@dataclass
class Busca:
    consulta: str
    resposta: str = ""  # resumo pronto da Tavily (vazio no DuckDuckGo)
    resultados: list[Resultado] = field(default_factory=list)
    fonte: str = "tavily"
    do_cache: bool = False


def normalizar(consulta: str) -> str:
    return " ".join(consulta.lower().split())


def url_valida(url: str) -> bool:
    p = urlparse(url.strip())
    return p.scheme in ("http", "https") and bool(p.hostname)


def _host_publico(url: str) -> bool:
    """O `web_ler` baixa o que o modelo pedir, e o link pode ter vindo de uma página: nunca abre a rede de casa
    nem o próprio PC (o servidor do Vision, o roteador). Confere o nome e cada IP para onde ele aponta."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host or "." not in host or host.endswith((".local", ".localhost", ".lan", ".home", ".internal")):
        return False
    try:
        enderecos = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return False
    try:
        return bool(enderecos) and all(ipaddress.ip_address(e.split("%")[0]).is_global for e in enderecos)
    except ValueError:
        return False


class CacheBuscas:
    """Buscas recentes num JSON pequeno. Arquivo corrompido ou apagado = cache vazio, nunca erro."""

    def __init__(self, arquivo: Path, validade_h: float = 6, maximo: int = 200):
        self.arquivo = arquivo
        self.validade_s = validade_h * 3600
        self.maximo = maximo
        self._itens: dict[str, dict[str, Any]] = {}
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            if isinstance(dados, dict):
                self._itens = {k: v for k, v in dados.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def pegar(self, chave: str, agora: float | None = None) -> dict[str, Any] | None:
        item = self._itens.get(chave)
        agora = time.time() if agora is None else agora
        if not item or agora - float(item.get("quando", 0)) > self.validade_s:
            return None
        return item.get("valor")

    def guardar(self, chave: str, valor: dict[str, Any], agora: float | None = None) -> None:
        self._itens[chave] = {"quando": time.time() if agora is None else agora, "valor": valor}
        if len(self._itens) > self.maximo:  # tira os mais velhos
            for velha in sorted(self._itens, key=lambda k: self._itens[k]["quando"])[: len(self._itens) - self.maximo]:
                del self._itens[velha]
        self._salvar()

    def _salvar(self) -> None:
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.arquivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._itens, ensure_ascii=False), encoding="utf-8", newline="")
            os.replace(tmp, self.arquivo)
        except OSError as e:
            log.warning("não deu para gravar o cache da web: %s", e)


class Cota:
    """Créditos da Tavily gastos no mês (a conta grátis tem 1.000). Conta local, só para avisar antes de acabar."""

    def __init__(self, arquivo: Path, limite: int = 1000, avisar_em: int = 900):
        self.arquivo = arquivo
        self.limite = limite
        self.avisar_em = avisar_em

    def _ler(self) -> dict[str, Any]:
        try:
            dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dados = {}
        mes = tempo.agora().strftime("%Y-%m")
        if not isinstance(dados, dict) or dados.get("mes") != mes:
            dados = {"mes": mes, "creditos": 0}
        return dados

    def gastos(self) -> int:
        return int(self._ler().get("creditos", 0))

    def gastar(self, creditos: int) -> int:
        dados = self._ler()
        dados["creditos"] = int(dados.get("creditos", 0)) + creditos
        try:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            self.arquivo.write_text(json.dumps(dados), encoding="utf-8", newline="")
        except OSError as e:
            log.warning("não deu para gravar a cota da web: %s", e)
        return dados["creditos"]

    def aviso(self) -> str:
        g = self.gastos()
        if g >= self.avisar_em:
            return f" (Aviso: {g} de {self.limite} buscas da Tavily já usadas neste mês.)"
        return ""


def limpar_consulta(consulta: str) -> str:
    return " ".join(DADO_PESSOAL.sub(" ", consulta).split())


class _Extrator(HTMLParser):
    """HTML → texto numa passada só (sem regex com retrocesso: página hostil não trava o Vision)."""

    PULAR = {"script", "style", "noscript", "svg", "template", "iframe"}
    BLOCO = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.pulando = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.PULAR:
            self.pulando += 1
        elif tag in self.BLOCO:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.PULAR:
            self.pulando = max(0, self.pulando - 1)
        elif tag in self.BLOCO:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.pulando:
            self.partes.append(data)


def _texto_de_html(pagina: str) -> str:
    """HTML → texto, sem dependência: tira script/style/nav e as tags. Serve para a reserva do `ler`."""
    extrator = _Extrator()
    extrator.feed(pagina[:MAX_HTML])
    extrator.close()
    linhas = [" ".join(linha.split()) for linha in "".join(extrator.partes).splitlines()]
    return "\n".join(linha for linha in linhas if len(linha) > 1)


class Web:
    def __init__(self, pasta_cache: Path, chave: str | None = None, *, validade_h: float = 6,
                 max_resultados: int = 5, pais: str = "brazil", limite_cota: int = 1000,
                 transporte: httpx.AsyncBaseTransport | None = None, buscar_ddg=None):
        self.chave = (chave if chave is not None else os.environ.get("TAVILY_API_KEY", "")).strip()
        self.cache = CacheBuscas(pasta_cache / "web.json", validade_h)
        self.cota = Cota(pasta_cache / "web-cota.json", limite_cota, int(limite_cota * 0.9))
        self.max_resultados = max_resultados
        self.pais = pais
        self._transporte = transporte
        self._buscar_ddg = buscar_ddg or _ddg  # trocável nos testes
        self._publico = _host_publico
        # Só se lê página que veio de uma busca: um texto de página não consegue mandar o Vision abrir
        # "site-do-atacante/?dados=<memórias>" (revisão do PR 23).
        self._links: OrderedDict[str, None] = OrderedDict()

    def _lembrar_links(self, resultados: list[Resultado]) -> None:
        for r in resultados:
            self._links[r.url] = None
            self._links.move_to_end(r.url)
        while len(self._links) > LINKS_LEMBRADOS:
            self._links.popitem(last=False)

    def _cliente(self) -> httpx.AsyncClient:
        # Sem proxy do sistema: com ele, o IP da conexão seria o do proxy e a trava de rede local não valeria.
        return httpx.AsyncClient(timeout=15, transport=self._transporte, trust_env=False,
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Vision"})

    async def buscar(self, consulta: str) -> Busca:
        consulta = limpar_consulta(consulta)
        if not consulta:
            raise ErroWeb("Diga o que procurar.")
        chave = "busca:" + normalizar(consulta)
        guardada = self.cache.pegar(chave)
        if guardada:
            b = Busca(consulta, guardada.get("resposta", ""),
                      [Resultado(**r) for r in guardada.get("resultados", [])], guardada.get("fonte", "?"), True)
            self._lembrar_links(b.resultados)
            return b
        busca = None
        if self.chave:
            try:
                busca = await self._tavily(consulta)
            except ErroWeb as e:
                log.warning("Tavily falhou (%s); buscando no DuckDuckGo", e)
        if busca is None:
            busca = await self._duckduckgo(consulta)
        self._lembrar_links(busca.resultados)
        if busca.resultados or busca.resposta:
            self.cache.guardar(chave, {"resposta": busca.resposta, "fonte": busca.fonte,
                                       "resultados": [asdict(r) for r in busca.resultados]})
        return busca

    async def _tavily(self, consulta: str) -> Busca:
        corpo = {"query": consulta, "search_depth": "basic", "max_results": self.max_resultados,
                 "include_answer": "basic", "country": self.pais}
        try:
            async with self._cliente() as c:
                r = await c.post(f"{TAVILY}/search", json=corpo, headers={"Authorization": f"Bearer {self.chave}"})
        except httpx.HTTPError as e:
            raise ErroWeb(f"sem conexão com a Tavily ({type(e).__name__})") from e
        if r.status_code in COTA_ESTOURADA:
            raise ErroWeb(f"cota da Tavily estourada ({r.status_code})")
        if r.status_code == 401:
            raise ErroWeb("chave da Tavily recusada (confira TAVILY_API_KEY no .env)")
        if r.status_code >= 400:
            raise ErroWeb(f"Tavily respondeu {r.status_code}")
        try:
            dados = r.json()
        except ValueError as e:
            raise ErroWeb("resposta da Tavily ilegível") from e
        self.cota.gastar(1)
        resultados = [
            Resultado(str(x.get("title") or "").strip(), str(x.get("url") or ""), str(x.get("content") or "").strip())
            for x in dados.get("results") or [] if url_valida(str(x.get("url") or ""))
        ]
        return Busca(consulta, str(dados.get("answer") or "").strip(), resultados, "tavily")

    async def _duckduckgo(self, consulta: str) -> Busca:
        try:
            brutos = await asyncio.wait_for(asyncio.to_thread(self._buscar_ddg, consulta, self.max_resultados), 15)
        except Exception as e:  # noqa: BLE001 - a biblioteca levanta tipos próprios (limite, timeout)
            raise ErroWeb(f"a busca na web falhou ({type(e).__name__})") from e
        resultados = [
            Resultado(str(x.get("title") or "").strip(), str(x.get("href") or ""), str(x.get("body") or "").strip())
            for x in brutos or [] if url_valida(str(x.get("href") or ""))
        ]
        return Busca(consulta, "", resultados, "duckduckgo")

    async def ler(self, url: str) -> str:
        """Texto principal da página. Tavily extract (1 crédito) com chave; senão, baixa e limpa o HTML."""
        url = url.strip()
        if not url_valida(url):
            raise ErroWeb("Endereço inválido: use um link http(s) dos resultados.")
        if url not in self._links:
            raise ErroWeb("Só abro links que vieram de uma busca (web_buscar). Use um link dos resultados.")
        chave = "ler:" + url
        guardada = self.cache.pegar(chave)
        if guardada:
            return guardada.get("texto", "")
        texto_ = ""
        if self.chave:
            try:
                texto_ = await self._extrair_tavily(url)
            except ErroWeb as e:
                log.warning("extract da Tavily falhou (%s); baixando a página", e)
        if not texto_:
            texto_ = await self._baixar(url)
        if texto_:
            self.cache.guardar(chave, {"texto": texto_})
        return texto_

    async def _extrair_tavily(self, url: str) -> str:
        try:
            async with self._cliente() as c:
                r = await c.post(f"{TAVILY}/extract", json={"urls": [url]},
                                 headers={"Authorization": f"Bearer {self.chave}"})
        except httpx.HTTPError as e:
            raise ErroWeb(type(e).__name__) from e
        if r.status_code >= 400:
            raise ErroWeb(f"Tavily respondeu {r.status_code}")
        self.cota.gastar(1)
        try:
            itens = r.json().get("results") or []
        except ValueError as e:
            raise ErroWeb("resposta ilegível") from e
        return str(itens[0].get("raw_content") or "").strip() if itens else ""

    async def _baixar(self, url: str) -> str:
        try:
            async with asyncio.timeout(20), self._cliente() as c:
                for _ in range(5):  # segue redirecionamentos um a um: cada destino também tem que ser público
                    if not await asyncio.to_thread(self._publico, url):
                        raise ErroWeb("esse endereço é da rede local; só abro páginas da internet")
                    async with c.stream("GET", url) as r:
                        if not _conexao_publica(r):  # o DNS pode mudar entre a checagem e a conexão
                            raise ErroWeb("esse endereço é da rede local; só abro páginas da internet")
                        if r.is_redirect:
                            url = str(r.next_request.url) if r.next_request else ""
                            if not url_valida(url):
                                raise ErroWeb("a página redirecionou para um endereço inválido")
                            continue
                        if r.status_code >= 400:
                            raise ErroWeb(f"a página respondeu {r.status_code}")
                        tipo = r.headers.get("content-type", "")
                        if "html" not in tipo and "text/plain" not in tipo:
                            raise ErroWeb("a página não é texto (PDF, imagem ou arquivo)")
                        corpo = bytearray()
                        async for pedaco in r.aiter_bytes():
                            corpo.extend(pedaco)
                            if len(corpo) >= MAX_PAGINA:
                                break
                        texto_ = corpo[:MAX_PAGINA].decode(r.encoding or "utf-8", errors="replace")
                        return await asyncio.to_thread(_texto_de_html, texto_)
                raise ErroWeb("a página redirecionou vezes demais")
        except TimeoutError as e:
            raise ErroWeb("a página demorou demais") from e
        except httpx.HTTPError as e:
            raise ErroWeb(f"não consegui abrir a página ({type(e).__name__})") from e


def _conexao_publica(r: httpx.Response) -> bool:
    """IP com que a conexão foi feita de fato. Sem essa informação (transporte de teste), vale a checagem do DNS."""
    rede = r.extensions.get("network_stream")
    if rede is None:
        return True
    endereco = rede.get_extra_info("server_addr")
    if not endereco:
        return True
    try:
        return ipaddress.ip_address(str(endereco[0]).split("%")[0]).is_global
    except ValueError:
        return False


def _ddg(consulta: str, maximo: int) -> list[dict[str, Any]]:
    """Primeiro no Brasil; sem nada, no mundo todo. "Nenhum resultado" vira lista vazia, não erro (visto em 02/10:
    uma consulta de ficha técnica de carro não achava nada em br-pt e achava em inglês)."""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    for regiao in ("br-pt", "wt-wt"):
        try:
            achados = list(DDGS().text(consulta, region=regiao, safesearch="moderate", max_results=maximo) or [])
        except DDGSException as e:
            if "no results" not in str(e).lower():
                raise
            achados = []
        if achados:
            return achados
    return []
