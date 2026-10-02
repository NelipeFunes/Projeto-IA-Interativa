"""Tela de Conexões (pedido de 02/10: "não quero ficar rodando comando para conectar"): o estado de cada serviço
e o que a tela pode pedir (conectar, desconectar, ligar/desligar).

Segredos (chave da Tavily, senha do Orbit, credencial do Google) vêm da tela UMA vez, são validados aqui e
gravados no .env ou em data/ (os dois fora do git). Nada daqui devolve um segredo para a tela: ela só fica
sabendo se ele existe. O login de verdade (Google, Spotify, Alexa, Wispr) continua sendo no navegador, na
página do próprio serviço; o Vision nunca vê a senha dessas contas.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
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
        if valor is not None and any(c in valor for c in "\r\n\0"):
            raise ValueError("valor com quebra de linha")
    try:
        linhas = arquivo.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        linhas = []
    feitas = set()
    for i, linha in enumerate(linhas):
        nome = linha.split("=", 1)[0].strip()
        if nome in mudancas and "=" in linha and not linha.lstrip().startswith("#") and nome not in feitas:
            valor = mudancas[nome]
            linhas[i] = f"{nome}={_aspas(valor) if valor else ''}"
            feitas.add(nome)
    for nome, valor in mudancas.items():
        if nome not in feitas:
            linhas.append(f"{nome}={_aspas(valor) if valor else ''}")
    tmp = arquivo.with_suffix(".tmp")
    tmp.write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(arquivo)  # nunca fica pela metade
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


def _orbit(cfg: Config) -> dict[str, Any]:
    email = _env("ORBIT_EMAIL")
    tem = bool(_env("ORBIT_TOKEN") or (email and _env("ORBIT_PASSWORD")))
    return {"situacao": "ok" if tem else "falta",
            "detalhe": (f"Login guardado ({email})." if email else "Token guardado.") if tem else "Sem login.",
            "campos": [_campo("email", "E-mail do Orbit", "email", valor=email),
                       _campo("senha", "Senha do Orbit", "senha", dica="Fica no .env, fora do git.")],
            "acao": "Trocar login" if tem else "Salvar login", "desconectar": tem}


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
    if servico == "orbit":
        senha = dados.get("senha", "")  # senha não perde espaço das pontas
        if not EMAIL.match(d.get("email", "")):
            raise ValueError("e-mail inválido")
        if not 1 <= len(senha) <= 200 or any(c in senha for c in "\r\n\0"):
            raise ValueError("senha inválida")
        return {"email": d["email"], "senha": senha}
    return {}


async def conectar(cfg: Config, servico: str, dados: dict[str, str], avisar: Callable[[str], None],
                   cancelar: threading.Event) -> bool:
    """Já validado por `validar`. Os com navegador esperam você entrar (minutos); os outros só gravam."""
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
        gravar_env(cfg.raiz / ".env", {"ORBIT_EMAIL": dados["email"], "ORBIT_PASSWORD": dados["senha"],
                                       "ORBIT_TOKEN": None})
        avisar("Login do Orbit guardado.")
        return True
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
        gravar_env(cfg.raiz / ".env", {"ORBIT_EMAIL": None, "ORBIT_PASSWORD": None, "ORBIT_TOKEN": None})
        return "Login do Orbit apagado."
    raise ValueError("esse serviço não tem desconectar pela tela")
