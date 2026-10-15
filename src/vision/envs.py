"""Variáveis de ambiente que cada processo filho recebe.

Antes (achado de 15/10): todo servidor MCP e todo comando do PC herdava o ambiente INTEIRO do Vision, onde a senha e o
token do Orbit e a chave da Tavily já estavam (o `.env` é carregado em `config.carregar` e a tela de Conexões grava lá
também). O Node de terceiros do Google Agenda recebia tudo isso sem precisar.

Agora:
- servidor MCP (`para_servidor_mcp`): só o básico do Windows e do proxy, mais o que o config declara em `env:` para
  AQUELE servidor. Um valor `$NOME` é copiado da variável `NOME` do Vision (é como o Orbit recebe `ORBIT_EMAIL` etc.).
- comando do PC (`sem_segredos`): o ambiente de sempre, menos o que parece segredo (o comando é confirmado pelo
  Felipe, mas ele não precisa ver a senha do Orbit nem por engano).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

# O mínimo para o Windows, o Node, o Python e PowerShell funcionarem, e para a rede passar por proxy corporativo.
BASICAS = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "USERNAME",
    "USERDOMAIN", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "PROGRAMW6432", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)", "COMMONPROGRAMW6432", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS", "PSMODULEPATH", "LANG", "LC_ALL",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
})
_REFERENCIA = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_PARECE_SEGREDO = re.compile(r"(PASSWORD|PASSWD|SENHA|TOKEN|SECRET|API_?KEY|CREDENTIAL|PRIVATE_?KEY)", re.IGNORECASE)


def para_servidor_mcp(declarado: Mapping[str, object] | None, ambiente: Mapping[str, str] | None = None) -> dict[str, str]:
    """O ambiente de um servidor MCP: o básico + o `env:` do config daquele servidor.
    `data/...` vira caminho (como sempre), `$NOME` vira o valor da variável NOME do Vision (se existir)."""
    origem = os.environ if ambiente is None else ambiente
    env = {k: v for k, v in origem.items() if k.upper() in BASICAS}
    for chave, valor in (declarado or {}).items():
        valor = str(valor)
        ref = _REFERENCIA.match(valor)
        if ref:
            if ref.group(1) in origem:
                env[chave] = origem[ref.group(1)]
            continue
        env[chave] = valor
    return env


def sem_segredos(ambiente: Mapping[str, str] | None = None) -> dict[str, str]:
    """O ambiente do Vision sem as variáveis que parecem segredo (senha, token, chave de API...)."""
    origem = os.environ if ambiente is None else ambiente
    return {k: v for k, v in origem.items() if not _PARECE_SEGREDO.search(k)}
