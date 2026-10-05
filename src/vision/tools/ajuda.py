""""O que você sabe fazer?": a lista sai das ferramentas ligadas agora, não da imaginação do modelo (ele costumava
prometer coisas que o Vision não faz, ou esquecer as que faz)."""

from __future__ import annotations

import re
from typing import Any

from vision.tools.base import Ferramenta, Registro, esquema
from vision.tools.pc import normalizar

# Grupo de ferramentas → como dizer isso em voz alta. Grupo sem entrada aqui não aparece.
GRUPOS = [
    ("agenda", "ver e mudar a sua agenda"),
    ("clima", "dizer o clima e fazer o resumo do dia"),
    ("timer", "pôr timers e alarmes"),
    ("musica", "tocar música no Spotify"),
    ("casa", "acender e apagar as luzes"),
    ("tv", "controlar a TV e tocar vídeos do YouTube nela"),
    ("pc", "abrir programas e sites, mexer no volume, travar o PC e ver o estado dele"),
    ("web", "pesquisar na internet"),
    ("memoria", "lembrar o que você me conta"),
    ("reunioes", "procurar nas suas reuniões gravadas e notas"),
    ("financas", "lançar e consultar gastos"),
    ("tarefas", "cuidar das suas tarefas"),
    ("protocolo", "rodar os seus protocolos"),
]


class Ajuda:
    def __init__(self, registro: Registro):
        self.registro = registro

    async def capacidades(self, _args: dict[str, Any] | None = None) -> str:
        coisas = [frase for grupo, frase in GRUPOS if self.registro.nomes_do_grupo(grupo)]
        if not coisas:
            return "Por enquanto, só conversar."
        lista = coisas[0] if len(coisas) == 1 else ", ".join(coisas[:-1]) + " e " + coisas[-1]
        return f"Posso {lista}. É só pedir."

    async def atalho(self, frase: str) -> tuple[str, dict[str, Any], str, bool] | None:
        if not pede_ajuda(frase):
            return None
        return "o_que_sei_fazer", {}, await self.capacidades(), True

    def ferramentas(self) -> list[Ferramenta]:
        return [Ferramenta("o_que_sei_fazer", "Diz o que o Vision consegue fazer agora (as funções ligadas).",
                           esquema([]), self.capacidades, grupo="ajuda")]


_AJUDA = re.compile(
    r"^(?:(?:vision|visao|hey|ei|ok)\s+)*(?:o que (?:voce|vc) (?:sabe|consegue|pode) fazer|"
    r"quais (?:sao )?(?:as )?suas (?:funcoes|habilidades|capacidades)|"
    r"(?:me )?(?:ajuda|help)|o que (?:voce|vc) faz)(?:\s+(?:vision|visao))?$"
)


def pede_ajuda(frase: str) -> bool:
    t = re.sub(r"\s+", " ", re.sub(r"[.,!?]", " ", normalizar(frase))).strip()
    return bool(_AJUDA.match(t))
