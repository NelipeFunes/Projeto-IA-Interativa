"""O Vision mexendo no PC (pedido de 01/10): programas, sites, volume, mídia, travar e desligar, e um comando
do PowerShell quando nenhuma ação pronta serve.

Nada aqui trava a conversa: o Windows é chamado fora do event loop (subprocesso assíncrono ou thread), sem
janela, e toda ferramenta tem prazo (`Ferramenta.prazo_s`). Abrir um programa não espera ele terminar.

Confirmação: desligar/reiniciar é sensível; o comando do PowerShell pede "sim" SEMPRE, em qualquer modo, com
o comando à vista. O resto vai direto (não dá prejuízo: fechar é educado, o app pergunta se tem algo sem salvar).
"""

from __future__ import annotations

import asyncio
import ctypes
import difflib
import json
import logging
import os
import re
import subprocess
import time
import unicodedata
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from vision.tools.base import PRAZO_PC_S, ErroFerramenta, Ferramenta, esquema, numero, texto

log = logging.getLogger(__name__)

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CACHE_APPS_S = 600
PRAZO_COMANDO_S = 60
LIMITE_SAIDA = 3000
# Nomes do dia a dia que não batem com o nome do app ou do processo.
APELIDOS = {"navegador": "chrome", "internet": "chrome", "google": "chrome", "musica": "spotify",
            "notas": "bloco de notas", "notepad": "bloco de notas", "calculadora": "calculadora",
            "configuracoes": "configuracoes", "explorador": "explorador de arquivos", "arquivos": "explorador de arquivos",
            "zap": "whatsapp", "cs": "counter-strike 2", "cs2": "counter-strike 2", "vscode": "visual studio code",
            "code": "visual studio code", "terminal": "terminal", "steam": "steam", "discord": "discord"}
# Processo de cada app, para fechar (o nome do app nem sempre é o do executável).
PROCESSOS = {"bloco de notas": "notepad.exe", "calculadora": "calculatorapp.exe", "chrome": "chrome.exe",
             "google chrome": "chrome.exe", "spotify": "spotify.exe", "whatsapp": "whatsapp.exe",
             "discord": "discord.exe", "steam": "steam.exe", "visual studio code": "code.exe",
             "counter-strike 2": "cs2.exe", "slack": "slack.exe", "edge": "msedge.exe", "microsoft edge": "msedge.exe"}
# Fechar estes derruba o Windows ou o próprio Vision.
INTOCAVEIS = {"explorer.exe", "csrss.exe", "winlogon.exe", "lsass.exe", "services.exe", "svchost.exe", "smss.exe",
              "wininit.exe", "dwm.exe", "system", "python.exe", "pythonw.exe", "visionw.exe", "vision.exe",
              "ollama.exe", "ollama app.exe", "msedgewebview2.exe", "node.exe", "uv.exe", "claude.exe",
              "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "sihost.exe",
              "taskhostw.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
              "shellexperiencehost.exe", "ctfmon.exe", "fontdrvhost.exe", "lsaiso.exe", "registry"}
LIMITE_COMANDO = 300  # comando maior que isso não dá para conferir de ouvido: é recusado, não cortado
# Vão direto quando é o Felipe que pede; depois de ler texto de fora (reunião, convite, nota), pedem "sim":
# um texto injetado não abre site com dados no endereço nem fecha programa sozinho (revisão do PR 20).
_DIRETA = {"confirmar_se_externo": True, "prazo_s": PRAZO_PC_S}
TECLAS = {"tocar_pausar": 0xB3, "proxima": 0xB0, "anterior": 0xB1, "parar": 0xB2, "mudo": 0xAD,
          "volume_mais": 0xAF, "volume_menos": 0xAE}


def normalizar(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.+-]", " ", t)).strip()


_PASTA_USUARIO = re.compile(r"(?i)\b([a-z]):[\\/]+users[\\/]+([^\\/\s'\"]+)")


def corrigir_caminhos(comando: str, casa: str | None = None) -> str:
    """O modelo chuta "C:/Users/Felipe": uma pasta de usuário que não existe vira a pasta de verdade."""
    casa = casa or os.environ.get("USERPROFILE", "")
    if not casa:
        return comando

    def trocar(m: re.Match) -> str:
        if Path(m.group(0)).exists() or m.group(2).lower() in {"public", "default", "all users"}:
            return m.group(0)
        return casa

    return _PASTA_USUARIO.sub(trocar, comando)


def _comando_conferivel(args: dict[str, Any]) -> str:
    """O comando como vai rodar, se der para conferir inteiro antes do "sim". Longo ou com várias linhas é
    recusado: cortar o que se mostra e rodar o resto seria confirmar às cegas (revisão do PR 20)."""
    comando = corrigir_caminhos(str(args.get("comando") or "").strip())
    if not comando:
        raise ErroFerramenta("Qual comando?")
    if "\n" in comando or "\r" in comando or len(comando) > LIMITE_COMANDO:
        raise ErroFerramenta(f"Comando longo demais para o Felipe conferir antes (até {LIMITE_COMANDO} caracteres, "
                             "uma linha). Faça em passos menores.")
    return comando


def _tecla(vk: int, vezes: int = 1) -> None:
    for _ in range(vezes):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def nivel_alvo(acao: str, atual: float, nivel: float | None) -> float:
    """Volume final (0 a 1). "aumenta o volume" sem dizer quanto: 10%. (Um 0 explícito é 0, não o padrão.)"""
    passo = 10 if nivel is None else nivel
    alvo = {"definir": (nivel or 0) / 100, "aumentar": atual + passo / 100, "diminuir": atual - passo / 100}[acao]
    return max(0.0, min(1.0, alvo))


def _volume(acao: str, nivel: float | None) -> int:
    """Volume do alto-falante padrão (Core Audio). Devolve o nível final em %."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        ev = AudioUtilities.GetSpeakers().EndpointVolume
        atual = ev.GetMasterVolumeLevelScalar()
        if acao == "mudo":
            ev.SetMute(1, None)
        elif acao == "som":
            ev.SetMute(0, None)
        else:
            alvo = nivel_alvo(acao, atual, nivel)
            ev.SetMasterVolumeLevelScalar(alvo, None)
            if acao != "diminuir" or alvo > 0:
                ev.SetMute(0, None)
        final = round(ev.GetMasterVolumeLevelScalar() * 100)
        del ev  # a interface COM é solta antes do CoUninitialize
        return final
    finally:
        comtypes.CoUninitialize()


class PC:
    def __init__(self, pasta_logs: Path, abrir=os.startfile, navegador=webbrowser.open):
        self.pasta_logs = pasta_logs
        self._abrir = abrir  # trocáveis nos testes
        self._navegador = navegador
        self._apps: list[dict[str, str]] = []
        self._apps_em = 0.0

    # ------------------------------------------------------------------ apoio

    async def _rodar(self, *cmd: str, prazo: float = 20) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, creationflags=SEM_JANELA)
        try:
            saida, _ = await asyncio.wait_for(proc.communicate(), prazo)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise ErroFerramenta(f"O comando demorou mais de {prazo:.0f} s e foi interrompido.") from None
        except asyncio.CancelledError:  # prazo da ferramenta ou o núcleo encerrando: não deixa processo solto
            proc.kill()
            raise
        return proc.returncode or 0, saida.decode("utf-8", errors="replace")

    async def apps(self) -> list[dict[str, str]]:
        """Apps do Menu Iniciar (desktop e Store), com o id que o Windows usa para abrir. Em cache por 10 min."""
        if self._apps and time.monotonic() - self._apps_em < CACHE_APPS_S:
            return self._apps
        _, saida = await self._rodar(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress")
        try:
            dados = json.loads(saida or "[]")
        except json.JSONDecodeError as e:
            raise ErroFerramenta("Não consegui ler a lista de programas do Windows.") from e
        self._apps = [{"nome": a["Name"], "id": a["AppID"]} for a in (dados if isinstance(dados, list) else [dados])]
        self._apps_em = time.monotonic()
        return self._apps

    def escolher(self, pedido: str, apps: list[dict[str, str]]) -> dict[str, str] | None:
        alvo = normalizar(pedido)
        alvo = normalizar(APELIDOS.get(alvo, alvo))
        if len(alvo) < 3:  # "cs", "o": curto demais para casar por pedaço com um app qualquer
            return None
        nomes = {normalizar(a["nome"]): a for a in apps}
        if alvo in nomes:
            return nomes[alvo]
        contem = [a for n, a in nomes.items() if alvo in n.split() or n.startswith(alvo) or alvo in n]
        if contem:
            return min(contem, key=lambda a: len(a["nome"]))  # "chrome" → "Google Chrome", não "Chrome Remote..."
        parecidos = difflib.get_close_matches(alvo, list(nomes), n=1, cutoff=0.75)
        return nomes[parecidos[0]] if parecidos else None

    # ------------------------------------------------------------------ ferramentas

    async def abrir_programa(self, args: dict[str, Any]) -> str:
        pedido = str(args.get("nome") or "").strip()
        if not pedido:
            raise ErroFerramenta("Qual programa?")
        app = self.escolher(pedido, await self.apps())
        if app is None:  # só o que está no Menu Iniciar: nada de achar um .exe solto pelo PATH (revisão do PR 20)
            raise ErroFerramenta(f"Não achei um programa chamado '{pedido}' no Menu Iniciar.")
        # O id do Get-StartApps abre qualquer app, de desktop ou da Store, pelo shell do Windows.
        await asyncio.to_thread(self._abrir, f"shell:AppsFolder\\{app['id']}")
        return f"Abri {app['nome']}."

    async def fechar_programa(self, args: dict[str, Any]) -> str:
        import psutil

        pedido = normalizar(str(args.get("nome") or ""))
        pedido = normalizar(APELIDOS.get(pedido, pedido))
        if not pedido:
            raise ErroFerramenta("Qual programa?")
        exe = PROCESSOS.get(pedido)
        nomes = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
        if exe is None and len(pedido) >= 4:  # pedaço curto ("co", "sv") casaria com processo errado
            candidatos = [n for n in nomes if n.endswith(".exe") and pedido.replace(" ", "") in n.replace(" ", "")]
            exe = min(candidatos, key=len) if candidatos else None
        if exe is None or exe.lower() not in nomes:
            raise ErroFerramenta(f"'{args.get('nome')}' não está aberto.")
        if exe.lower() in INTOCAVEIS:
            raise ErroFerramenta(f"Não fecho {exe}: é do Windows ou do próprio Vision.")
        # Sem /F: o Windows pede para o app fechar (como o X da janela); se tiver algo sem salvar, ele pergunta.
        codigo, saida = await self._rodar("taskkill", "/IM", exe)
        if codigo != 0:
            raise ErroFerramenta(f"O Windows não fechou {exe}: {saida.strip()[:200]}")
        return f"Pedi para {exe.removesuffix('.exe')} fechar."

    async def abrir_site(self, args: dict[str, Any]) -> str:
        pedido = str(args.get("endereco") or "").strip()
        if not pedido:
            raise ErroFerramenta("Qual site ou o que buscar?")
        url = pedido
        if re.match(r"^[\w.-]+:\d+(/|$)", pedido):  # "localhost:3000", "site.com:8080"
            pedido = url = "http://" + pedido
        if re.match(r"^[a-z][a-z0-9+.-]*:", pedido, re.IGNORECASE) and not re.match(r"^https?://", pedido, re.I):
            raise ErroFerramenta("Só abro endereços da web (http ou https).")  # file://, ms-settings: etc.
        if re.match(r"^https?://", pedido, re.IGNORECASE):
            url = pedido
        elif " " in pedido or "." not in pedido:
            url = "https://www.google.com/search?q=" + quote_plus(pedido)
        elif not re.match(r"^https?://", pedido, re.IGNORECASE):
            url = "https://" + pedido
        if urlparse(url).scheme not in ("http", "https"):
            raise ErroFerramenta("Só abro endereços da web (http ou https).")
        await asyncio.to_thread(self._navegador, url)
        if url.startswith("https://www.google.com/search"):
            return f"Abri a busca por '{pedido}' no navegador."
        return f"Abri {url} no navegador."

    async def mudar_volume(self, args: dict[str, Any]) -> str:
        acao = str(args.get("acao") or "").lower()
        nivel = args.get("nivel")
        if acao not in {"definir", "aumentar", "diminuir", "mudo", "som"}:
            raise ErroFerramenta("Ação de volume: definir, aumentar, diminuir, mudo ou som.")
        try:
            nivel = None if nivel in (None, "") else float(nivel)
        except (TypeError, ValueError) as e:
            raise ErroFerramenta("Nível de volume de 0 a 100.") from e
        if acao == "definir" and (nivel is None or not 0 <= nivel <= 100):
            raise ErroFerramenta("Nível de volume de 0 a 100.")
        final = await asyncio.to_thread(_volume, acao, nivel)
        return "Som mudo." if acao == "mudo" else f"Volume em {final}%."

    async def controlar_midia(self, args: dict[str, Any]) -> str:
        acao = str(args.get("acao") or "").lower()
        if acao not in {"tocar_pausar", "proxima", "anterior", "parar"}:
            raise ErroFerramenta("Ação de mídia: tocar_pausar, proxima, anterior ou parar.")
        await asyncio.to_thread(_tecla, TECLAS[acao])
        return {"tocar_pausar": "Play/pausa.", "proxima": "Próxima.", "anterior": "Anterior.", "parar": "Parei."}[acao]

    async def travar(self, _args: dict[str, Any]) -> str:
        await asyncio.to_thread(ctypes.windll.user32.LockWorkStation)
        return "PC travado."

    async def energia(self, args: dict[str, Any]) -> str:
        acao = str(args.get("acao") or "").lower()
        if acao == "suspender":
            await self._rodar("rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0")
            return "Suspendendo."
        flag = {"desligar": "/s", "reiniciar": "/r"}.get(acao)
        if flag is None:
            raise ErroFerramenta("Ação: desligar, reiniciar ou suspender.")
        codigo, saida = await self._rodar("shutdown", flag, "/t", "30")
        if codigo != 0:
            raise ErroFerramenta(f"O Windows recusou: {saida.strip()[:200]}")
        return f"O PC vai {acao} em 30 segundos. Para cancelar, me peça para cancelar o desligamento."

    async def descrever_energia(self, args: dict[str, Any]) -> str:
        acao = str(args.get("acao") or "?").lower()
        return "Vou suspender o PC agora." if acao == "suspender" else f"Vou {acao} o PC em 30 segundos."

    async def cancelar_energia(self, _args: dict[str, Any]) -> str:
        codigo, _ = await self._rodar("shutdown", "/a")
        return "Desligamento cancelado." if codigo == 0 else "Não havia desligamento agendado."

    async def rodar_comando(self, args: dict[str, Any]) -> str:
        comando = _comando_conferivel(args)
        preparo = "$ProgressPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        inicio = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            codigo, saida = await self._rodar("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                              preparo + comando, prazo=PRAZO_COMANDO_S)
        except ErroFerramenta:
            self._registrar(inicio, comando, "prazo estourado")
            raise
        except Exception as e:
            self._registrar(inicio, comando, f"erro {type(e).__name__}")
            raise
        self._registrar(inicio, comando, f"código {codigo}")
        saida = saida.strip() or "(sem saída)"
        if len(saida) > LIMITE_SAIDA:
            saida = saida[:LIMITE_SAIDA] + "\n[... saída cortada]"
        if codigo != 0:  # falhou: vira "Não consegui: ..." para o Felipe, não "Feito."
            raise ErroFerramenta(f"o comando falhou (código {codigo}). {saida}")
        return saida

    def _registrar(self, quando: str, comando: str, resultado: str) -> None:
        """Quem rodou o quê: o comando e o resultado (sem a saída, que pode ter dados) em data/logs/comandos.log.
        O comando vai com repr(): uma quebra de linha nele não forja outra linha do log."""
        try:
            self.pasta_logs.mkdir(parents=True, exist_ok=True)
            with (self.pasta_logs / "comandos.log").open("a", encoding="utf-8") as f:
                f.write(f"{quando}\t{resultado}\t{comando!r}\n")
        except OSError:
            log.exception("não consegui gravar o log de comandos")

    async def descrever_comando(self, args: dict[str, Any]) -> str:
        comando = _comando_conferivel(args)  # o que você confirma é exatamente o que roda
        motivo = str(args.get("motivo") or "").strip().replace("\n", " ")[:80]
        return f"Vou rodar no PowerShell: {comando}" + (f" (para {motivo})" if motivo else "")

    # ------------------------------------------------------------------ atalho

    async def atalho(self, texto: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """Mídia e volume ditos de forma curta, sem passar pelo modelo. None = o modelo decide."""
        cmd = comando_de_pc(texto)
        if cmd is None:
            return None
        nome, args = cmd
        executar = {"midia": self.controlar_midia, "volume": self.mudar_volume}[nome]
        try:
            return nome, args, await executar(args), True
        except ErroFerramenta as e:
            return nome, args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        return [
            Ferramenta("programa_abrir", "Abre um programa do PC pelo nome (ex.: Spotify, Chrome, Bloco de notas).",
                       esquema(["nome"], nome=texto("Nome do programa, como o Felipe disse")),
                       self.abrir_programa, escrita=True, grupo="pc", confirmar=False, **_DIRETA),
            Ferramenta("programa_fechar", "Fecha um programa aberto (como clicar no X; ele pergunta se precisar salvar).",
                       esquema(["nome"], nome=texto("Nome do programa")),
                       self.fechar_programa, escrita=True, grupo="pc", confirmar=False, **_DIRETA),
            Ferramenta("site_abrir", "Abre um site no navegador, ou uma busca no Google se não for endereço.",
                       esquema(["endereco"], endereco=texto("Endereço (ex.: youtube.com) ou o que buscar")),
                       self.abrir_site, escrita=True, grupo="pc", confirmar=False, **_DIRETA),
            Ferramenta("volume", "Muda o volume do PC.",
                       esquema(["acao"], acao={"type": "string", "enum": ["definir", "aumentar", "diminuir", "mudo",
                                                                          "som"]},
                               nivel=numero("Para 'definir': 0 a 100. Para aumentar/diminuir: quanto (padrão 10)")),
                       self.mudar_volume, escrita=True, grupo="pc", confirmar=False, confirmar_se_externo=True,
                       prazo_s=10),
            Ferramenta("midia", "Controla o que está tocando no PC (qualquer player): tocar/pausar, próxima, anterior.",
                       esquema(["acao"], acao={"type": "string", "enum": ["tocar_pausar", "proxima", "anterior",
                                                                          "parar"]}),
                       self.controlar_midia, escrita=True, grupo="pc", confirmar=False, prazo_s=10),
            Ferramenta("pc_travar", "Trava a tela do PC (pede a senha do Windows para voltar).", esquema([]),
                       self.travar, escrita=True, grupo="pc", confirmar=False, confirmar_se_externo=True,
                       prazo_s=10),
            Ferramenta("pc_energia", "Desliga, reinicia ou suspende o PC.",
                       esquema(["acao"], acao={"type": "string", "enum": ["desligar", "reiniciar", "suspender"]}),
                       self.energia, escrita=True, sensivel=True, sempre_confirmar=True,
                       descrever=self.descrever_energia, grupo="pc", prazo_s=PRAZO_PC_S),
            Ferramenta("pc_cancelar_desligamento", "Cancela um desligamento ou reinício agendado.", esquema([]),
                       self.cancelar_energia, escrita=True, grupo="pc", confirmar=False, prazo_s=PRAZO_PC_S),
            Ferramenta("comando_rodar",
                       "Roda um comando do PowerShell no PC. Só quando nenhuma outra ferramenta serve. O Felipe "
                       "confirma antes de rodar.",
                       esquema(["comando", "motivo"],
                               comando=texto("O comando do PowerShell, completo. Pastas do Felipe: $env:USERPROFILE "
                                             "(ex.: $env:USERPROFILE/Desktop), nunca um caminho inventado"),
                               motivo=texto("Para que serve, em poucas palavras")),
                       self.rodar_comando, escrita=True, sempre_confirmar=True, descrever=self.descrever_comando,
                       devolve_saida=True, grupo="pc", prazo_s=PRAZO_COMANDO_S + 15),
        ]


# ---------------------------------------------------------------------- comandos curtos (sem o modelo)

_INICIO = r"^(?:(?:vision|visao|hey|ei|ok|pode|por favor|ai|e|entao|agora|ja)\s+)*"
# Verbo ambíguo ("continua", "pula", "passa", "play") só com o objeto: "continua" sozinho é pedir para ele seguir
# falando, não apertar play (revisão do PR 20). "pausa" e "despausa" bastam sozinhos.
_OBJETO = r"(?:\s+(?:a|o|essa|esta))?\s+(?:musica|som|video|spotify|faixa)"
_FIM = r"(?:\s+(?:por favor|pf))?$"
_MIDIA = [
    (re.compile(_INICIO + r"(?:pausa|pause|pausar|despausa|despausar)(?:" + _OBJETO + r")?" + _FIM), "tocar_pausar"),
    (re.compile(_INICIO + r"(?:continua|continuar|play|da play|solta)" + _OBJETO + _FIM), "tocar_pausar"),
    (re.compile(_INICIO + r"(?:proxima|pula|pular|passa|passar|avanca)" + _OBJETO + _FIM), "proxima"),
    (re.compile(_INICIO + r"(?:volta|voltar|anterior)(?:\s+(?:a|pra|para))?(?:\s+(?:musica|faixa))"
                r"(?:\s+anterior)?(?:\s+(?:por favor|pf))?$"), "anterior"),
]
_VOLUME_NIVEL = re.compile(_INICIO + r"(?:(?:coloca|poe|bota|deixa|muda)\s+(?:o\s+)?)?volume\s+(?:em|no|pra|para|a)?\s*"
                           r"(\d{1,3})(?:\s+por cento)?$")
_VOLUME_SOBE = re.compile(_INICIO + r"(?:aumenta|aumentar|sobe|subir)\s+(?:o\s+)?(?:volume|som)(?:\s+(?:por favor|pf))?$")
_VOLUME_DESCE = re.compile(_INICIO + r"(?:abaixa|abaixar|diminui|diminuir|baixa|baixar)\s+(?:o\s+)?(?:volume|som)"
                           r"(?:\s+(?:por favor|pf))?$")
_MUDO = re.compile(_INICIO + r"(?:muta|mutar|silencia|silenciar|tira o som|coloca no mudo|poe no mudo)$")


def comando_de_pc(texto: str) -> tuple[str, dict[str, Any]] | None:
    """"pausa a música" → ("midia", {"acao": "tocar_pausar"}); "volume 30" → ("volume", ...). None = não é."""
    t = normalizar(str(texto).replace("%", " por cento "))
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", t)).strip()
    if not t or "nao" in t.split():
        return None
    for padrao, acao in _MIDIA:
        if padrao.match(t):
            return "midia", {"acao": acao}
    if m := _VOLUME_NIVEL.match(t):
        nivel = int(m.group(1))
        return ("volume", {"acao": "definir", "nivel": nivel}) if nivel <= 100 else None
    if _VOLUME_SOBE.match(t):
        return "volume", {"acao": "aumentar"}
    if _VOLUME_DESCE.match(t):
        return "volume", {"acao": "diminuir"}
    if _MUDO.match(t):
        return "volume", {"acao": "mudo"}
    return None
