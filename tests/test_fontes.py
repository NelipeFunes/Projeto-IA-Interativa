"""Os fontes não têm caractere de controle escondido.

Visto em 01/10: um script de edição gravou um backspace de verdade (0x08) no lugar de `\b` dentro de dois
regex. Os regex continuavam válidos e só nunca casavam.
"""

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PERMITIDOS = {9, 10, 13}  # tab, \n, \r


def test_sem_caractere_de_controle_nos_fontes():
    ruins = []
    for pasta in ("src", "tests", "scripts"):
        for f in (RAIZ / pasta).rglob("*.py"):
            if any(b < 32 and b not in PERMITIDOS for b in f.read_bytes()):
                ruins.append(str(f.relative_to(RAIZ)))
    assert ruins == []
