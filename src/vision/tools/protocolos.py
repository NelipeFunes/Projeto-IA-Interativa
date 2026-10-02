"""Protocolos: uma frase dispara várias ações, como o "House Party Protocol" do JARVIS.

Ficam em data/protocolos.yaml (fora do git: dizem como é a casa e a rotina de quem usa). Exemplo:

    boa noite:
      frases: [boa noite, vou dormir]
      passos:
        - musica_controlar: {acao: pausar}
        - luz_apagar: {luz: quarto}
        - volume: {acao: definir, nivel: 20}
      dizer: Boa noite, Felipe.

Cada passo é uma ferramenta que já existe, rodada pelo registro como se o modelo a tivesse chamado. Ferramenta
sensível (apagar evento, desligar o PC, comando do PowerShell...) não pode ser passo: ela precisa de um "sim", e
um protocolo roda de uma vez. Um passo que falha não para os outros; a resposta conta o que deu e o que não deu.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from vision.tools.base import ErroFerramenta, Ferramenta, Registro, esquema, texto
from vision.tools.pc import normalizar

log = logging.getLogger(__name__)

MAX_PASSOS = 12
EXEMPLO = """\
# Protocolos do Vision: uma frase, várias ações (como o "House Party Protocol" do JARVIS).
# Cada passo é uma ferramenta do Vision com seus argumentos. Ferramentas que pedem "sim" (apagar, desligar o PC,
# comando do PowerShell) não podem ser passo. Depois de mudar, reinicie o Vision.
# Para usar: diga uma das frases, ou "protocolo <nome>" / "modo <nome>".

boa noite:
  frases: [boa noite vision, vou dormir]
  passos:
    - musica_controlar: {acao: pausar}
    - volume: {acao: definir, nivel: 15}
  dizer: Boa noite, Felipe. Pausei a música e baixei o volume.

foco:
  frases: [modo foco, hora de focar]
  passos:
    - musica_tocar: {busca: lofi focus, tipo: playlist}
    - volume: {acao: definir, nivel: 25}
  dizer: Modo foco ligado. Bom trabalho.
"""


def limpar(frase: str) -> str:
    """"Vision, boa noite!" → "boa noite": sem acento, pontuação nem o nome do assistente nas pontas."""
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?;:]", " ", normalizar(frase))).strip()
    t = re.sub(r"^(?:(?:vision|visao|hey|ei|ok)\s+)+", "", t)
    return re.sub(r"(?:\s+(?:vision|visao|por favor|pf))+$", "", t)


class Protocolo:
    def __init__(self, nome: str, passos: list[tuple[str, dict[str, Any]]], frases: list[str], dizer: str = ""):
        self.nome = nome
        self.passos = passos
        self.frases = frases
        self.dizer = dizer


def ler(arquivo: Path, registro: Registro) -> tuple[dict[str, Protocolo], list[str]]:
    """(protocolos válidos, problemas para o log). Arquivo ausente: nenhum protocolo."""
    if not arquivo.exists():
        return {}, []
    try:
        dados = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return {}, [f"protocolos.yaml ilegível: {e}"]
    if not isinstance(dados, dict):
        return {}, ["protocolos.yaml precisa ser um mapa de nome → protocolo"]
    protocolos: dict[str, Protocolo] = {}
    problemas: list[str] = []
    for nome, corpo in dados.items():
        nome = " ".join(str(nome).split())[:40]
        if not isinstance(corpo, dict) or not isinstance(corpo.get("passos"), list):
            problemas.append(f"protocolo '{nome}': falta a lista de passos")
            continue
        passos: list[tuple[str, dict[str, Any]]] = []
        for passo in corpo["passos"][:MAX_PASSOS]:
            if not (isinstance(passo, dict) and len(passo) == 1):
                problemas.append(f"protocolo '{nome}': passo inválido {passo!r:.60}")
                continue
            ferramenta, args = next(iter(passo.items()))
            args = args if isinstance(args, dict) else {}
            f = registro.get(str(ferramenta))
            if f is None:
                problemas.append(f"protocolo '{nome}': a ferramenta '{ferramenta}' não existe (ou está desligada)")
                continue
            if f.sensivel or f.sempre_confirmar:
                problemas.append(f"protocolo '{nome}': '{ferramenta}' pede confirmação e não pode ser passo")
                continue
            passos.append((f.nome, args))
        if not passos:
            problemas.append(f"protocolo '{nome}': nenhum passo válido")
            continue
        frases = [f for x in corpo.get("frases") or [] if (f := limpar(str(x)))]
        protocolos[normalizar(nome)] = Protocolo(nome, passos, frases, " ".join(str(corpo.get("dizer") or "").split()))
    return protocolos, problemas


class Protocolos:
    def __init__(self, arquivo: Path, registro: Registro):
        self.arquivo = arquivo
        self.registro = registro
        if not arquivo.exists():
            try:
                arquivo.parent.mkdir(parents=True, exist_ok=True)
                arquivo.write_text(EXEMPLO, encoding="utf-8", newline="")
            except OSError as e:
                log.warning("não deu para criar o exemplo de protocolos: %s", e)
        self.protocolos, problemas = ler(arquivo, registro)
        for p in problemas:
            log.warning(p)

    def achar(self, nome: str) -> Protocolo | None:
        n = limpar(nome)
        n = re.sub(r"^(?:o |a )?(?:protocolo|modo|rotina)\s+", "", n)
        return self.protocolos.get(n) or next((p for p in self.protocolos.values() if n in p.frases), None)

    async def rodar(self, p: Protocolo) -> tuple[str, bool]:
        feitos, falhas = [], []
        for ferramenta, args in p.passos:
            ok, resultado, _dados = await self.registro.rodar_com_dados(ferramenta, dict(args))
            (feitos if ok else falhas).append((ferramenta, resultado))
            if not ok:
                log.warning("protocolo %s: %s falhou: %s", p.nome, ferramenta, resultado[:200])
        if not falhas:
            return (p.dizer or f"Protocolo {p.nome} feito."), True
        nomes = ", ".join(f for f, _ in falhas)
        inicio = p.dizer + " " if p.dizer and feitos else ""
        return f"{inicio}Protocolo {p.nome}: {len(feitos)} de {len(p.passos)} passos feitos; falhou: {nomes}.", False

    async def executar(self, args: dict[str, Any]) -> str:
        p = self.achar(str(args.get("nome") or ""))
        if p is None:
            existentes = ", ".join(x.nome for x in self.protocolos.values()) or "nenhum"
            raise ErroFerramenta(f"Esse protocolo não existe. Os que existem: {existentes}.")
        resposta, _ok = await self.rodar(p)
        return resposta

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """A frase do protocolo (ou "protocolo X", "modo X") roda direto, sem o modelo."""
        t = limpar(frase)
        p = next((x for x in self.protocolos.values() if t in x.frases), None)
        if p is None:
            m = re.fullmatch(r"(?:(?:ativa|ativar|liga|ligar|inicia|iniciar|executa|executar|roda|rodar)\s+)?"
                             r"(?:o |a )?(?:protocolo|modo|rotina)\s+(.+)", t)
            p = self.protocolos.get(m.group(1)) if m else None
        if p is None:
            return None
        resposta, ok = await self.rodar(p)
        return "protocolo_executar", {"nome": p.nome}, resposta, ok

    def ferramentas(self) -> list[Ferramenta]:
        if not self.protocolos:
            return []
        nomes = ", ".join(p.nome for p in self.protocolos.values())
        return [Ferramenta(
            "protocolo_executar",
            f"Roda um protocolo do Felipe (várias ações de uma vez). Protocolos: {nomes}.",
            esquema(["nome"], nome=texto("Nome do protocolo")),
            self.executar, escrita=True, confirmar=False, confirmar_se_externo=True, grupo="protocolo", prazo_s=90,
        )]
