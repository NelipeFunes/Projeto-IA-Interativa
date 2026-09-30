"""Ferramentas de memória para o modelo: lembrar, buscar e esquecer."""

from __future__ import annotations

from typing import Any

from jarvis.memory.store import Memorias
from jarvis.tools.base import ErroFerramenta, Ferramenta, esquema, texto

CATEGORIAS = ["rotina", "preferencia", "pessoa", "meta", "saude", "estudo", "trabalho", "financas", "geral"]


class FerramentasMemoria:
    def __init__(self, memorias: Memorias):
        self.m = memorias

    async def lembrar(self, args: dict[str, Any]) -> str:
        fato = (args.get("fato") or "").strip()
        if len(fato) < 4:
            raise ErroFerramenta("Diga o fato a guardar, numa frase completa.")
        categoria = args.get("categoria") or "geral"
        if categoria not in CATEGORIAS:
            categoria = "geral"
        tipo, mem = await self.m.lembrar(fato, categoria)
        return f"Memória {'atualizada' if tipo == 'atualizada' else 'guardada'} [{mem.id}]: {mem.texto}"

    async def buscar(self, args: dict[str, Any]) -> str:
        consulta = (args.get("consulta") or "").strip()
        achadas = await self.m.buscar(consulta, k=6, minimo=0.22)
        if not achadas:
            return "Nenhuma memória relacionada."
        return "Memórias encontradas:\n" + Memorias.formatar(achadas)

    async def _alvo(self, args: dict[str, Any]):
        consulta = (args.get("consulta") or "").strip()
        achadas = await self.m.buscar(consulta, k=1, minimo=0.35)
        if not achadas:
            raise ErroFerramenta(f"Não achei memória parecida com '{consulta}'.")
        return achadas[0]

    async def esquecer(self, args: dict[str, Any]) -> str:
        alvo = await self._alvo(args)
        await self.m.esquecer(alvo.id)
        return f"Memória apagada: {alvo.texto}"

    async def descrever_esquecer(self, args: dict[str, Any]) -> str:
        alvo = await self._alvo(args)
        return f"Vou esquecer isto: \"{alvo.texto}\"."

    def ferramentas(self) -> list[Ferramenta]:
        return [
            Ferramenta(
                "guardar_memoria",
                "Guarda um FATO duradouro sobre o Felipe (rotina, preferência, pessoa, meta, saúde, estudo). "
                "Use quando ele disser 'lembra que eu...', 'anota que eu...' ou contar algo que vale para o futuro. "
                "NÃO use para lembretes ou coisas a fazer ('me lembra de pagar X'): isso é tarefas_criar.",
                esquema(
                    ["fato"],
                    fato=texto("O fato em uma frase completa, em terceira pessoa: 'O Felipe ...'"),
                    categoria={"type": "string", "enum": CATEGORIAS},
                ),
                self.lembrar,
                grupo="memoria",
            ),
            Ferramenta(
                "buscar_memoria",
                "Procura na memória fatos sobre o Felipe que não estejam já no contexto.",
                esquema(["consulta"], consulta=texto("O que procurar")),
                self.buscar,
                grupo="memoria",
            ),
            Ferramenta(
                "esquecer",
                "Apaga da memória um fato que o Felipe pediu para esquecer ou que ficou errado.",
                esquema(["consulta"], consulta=texto("Descrição do fato a esquecer")),
                self.esquecer,
                escrita=True,
                descrever=self.descrever_esquecer,
                grupo="memoria",
            ),
        ]
