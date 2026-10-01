"""Luzes da casa pela Alexa (vision/alexa.py), em ferramentas simples.

Só luzes da lista que a Alexa devolveu no login (data/alexa/luzes.json): o modelo escolhe pelo nome, e
um nome que não está na lista é recusado. Nada de texto livre para a Alexa (ela também compra coisas).
Acender e apagar são escrita: passam pela confirmação, como a agenda.
"""

from __future__ import annotations

import difflib
from typing import Any

from vision.alexa import Alexa, SemLogin, normalizar
from vision.tools.base import ErroFerramenta, Ferramenta, esquema, numero, texto

TODAS = {"todas", "todas as luzes", "tudo", "casa toda", "a casa toda"}


class Casa:
    def __init__(self, alexa: Alexa):
        self.alexa = alexa

    def _escolher(self, nome: Any) -> list[dict[str, str]]:
        luzes = self.alexa.luzes()
        if not luzes:
            raise ErroFerramenta("Nenhuma luz conhecida. Diga ao Felipe para rodar `vision alexa-login`.")
        pedido = normalizar(nome or "")
        if pedido in TODAS:
            return luzes
        if not pedido:
            if len(luzes) == 1:
                return luzes
            raise ErroFerramenta("Qual luz? As que existem: " + ", ".join(x["nome"] for x in luzes) + ".")
        por_nome = {normalizar(x["nome"]): x for x in luzes}
        if pedido in por_nome:
            return [por_nome[pedido]]
        contem = [x for n, x in por_nome.items() if pedido in n or n in pedido]
        if len(contem) == 1:
            return contem
        parecido = difflib.get_close_matches(pedido, list(por_nome), n=1, cutoff=0.6)
        if parecido and not contem:
            return [por_nome[parecido[0]]]
        raise ErroFerramenta(f"Não achei a luz '{nome}'. As que existem: " + ", ".join(x["nome"] for x in luzes) + ".")

    @staticmethod
    def _brilho(args: dict[str, Any]) -> int | None:
        b = args.get("brilho")
        if b in (None, ""):
            return None
        try:
            v = int(float(str(b).replace("%", "")))
        except (ValueError, OverflowError) as e:
            raise ErroFerramenta("Brilho de 1 a 100.") from e
        if not 1 <= v <= 100:
            raise ErroFerramenta("Brilho de 1 a 100.")
        return v

    # ---------- ferramentas ----------

    async def listar(self, _args: dict[str, Any]) -> str:
        luzes = self.alexa.luzes()
        if not luzes:
            return "Nenhuma luz ligada ao Vision ainda (o Felipe precisa rodar `vision alexa-login`)."
        return "Luzes que o Vision controla (pela Alexa): " + ", ".join(x["nome"] for x in luzes) + "."

    def _mudar(self, ligar: bool):
        async def executar(args: dict[str, Any]) -> str:
            alvo = self._escolher(args.get("luz"))
            brilho = self._brilho(args) if ligar else None
            feitas, falhas = [], []
            for luz in alvo:
                try:
                    await self.alexa.mudar_luz(luz["entity_id"], ligar, brilho)
                    feitas.append(luz["nome"])
                except SemLogin as e:
                    raise ErroFerramenta(f"Alexa: {e}") from e
                except Exception as e:  # noqa: BLE001
                    falhas.append(f"{luz['nome']} ({str(e)[:120]})")
                    if not feitas:
                        break  # a Alexa não respondeu nem à primeira: não refaz o login luz por luz
            if not feitas:
                raise ErroFerramenta("A Alexa não respondeu: " + "; ".join(falhas))
            verbo = "Acendi" if ligar else "Apaguei"
            extra = f" em {brilho}%" if brilho else ""
            texto_ok = f"{verbo} {', '.join(feitas)}{extra}."
            return texto_ok + (f" Não consegui: {'; '.join(falhas)}." if falhas else "")

        return executar

    def _descrever(self, ligar: bool):
        async def descrever(args: dict[str, Any]) -> str:
            alvo = self._escolher(args.get("luz"))  # nome errado vira erro já aqui, antes de pedir confirmação
            nomes = "todas as luzes" if len(alvo) > 1 else alvo[0]["nome"]
            brilho = self._brilho(args) if ligar else None
            return f"Vou {'acender' if ligar else 'apagar'} {nomes}{f' em {brilho}%' if brilho else ''}."

        return descrever

    def ferramentas(self) -> list[Ferramenta]:
        luz = texto("Nome da luz como está na lista (ou 'todas')")
        return [
            Ferramenta("luzes_listar", "Lista as luzes da casa que o Vision controla.", esquema([]), self.listar,
                       grupo="casa"),
            Ferramenta("luz_acender", "Acende uma luz da casa (pela Alexa), opcionalmente com brilho.",
                       esquema([], luz=luz, brilho=numero("Brilho de 1 a 100, opcional")),
                       self._mudar(True), escrita=True, descrever=self._descrever(True), grupo="casa"),
            Ferramenta("luz_apagar", "Apaga uma luz da casa (pela Alexa).", esquema([], luz=luz),
                       self._mudar(False), escrita=True, descrever=self._descrever(False), grupo="casa"),
        ]
