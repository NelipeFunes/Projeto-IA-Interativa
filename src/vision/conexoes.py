"""Tela de Conexões (pedido de 02/10: "não quero ficar rodando comando para conectar"): o estado de cada serviço
e o que a tela pode pedir (conectar, desconectar, ligar/desligar).

Segredos (chave da Tavily, senha do Orbit, credencial do Google) vêm da tela UMA vez, são validados aqui e
gravados no .env ou em data/ (os dois fora do git). Nada daqui devolve um segredo para a tela: ela só fica
sabendo se ele existe. O login de verdade (Google, Spotify, Alexa, Wispr) continua sendo no navegador, na
página do próprio serviço; o Vision nunca vê a senha dessas contas.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vision.config import Config

# Variáveis do .env que a tela pode gravar. Qualquer outra fica como está.
CHAVES_ENV = ("TAVILY_API_KEY", "ORBIT_EMAIL", "ORBIT_PASSWORD", "ORBIT_TOKEN")
LIMITE_CAMPO = 64_000  # o JSON da credencial do Google tem ~400 bytes; o resto, bem menos
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
CHAVE_TAVILY = re.compile(r"^tvly-[A-Za-z0-9_-]{8,120}$")
CLIENT_ID_SPOTIFY = re.compile(r"^[0-9a-f]{32}$")
CODIGO_ORBIT = re.compile(r"^[A-Za-z0-9]{4,10}$")
VALIDADE_DESAFIO_S = 600  # o código do e-mail do Orbit: depois disso, e-mail e senha de novo
GUIA_GOOGLE = "https://github.com/NelipeFunes/Projeto-IA-Interativa/blob/main/docs/guia-google-cloud.md"


@dataclass(frozen=True)
class Servico:
    id: str
    nome: str
    descricao: str
    chave_ligado: str  # o interruptor no config (data/config-local.yaml por cima do config.yaml)


SERVICOS = (
    Servico("google", "Google Agenda", "Compromissos: ler, criar, mudar e avisar antes.", "mcp.google-calendar.ativo"),
    Servico("spotify", "Spotify", "Tocar música no PC (conta Premium).", "spotify.ativo"),
    Servico("alexa", "Alexa", "Luzes da casa pela sua conta da Amazon.", "alexa.ativo"),
    Servico("wispr", "Wispr Flow", "Reuniões, transcrições e notas (só leitura).", "mcp.wispr.ativo"),
    Servico("web", "Busca na web", "Tavily (1.000 buscas grátis por mês); sem chave, usa o DuckDuckGo.", "web.ativo"),
    Servico("orbit", "Orbit", "Finanças e tarefas do seu app.", "mcp.orbit.ativo"),
)
POR_ID = {s.id: s for s in SERVICOS}
# Os que abrem o navegador e esperam você entrar: rodam em segundo plano e podem ser cancelados.
COM_NAVEGADOR = {"google", "spotify", "alexa", "wispr"}


# ------------------------------------------------------------------ .env


def _aspas(valor: str) -> str:
    """Entre aspas simples, com \\ e ' escapados: o python-dotenv devolve o valor exato (tem teste)."""
    return "'" + valor.replace("\\", "\\\\").replace("'", "\\'") + "'"


def gravar_env(arquivo: Path, mudancas: dict[str, str | None]) -> None:
    """Troca (ou acrescenta) só as linhas destas variáveis no .env, mantendo o resto e os comentários.
    None apaga o valor (a linha fica `CHAVE=`). Também vale na hora para este processo (os.environ)."""
    for chave, valor in mudancas.items():
        if chave not in CHAVES_ENV:
            raise ValueError(f"variável que a tela não pode mudar: {chave}")
        # Nenhum caractere de controle (não só \r\n): \x0b, \x85, \u2028... quebram a linha para o splitlines
        # e trocariam a senha em silêncio na próxima gravação (revisão do PR 38).
        if valor is not None and not valor.isprintable():
            raise ValueError("valor com caractere de controle")
    try:
        linhas = arquivo.read_text(encoding="utf-8").split("\n")
    except FileNotFoundError:
        linhas = []
    if linhas and linhas[-1] == "":
        linhas.pop()
    saida, feitas = [], set()
    for linha in linhas:
        corpo = linha.strip().removeprefix("export ").lstrip()
        nome = corpo.split("=", 1)[0].strip()
        if nome in mudancas and "=" in corpo and not corpo.startswith("#"):
            # Toda linha da variável (o dotenv usa a última): a 1ª vira o valor novo, as repetidas somem.
            if nome not in feitas:
                valor = mudancas[nome]
                saida.append(f"{nome}={_aspas(valor) if valor else ''}")
                feitas.add(nome)
            continue
        saida.append(linha)
    for nome, valor in mudancas.items():
        if nome not in feitas:
            saida.append(f"{nome}={_aspas(valor) if valor else ''}")
    tmp = arquivo.with_suffix(".tmp")  # .env.tmp: no .gitignore, e apagado se a troca falhar
    try:
        tmp.write_text("\n".join(saida) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(arquivo)  # nunca fica pela metade
    finally:
        tmp.unlink(missing_ok=True)
    for nome, valor in mudancas.items():
        if valor:
            os.environ[nome] = valor
        else:
            os.environ.pop(nome, None)


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


# ------------------------------------------------------------------ Google


def guardar_credencial_google(cfg: Config, texto: str) -> None:
    """O JSON baixado do Google Cloud (app do tipo "App para computador"). ValueError com a mensagem da tela."""
    from vision.google_login import arquivo_credenciais

    try:
        dados = json.loads(texto)
    except ValueError:
        raise ValueError("esse arquivo não é um JSON: baixe de novo no Google Cloud (Credenciais → baixar)") from None
    if isinstance(dados, dict) and "web" in dados:
        raise ValueError("essa credencial é de \"Aplicativo da Web\": crie uma do tipo \"App para computador\"")
    app = dados.get("installed") if isinstance(dados, dict) else None
    if not (isinstance(app, dict) and isinstance(app.get("client_id"), str)
            and app["client_id"].endswith(".apps.googleusercontent.com") and isinstance(app.get("client_secret"), str)):
        raise ValueError("não parece a credencial do Google Cloud (falta client_id ou client_secret)")
    destino = arquivo_credenciais(cfg)
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, indent=2), encoding="utf-8")
    tmp.replace(destino)


# ------------------------------------------------------------------ estado (o que a tela mostra)


def _campo(nome: str, rotulo: str, tipo: str = "texto", *, valor: str = "", dica: str = "",
           obrigatorio: bool = True) -> dict[str, Any]:
    return {"nome": nome, "rotulo": rotulo, "tipo": tipo, "valor": valor, "dica": dica, "obrigatorio": obrigatorio}


def _google(cfg: Config, status_mcp: dict[str, str]) -> dict[str, Any]:
    from vision.google_login import arquivo_credenciais, dias_desde_login

    tem_credencial = arquivo_credenciais(cfg).exists()
    dias = dias_desde_login(cfg)
    limite = float(cfg.get("agenda.aviso_login_dias", 6))
    campos = [_campo("credencial", "Credencial do Google Cloud (arquivo .json)", "arquivo",
                     dica="Só na primeira vez (ou para trocar). Como criar: veja o guia.",
                     obrigatorio=not tem_credencial)]
    if not tem_credencial:
        situacao, detalhe = "falta", "Falta a credencial do Google Cloud (o arquivo .json do guia)."
    elif dias is None:
        situacao, detalhe = "falta", "Ainda não conectado."
    elif dias >= 7:
        situacao, detalhe = "atencao", "O login venceu (o app em modo teste dura 7 dias). Reconecte."
    else:
        restam = max(1, math.ceil(7 - dias))
        situacao = "atencao" if dias >= limite else "ok"
        detalhe = f"Conectado · o login vence em {restam} dia{'s' if restam > 1 else ''}."
        st = status_mcp.get(str(cfg.get("agenda.servidor", "google-calendar")))
        if st is not None and st != "ok":
            situacao, detalhe = "atencao", "Conectado, mas o servidor da agenda não respondeu ao iniciar."
    return {"situacao": situacao, "detalhe": detalhe, "campos": campos,
            "acao": "Reconectar" if dias is not None else "Conectar", "desconectar": False,
            "ajuda": {"rotulo": "Guia do Google Cloud", "url": GUIA_GOOGLE}}


def _spotify(cfg: Config) -> dict[str, Any]:
    from vision import spotify

    dados = spotify.ler(cfg)
    logado = spotify.tem_login(cfg)
    campos = [_campo("client_id", "Client ID do seu app do Spotify", valor=str(dados.get("client_id") or ""),
                     dica=f"Em developer.spotify.com → Dashboard → Create app (Web API), com o endereço de retorno "
                          f"{spotify.RETORNO}")]
    return {"situacao": "ok" if logado else "falta",
            "detalhe": "Conectado." if logado else "Ainda não conectado.",
            "campos": campos, "acao": "Reconectar" if logado else "Conectar", "desconectar": logado,
            "ajuda": {"rotulo": "Painel do Spotify", "url": "https://developer.spotify.com/dashboard"}}


def _alexa(cfg: Config) -> dict[str, Any]:
    from vision import alexa

    conta = alexa._ler(alexa.pasta(cfg) / "conta.json") or {}
    logado = alexa.tem_login(cfg)
    email = str(conta.get("email") or "") if isinstance(conta, dict) else ""
    return {"situacao": "ok" if logado else "falta",
            "detalhe": f"Conectado como {email}." if logado and email else "Ainda não conectado.",
            "campos": [_campo("email", "E-mail da conta Amazon", "email", valor=email,
                              dica="A senha você digita na página da Amazon, não aqui.")],
            "acao": "Reconectar" if logado else "Conectar", "desconectar": logado}


def _wispr(cfg: Config) -> dict[str, Any]:
    from vision import wispr

    logado = wispr.tem_login(cfg)
    return {"situacao": "ok" if logado else "falta",
            "detalhe": "Conectado." if logado else "Ainda não conectado (entre com Google, Apple ou Microsoft).",
            "campos": [], "acao": "Reconectar" if logado else "Conectar", "desconectar": logado}


def _web(cfg: Config) -> dict[str, Any]:
    from vision.web import Cota

    tem_chave = bool(_env("TAVILY_API_KEY"))
    if tem_chave:
        limite = int(cfg.get("web.cota_mensal", 1000))
        gastos = Cota(cfg.dados / "cache" / "web-cota.json", limite).gastos()
        detalhe = f"Tavily · {gastos} de {limite} buscas neste mês."
    else:
        detalhe = "Sem chave: buscando pelo DuckDuckGo."
    return {"situacao": "ok" if tem_chave else "falta", "detalhe": detalhe,
            "campos": [_campo("chave", "Chave da Tavily", "senha",
                              dica="Começa com tvly-. Fica no .env, fora do git; a tela nunca mostra ela de volta.",
                              obrigatorio=True)],
            "acao": "Trocar chave" if tem_chave else "Salvar chave", "desconectar": tem_chave,
            "ajuda": {"rotulo": "tavily.com", "url": "https://app.tavily.com"}}


# O login do Orbit pode pedir o código que ele manda por e-mail. Entre "Conectar" e o código, o desafio (e o
# e-mail e a senha digitados) ficam só na memória deste processo, por VALIDADE_DESAFIO_S; nunca vão para a tela.
_desafio_orbit: dict[str, Any] | None = None


def _desafio_valido() -> dict[str, Any] | None:
    global _desafio_orbit
    if _desafio_orbit is not None and time.monotonic() - _desafio_orbit["quando"] > VALIDADE_DESAFIO_S:
        _desafio_orbit = None
    return _desafio_orbit


def _dias_do_token(token: str) -> float | None:
    """Quantos dias faltam para o accessToken (JWT) vencer, pelo `exp`. Sem conferir assinatura: é só para avisar."""
    try:
        meio = token.split(".")[1]
        dados = json.loads(base64.urlsafe_b64decode(meio + "=" * (-len(meio) % 4)))
        dias = (float(dados["exp"]) - time.time()) / 86400
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return dias if math.isfinite(dias) else None  # exp infinito ou NaN derrubaria a tela inteira (revisão do PR 40)


def _orbit(cfg: Config) -> dict[str, Any]:
    email = _env("ORBIT_EMAIL")
    token = _env("ORBIT_TOKEN")
    desafio = _desafio_valido()
    if desafio is not None:
        # "Desconectar" aqui só cancela o código (e-mail errado, código que não chegou): o login antigo fica.
        return {"situacao": "atencao", "detalhe": f"O Orbit mandou um código para {desafio['mascarado']}.",
                "campos": [_campo("codigo", "Código do e-mail", dica="Chega em instantes; vale por alguns minutos. "
                                                                     "Para recomeçar, use Desconectar.")],
                "acao": "Confirmar código", "desconectar": True}
    tem = bool(token or (email and _env("ORBIT_PASSWORD")))
    situacao, detalhe = ("ok", f"Conectado ({email})." if email else "Conectado.") if tem else ("falta", "Sem login.")
    dias = _dias_do_token(token) if token else None
    if dias is not None and dias <= 0:
        situacao, detalhe = "atencao", "O acesso venceu: reconecte aqui (o Orbit pode pedir o código do e-mail)."
    elif dias is not None:
        restam = max(1, math.ceil(dias))
        detalhe = detalhe.rstrip(".") + f" · o acesso vence em {restam} dia{'s' if restam > 1 else ''}."
    return {"situacao": situacao, "detalhe": detalhe,
            "campos": [_campo("email", "E-mail do Orbit", "email", valor=email),
                       _campo("senha", "Senha do Orbit", "senha",
                              dica=f"Vai para {_host_orbit()} e fica no .env, fora do git.")],
            "acao": "Trocar login" if tem else "Conectar", "desconectar": tem}


def _host_orbit() -> str:
    """Para onde o e-mail e a senha vão (repositório público: quem clonar vê que o padrão é o Orbit do Felipe)."""
    from urllib.parse import urlparse

    url = _env("ORBIT_URL") or "https://orbit-fdzy.onrender.com"
    return urlparse(url).netloc or url


def _orbit_api(cfg: Config):
    """O cliente do Orbit fica com o servidor MCP dele (mcp_servers/orbit), fora do pacote vision."""
    caminho = cfg.raiz / "mcp_servers" / "orbit" / "orbit_api.py"
    if not caminho.is_file():
        raise ValueError("não achei o cliente do Orbit (mcp_servers/orbit)")
    spec = importlib.util.spec_from_file_location("vision_orbit_api", caminho)
    if spec is None or spec.loader is None:
        raise ValueError("não achei o cliente do Orbit (mcp_servers/orbit)")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


async def _conectar_orbit(cfg: Config, dados: dict[str, str], avisar: Callable[[str], None]) -> bool | None:
    """True: conectado. None: o Orbit mandou um código por e-mail (a tela pede). False: não deu."""
    global _desafio_orbit
    mod = _orbit_api(cfg)
    if "codigo" in dados:
        d = _desafio_valido()
        if d is None:
            avisar("O código venceu: entre de novo com e-mail e senha.")
            return False
        api = mod.OrbitAPI(token="", email=d["email"], senha=d["senha"])
        try:
            token = await api.confirmar_codigo(d["desafio"], dados["codigo"])
        except mod.ErroOrbit as e:
            avisar(str(e))
            return False
        finally:
            await api.fechar()
        email, senha = d["email"], d["senha"]
        _desafio_orbit = None
    else:
        email, senha = dados["email"], dados["senha"]
        avisar("Conferindo o login no Orbit (se ele estiver dormindo no Render, leva até 1 minuto)...")
        api = mod.OrbitAPI(token="", email=email, senha=senha)
        try:
            token = await api.login()
        except mod.PrecisaCodigo as e:
            _desafio_orbit = {"desafio": e.desafio, "mascarado": e.email_mascarado, "email": email, "senha": senha,
                              "quando": time.monotonic()}
            avisar(f"O Orbit mandou um código para {e.email_mascarado}: digite ele aqui.")
            return None
        except mod.ErroOrbit as e:
            avisar(str(e))
            return False
        finally:
            await api.fechar()
    gravar_env(cfg.raiz / ".env", {"ORBIT_EMAIL": email, "ORBIT_PASSWORD": senha, "ORBIT_TOKEN": token})
    avisar("Orbit conectado.")
    return True


def estado(cfg: Config, status_mcp: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Um cartão por serviço, na ordem da tela. Nunca inclui um segredo."""
    status_mcp = status_mcp or {}
    feitos = {"google": lambda: _google(cfg, status_mcp), "spotify": lambda: _spotify(cfg),
              "alexa": lambda: _alexa(cfg), "wispr": lambda: _wispr(cfg), "web": lambda: _web(cfg),
              "orbit": lambda: _orbit(cfg)}
    cartoes = []
    for s in SERVICOS:
        ligado = bool(cfg.get(s.chave_ligado, True))
        c = {"id": s.id, "nome": s.nome, "descricao": s.descricao, "ligado": ligado, **feitos[s.id]()}
        if not ligado:
            c["situacao"] = "desligado"
        cartoes.append(c)
    return cartoes


def _resumo(*partes: Any) -> str:
    """Impressão digital de um segredo: dá para comparar sem guardar (nem mostrar) o valor."""
    return hashlib.sha256("\0".join(str(p) for p in partes).encode()).hexdigest()[:16]


def assinatura(cfg: Config) -> dict[str, str]:
    """O que o Vision montou ao iniciar, por serviço. Mudou depois disso: só vale ao reiniciar."""
    from vision import alexa, spotify, wispr
    from vision.google_login import ARQUIVO_DATA, arquivo_credenciais

    def ler(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    conta = alexa._ler(alexa.pasta(cfg) / "conta.json") or {}
    partes = {
        "google": (arquivo_credenciais(cfg).exists(), ler(cfg.dados / ARQUIVO_DATA)),
        "spotify": (spotify.tem_login(cfg), spotify.ler(cfg).get("client_id")),
        "alexa": (alexa.tem_login(cfg), conta.get("email") if isinstance(conta, dict) else None),
        "wispr": (wispr.tem_login(cfg),),
        "web": (_env("TAVILY_API_KEY"),),
        "orbit": tuple(_env(n) for n in ("ORBIT_EMAIL", "ORBIT_PASSWORD", "ORBIT_TOKEN")),
    }
    return {s.id: _resumo(bool(cfg.get(s.chave_ligado, True)), *partes[s.id]) for s in SERVICOS}


def mudou_desde(cfg: Config, inicio: dict[str, str]) -> list[str]:
    """Nomes dos serviços que mudaram desde que o Vision iniciou (a tela pede para reiniciar)."""
    agora = assinatura(cfg)
    return [s.nome for s in SERVICOS if agora.get(s.id) != inicio.get(s.id)]


# ------------------------------------------------------------------ o que a tela pede


def validar(servico: Any, dados: Any) -> dict[str, str]:
    """Os campos de "conectar", limpos. ValueError com a mensagem para a tela."""
    if servico not in POR_ID:
        raise ValueError("serviço desconhecido")
    if not isinstance(dados, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in dados.items()):
        raise ValueError("dados inválidos")
    if any(len(v) > LIMITE_CAMPO for v in dados.values()):
        raise ValueError("campo grande demais")
    d = {k: v.strip() for k, v in dados.items()}
    if servico == "google":
        return {"credencial": d["credencial"]} if d.get("credencial") else {}
    if servico == "spotify":
        if not CLIENT_ID_SPOTIFY.match(d.get("client_id", "")):
            raise ValueError("o Client ID do Spotify tem 32 letras e números (copie do Dashboard do app)")
        return {"client_id": d["client_id"]}
    if servico == "alexa":
        if not EMAIL.match(d.get("email", "")):
            raise ValueError("e-mail inválido")
        return {"email": d["email"]}
    if servico == "web":
        if not CHAVE_TAVILY.match(d.get("chave", "")):
            raise ValueError("a chave da Tavily começa com tvly- (copie do painel da Tavily)")
        return {"chave": d["chave"]}
    if servico == "orbit" and "codigo" in d:
        if not CODIGO_ORBIT.match(d["codigo"]):
            raise ValueError("o código tem só letras e números (copie do e-mail)")
        return {"codigo": d["codigo"]}
    if servico == "orbit":
        senha = dados.get("senha", "")  # senha não perde espaço das pontas
        if not EMAIL.match(d.get("email", "")):
            raise ValueError("e-mail inválido")
        if not 1 <= len(senha) <= 200 or not senha.isprintable():
            raise ValueError("senha inválida")
        return {"email": d["email"], "senha": senha}
    return {}


async def conectar(cfg: Config, servico: str, dados: dict[str, str], avisar: Callable[[str], None],
                   cancelar: threading.Event) -> bool | None:
    """Já validado por `validar`. Os com navegador esperam você entrar (minutos); os outros só gravam.
    None: falta um passo seu (o código do Orbit)."""
    import asyncio

    if servico == "google":
        from vision.google_login import fazer_login

        if dados.get("credencial"):
            guardar_credencial_google(cfg, dados["credencial"])
        return await asyncio.to_thread(fazer_login, cfg, avisar, sem_console=True, cancelar=cancelar) == 0
    if servico == "spotify":
        from vision import spotify

        return await spotify.login(cfg, dados["client_id"], avisar=avisar) == 0
    if servico == "alexa":
        from vision import alexa

        return await alexa.login_interativo(cfg, dados["email"], avisar=avisar) == 0
    if servico == "wispr":
        from vision import wispr

        return await wispr.login(cfg, avisar=avisar) == 0
    if servico == "web":
        gravar_env(cfg.raiz / ".env", {"TAVILY_API_KEY": dados["chave"]})
        avisar("Chave da Tavily guardada.")
        return True
    if servico == "orbit":
        return await _conectar_orbit(cfg, dados, avisar)
    raise ValueError("serviço desconhecido")


def desconectar(cfg: Config, servico: str) -> str:
    """Esquece o login daquele serviço neste PC. Devolve a frase para a tela."""
    if servico == "spotify":
        from vision import spotify

        client_id = spotify.ler(cfg).get("client_id")
        # O Client ID não é segredo (login PKCE) e evita copiar de novo; os tokens vão embora.
        spotify.gravar(cfg, {"client_id": client_id} if client_id else {})
        return "Spotify desconectado."
    if servico == "alexa":
        from vision import alexa

        pasta = alexa.pasta(cfg).resolve()
        if pasta.parent == cfg.dados.resolve() and pasta.is_dir():  # só a pasta da Alexa dentro de data/
            shutil.rmtree(pasta)
        return "Alexa desconectada."
    if servico == "wispr":
        from vision import wispr

        wispr.arquivo_tokens(cfg).unlink(missing_ok=True)
        return "Wispr Flow desconectado."
    if servico == "web":
        gravar_env(cfg.raiz / ".env", {"TAVILY_API_KEY": None})
        return "Chave da Tavily apagada: a busca volta para o DuckDuckGo."
    if servico == "orbit":
        global _desafio_orbit
        if _desafio_valido() is not None:
            _desafio_orbit = None  # cancelar o código não apaga o login que já existia
            return "Código cancelado: entre de novo com e-mail e senha."
        gravar_env(cfg.raiz / ".env", {"ORBIT_EMAIL": None, "ORBIT_PASSWORD": None, "ORBIT_TOKEN": None})
        return "Login do Orbit apagado."
    raise ValueError("esse serviço não tem desconectar pela tela")
