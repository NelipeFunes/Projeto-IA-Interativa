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
# A Alexa costuma nomear em inglês ("Bedroom Light"): o pedido em português casa com essas palavras.
TRADUCAO = {"bedroom": "quarto", "living": "sala", "kitchen": "cozinha", "bathroom": "banheiro",
            "office": "escritorio", "hall": "corredor", "dining": "jantar", "lamp": "abajur", "desk": "mesa",
            "garage": "garagem", "front": "frente", "back": "fundos", "porch": "varanda", "balcony": "varanda"}
# A tradução vem antes do filtro: "lamp" vira "abajur" e não é descartada como palavra vazia.
VAZIAS = {"a", "o", "as", "os", "do", "da", "de", "dos", "das", "no", "na", "nos", "nas", "the", "room", "luz",
          "luzes", "lampada", "lampadas", "light", "lights", "lamp", "lampadinha", "meu", "minha", "e"}


def _chaves(texto: str) -> set[str]:
    palavras = (TRADUCAO.get(p, p) for p in normalizar(texto).split())
    return {p for p in palavras if p and p not in VAZIAS}


class Casa:
    def __init__(self, alexa: Alexa):
        self.alexa = alexa

    def _rotuladas(self) -> list[dict[str, str]]:
        """As luzes com um rótulo único: nomes repetidos na Alexa viram "Bedroom Light 1", "Bedroom Light 2"."""
        luzes = self.alexa.luzes()
        contagem: dict[str, int] = {}
        for x in luzes:
            contagem[x["nome"]] = contagem.get(x["nome"], 0) + 1
        vistos: dict[str, int] = {}
        saida = []
        for x in luzes:
            if contagem[x["nome"]] > 1:
                vistos[x["nome"]] = vistos.get(x["nome"], 0) + 1
                saida.append({**x, "rotulo": f"{x['nome']} {vistos[x['nome']]}"})
            else:
                saida.append({**x, "rotulo": x["nome"]})
        return saida

    def _escolher(self, nome: Any) -> list[dict[str, str]]:
        luzes = self._rotuladas()
        if not luzes:
            raise ErroFerramenta("Nenhuma luz conhecida. Diga ao Felipe para rodar `vision alexa-login`.")
        lista = ", ".join(x["rotulo"] for x in luzes)
        pedido = normalizar(nome or "")
        if pedido in TODAS:
            return luzes
        por_rotulo = {normalizar(x["rotulo"]): x for x in luzes}
        if pedido in por_rotulo:
            return [por_rotulo[pedido]]
        grupos = {x["nome"] for x in luzes}
        numeros = [int(t) for t in pedido.split() if t.isdigit()]
        chave = _chaves(pedido) - {str(n) for n in numeros}
        if not chave:  # "acende a luz": se só existe um grupo de luzes, é ele
            if len(grupos) == 1:
                escolhidas = luzes
            else:
                raise ErroFerramenta(f"Qual luz? As que existem: {lista}.")
        else:
            escolhidas = [x for x in luzes if chave <= _chaves(x["nome"])]
        mesmo_nome = bool(escolhidas) and len({x["nome"] for x in escolhidas}) == 1
        if numeros and escolhidas:
            n = numeros[0]
            if mesmo_nome and len(escolhidas) > 1:  # "quarto 2": a 2ª das lâmpadas com o mesmo nome
                if 1 <= n <= len(escolhidas):
                    return [escolhidas[n - 1]]
                raise ErroFerramenta(f"Não existe a luz número {n}. As que existem: {lista}.")
            # Nomes diferentes: o número é parte do nome ("Lamp 2"), nunca a posição na lista.
            com_numero = [x for x in escolhidas if _chaves(pedido) <= _chaves(x["nome"])]
            if len(com_numero) == 1:
                return com_numero
            raise ErroFerramenta(f"Qual delas? As que existem: {lista}.")
        if mesmo_nome:
            return escolhidas  # o mesmo nome na Alexa: as lâmpadas do mesmo lugar acendem juntas
        if escolhidas:
            raise ErroFerramenta("Qual delas? " + ", ".join(x["rotulo"] for x in escolhidas) + ".")
        if not escolhidas:
            parecido = difflib.get_close_matches(pedido, list(por_rotulo), n=1, cutoff=0.6)
            if parecido:
                return [por_rotulo[parecido[0]]]
        raise ErroFerramenta(f"Não achei a luz '{nome}'. As que existem: {lista}.")

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
        if not self.alexa.luzes():
            return "Nenhuma luz ligada ao Vision ainda (o Felipe precisa rodar `vision alexa-login`)."
        return "Luzes que o Vision controla (pela Alexa): " + self.resumo() + "."

    def resumo(self) -> str:
        """As luzes agrupadas pelo nome na Alexa: o modelo pede o grupo inteiro pelo nome, sem número."""
        grupos: dict[str, int] = {}
        for x in self.alexa.luzes():
            grupos[x["nome"]] = grupos.get(x["nome"], 0) + 1
        partes = []
        for nome, n in grupos.items():
            if n == 1:
                partes.append(f"'{nome}'")
            else:
                partes.append(f"'{nome}' ({n} lâmpadas: use '{nome}' para todas juntas, ou "
                              + " / ".join(f"'{nome} {i}'" for i in range(1, n + 1)) + " para uma só)")
        return "; ".join(partes)

    def _mudar(self, ligar: bool):
        async def executar(args: dict[str, Any]) -> str:
            alvo = self._escolher(args.get("luz"))
            brilho = self._brilho(args) if ligar else None
            feitas, falhas = [], []
            for luz in alvo:
                try:
                    await self.alexa.mudar_luz(luz["entity_id"], ligar, brilho)
                    feitas.append(luz["rotulo"])
                except SemLogin as e:
                    raise ErroFerramenta(f"Alexa: {e}") from e
                except Exception as e:  # noqa: BLE001
                    falhas.append(f"{luz['rotulo']} ({str(e)[:120]})")
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
            todas = len(alvo) == len(self.alexa.luzes()) and len(alvo) > 1
            nomes = "todas as luzes" if todas else " e ".join(x["rotulo"] for x in alvo)
            brilho = self._brilho(args) if ligar else None
            return f"Vou {'acender' if ligar else 'apagar'} {nomes}{f' em {brilho}%' if brilho else ''}."

        return descrever

    def ferramentas(self) -> list[Ferramenta]:
        luz = texto("Nome da luz ou do lugar (ex.: 'quarto'), ou 'todas'. Sem número = todas as lâmpadas com esse "
                    "nome; com número = só aquela. Repasse o que o Felipe disse")
        return [
            Ferramenta("luzes_listar", "Lista as luzes da casa que o Vision controla.", esquema([]), self.listar,
                       grupo="casa"),
            Ferramenta("luz_acender", "Acende uma luz da casa (pela Alexa), opcionalmente com brilho.",
                       esquema([], luz=luz, brilho=numero("Brilho de 1 a 100, opcional")),
                       self._mudar(True), escrita=True, descrever=self._descrever(True), grupo="casa"),
            Ferramenta("luz_apagar", "Apaga uma luz da casa (pela Alexa).", esquema([], luz=luz),
                       self._mudar(False), escrita=True, descrever=self._descrever(False), grupo="casa"),
        ]
