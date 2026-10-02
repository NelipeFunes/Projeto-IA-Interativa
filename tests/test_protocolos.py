"""Protocolos: uma frase roda várias ferramentas; nada que precise de "sim" pode ser passo."""

from __future__ import annotations

import pytest

from vision.tools.base import ErroFerramenta, Ferramenta, Registro, esquema, texto
from vision.tools.protocolos import EXEMPLO, Protocolos, ler


def _registro(chamadas, quebra=()):
    r = Registro()

    def fazer(nome):
        async def executar(args):
            chamadas.append((nome, args))
            if nome in quebra:
                raise ErroFerramenta("deu ruim")
            return f"{nome} ok"

        return executar

    for nome in ("luz_apagar", "musica_controlar", "volume"):
        r.adicionar(Ferramenta(nome, nome, esquema([], acao=texto("a"), luz=texto("l"), nivel=texto("n")), fazer(nome),
                               escrita=True, confirmar=False))
    r.adicionar(Ferramenta("pc_energia", "desliga", esquema([]), fazer("pc_energia"), escrita=True, sensivel=True))
    r.adicionar(Ferramenta("comando_rodar", "ps", esquema([]), fazer("comando_rodar"), escrita=True,
                           sempre_confirmar=True))
    return r


YAML = """
boa noite:
  frases: [Boa noite, Vision!, vou dormir]
  passos:
    - musica_controlar: {acao: pausar}
    - luz_apagar: {luz: quarto}
    - pc_energia: {acao: desligar}
    - comando_rodar: {comando: "Remove-Item C:/ -Recurse"}
    - nao_existe: {}
  dizer: Boa noite, Felipe.
vazio:
  passos:
    - pc_energia: {acao: desligar}
"""


def _protocolos(tmp_path, chamadas, quebra=()):
    arq = tmp_path / "protocolos.yaml"
    arq.write_text(YAML, encoding="utf-8")
    return Protocolos(arq, _registro(chamadas, quebra))


async def test_frase_roda_os_passos_e_pula_os_sensiveis(tmp_path):
    chamadas = []
    p = _protocolos(tmp_path, chamadas)
    assert set(p.protocolos) == {"boa noite"}  # "vazio" só tinha passo sensível
    feito = await p.atalho("Vision, boa noite!")
    assert feito == ("protocolo_executar", {"nome": "boa noite"}, "Boa noite, Felipe.", True)
    assert chamadas == [("musica_controlar", {"acao": "pausar"}), ("luz_apagar", {"luz": "quarto"})]
    assert await p.atalho("vou dormir vision") is not None
    assert await p.atalho("ativa o protocolo boa noite") is not None
    assert await p.atalho("boa noite pra você também, que dia longo") is None


def test_problemas_ficam_no_log(tmp_path):
    arq = tmp_path / "protocolos.yaml"
    arq.write_text(YAML, encoding="utf-8")
    _, problemas = ler(arq, _registro([]))
    texto = "\n".join(problemas)
    assert "pc_energia" in texto and "comando_rodar" in texto and "nao_existe" in texto


async def test_passo_que_falha_nao_para_os_outros(tmp_path):
    chamadas = []
    p = _protocolos(tmp_path, chamadas, quebra=("musica_controlar",))
    nome, _args, resposta, ok = await p.atalho("boa noite")
    assert not ok and "1 de 2 passos feitos; falhou: musica_controlar" in resposta
    assert [c[0] for c in chamadas] == ["musica_controlar", "luz_apagar"]


async def test_ferramenta_e_protocolo_inexistente(tmp_path):
    p = _protocolos(tmp_path, [])
    f = p.ferramentas()[0]
    assert f.nome == "protocolo_executar" and "boa noite" in f.descricao and f.confirmar_se_externo
    assert await p.executar({"nome": "o protocolo boa noite"}) == "Boa noite, Felipe."
    with pytest.raises(ErroFerramenta, match="não existe"):
        await p.executar({"nome": "festa"})


def test_cria_o_exemplo_na_primeira_vez_e_arquivo_ruim_nao_quebra(tmp_path):
    arq = tmp_path / "data" / "protocolos.yaml"
    p = Protocolos(arq, _registro([]))
    assert arq.read_text(encoding="utf-8") == EXEMPLO
    assert "boa noite" in p.protocolos  # os passos que existem neste registro (volume, música) ficam
    arq.write_text("[isso: nao é: yaml", encoding="utf-8")
    assert Protocolos(arq, _registro([])).ferramentas() == []
