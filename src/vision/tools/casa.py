"""Luzes da casa pela Alexa (vision/alexa.py), em ferramentas simples.

Só luzes da lista que a Alexa devolveu no login (data/alexa/luzes.json): o modelo escolhe pelo nome, e
um nome que não está na lista é recusado. Nada de texto livre para a Alexa (ela também compra coisas).
Acender e apagar são escrita: passam pela confirmação, como a agenda.
"""

from __future__ import annotations

import difflib
import re
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


# Comando curto de luz, entendido sem o modelo (ele esquece de chamar a ferramenta e "lembra" do estado).
VERBOS = {"acende": True, "acenda": True, "acender": True, "liga": True, "ligue": True, "ligar": True,
          "apaga": False, "apague": False, "apagar": False, "desliga": False, "desligue": False, "desligar": False}
ANTES_DO_VERBO = {"vision", "visao", "visium", "hey", "ei", "oi", "pode", "por", "favor", "ai", "e", "entao",
                  "agora", "ja", "beleza", "ok", "okay", "vai", "me", "faz", "o", "favor"}
NAO_E_COMANDO = {"nao", "lembra", "lembre", "lembrar", "amanha", "depois", "quando", "daqui", "minuto", "minutos",
                 "hora", "horas", "se", "porque", "pq", "como", "qual", "quais", "deveria", "devo", "tarde", "noite",
                 "logo", "antes", "enquanto", "ate", "manha", "semana", "segundos", "dormir", "sair", "chegar"}
SOBRA = {"a", "o", "as", "os", "luz", "luzes", "lampada", "lampadas", "do", "da", "de", "dos", "das", "em", "com",
         "brilho", "pra", "para", "mim", "pf", "pfv", "por", "favor", "porfavor", "aqui", "agora", "ai", "ja", "no", "na"}


def comando_de_luz(texto: str) -> tuple[bool, str, int | None] | None:
    """"liga a luz do quarto 2 em 30%" → (True, "quarto 2", 30). None se não for um comando curto e claro."""
    t = normalizar(re.sub(r"[^\w%\s]", " ", str(texto)))
    brilho = None
    if m := re.search(r"\b(\d{1,3})\s*(%|por cento)", t):
        brilho = int(m.group(1))
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    palavras = t.split()
    if not palavras or len(palavras) > 10 or NAO_E_COMANDO & set(palavras) or re.search(r"\b\d+\s*h\b|\bas \d", t):
        return None
    i = 0
    while i < len(palavras) and palavras[i] in ANTES_DO_VERBO:
        i += 1
    if i >= len(palavras) or palavras[i] not in VERBOS:
        return None
    ligar = VERBOS[palavras[i]]
    resto = [p for p in palavras[i + 1:] if p not in SOBRA]
    if brilho is not None and not ligar:
        return None  # "apaga em 30%" não faz sentido: deixa o modelo perguntar
    alvo = " ".join(resto)
    tem_luz = bool({"luz", "luzes", "lampada", "lampadas", "abajur"} & set(palavras)) or brilho is not None
    if not alvo and not tem_luz:
        return None  # "desliga", "me desliga": sem dizer o quê, não apaga a casa
    return ligar, ("todas" if alvo in {"tudo", "todas", "todos", "toda casa", "casa toda", "casa"} else alvo), brilho


def _chaves(texto: str) -> set[str]:
    palavras = (TRADUCAO.get(p, p) for p in normalizar(texto).split())
    return {p for p in palavras if p and p not in VAZIAS}


class Casa:
    def __init__(self, alexa: Alexa, confirmar: bool = True):
        self.alexa = alexa
        self.confirmar = confirmar

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
            if len(feitas) > 1 and len(feitas) == len(self.alexa.luzes()):
                quais = "todas as luzes"  # falado soa melhor que a lista de nomes
            elif len(feitas) > 1:
                quais = f"as {len(feitas)} luzes ({', '.join(feitas)})"
            else:
                quais = feitas[0]
            texto_ok = f"{verbo} {quais}{extra}."
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

    async def atalho(self, texto: str) -> tuple[str, dict[str, Any], str, bool] | None:
        """Comando curto de luz sem passar pelo modelo: (ferramenta, args, resposta, ok). None = o modelo decide.

        Só sem confirmação (com `confirmar`, o caminho normal é que pergunta). Um nome que não casa com nenhuma
        luz também volta para o modelo, que pode perguntar qual."""
        if self.confirmar:
            return None
        cmd = comando_de_luz(texto)
        if cmd is None:
            return None
        ligar, luz, brilho = cmd
        try:
            self._escolher(luz)
        except ErroFerramenta:
            return None
        args: dict[str, Any] = {"luz": luz} if not brilho else {"luz": luz, "brilho": brilho}
        nome = "luz_acender" if ligar else "luz_apagar"
        try:
            return nome, args, await self._mudar(ligar)(args), True
        except ErroFerramenta as e:
            return nome, args, str(e), False

    def ferramentas(self) -> list[Ferramenta]:
        luz = texto("Nome da luz ou do lugar (ex.: 'quarto'), ou 'todas'. Sem número = todas as lâmpadas com esse "
                    "nome; com número = só aquela. Repasse o que o Felipe disse")
        return [
            Ferramenta("luzes_listar", "Lista as luzes da casa que o Vision controla.", esquema([]), self.listar,
                       grupo="casa"),
            Ferramenta("luz_acender", "Acende uma luz da casa (pela Alexa), opcionalmente com brilho.",
                       esquema([], luz=luz, brilho=numero("Brilho de 1 a 100, opcional")),
                       self._mudar(True), escrita=True, descrever=self._descrever(True), grupo="casa",
                       confirmar=self.confirmar),
            Ferramenta("luz_apagar", "Apaga uma luz da casa (pela Alexa).", esquema([], luz=luz),
                       self._mudar(False), escrita=True, descrever=self._descrever(False), grupo="casa",
                       confirmar=self.confirmar),
        ]
