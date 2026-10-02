"""Qual volume mexer: o do Spotify ou o do Windows (pedido de 02/10).

"Ele deve sempre tentar mexer no Spotify; se o volume do Spotify estiver no máximo e o do Windows não, aí ele sobe
o do Windows. Ele deve ter essa inteligência para averiguar o que faz sentido." Por isso a escolha é do código,
olhando os dois volumes, e não do modelo:

- Spotify tocando (e o aparelho deixa mudar o volume): "aumenta" sobe o Spotify; o que passar de 100 vai para o
  Windows, e com o Spotify já em 100 sobe só o Windows. "Abaixa" desce o Spotify; com ele já em 0, desce o Windows.
  "Volume em 30" põe o Spotify em 30.
- Spotify parado, fechado ou sem controle de volume: o Windows, como antes.
- Mudo e "volta o som": sempre o Windows. "Volume do PC/Windows" (onde="pc"): só o Windows.
"""

from __future__ import annotations

from dataclasses import dataclass

PASSO_PADRAO = 10


@dataclass(frozen=True)
class Plano:
    spotify: int | None = None  # nível final do Spotify (0 a 100), se mexer nele
    windows: tuple[str, float | None] | None = None  # (acao, nivel) para o volume do Windows
    tirar_mudo: bool = False  # o Windows estava mudo: "aumenta o volume" também tira do mudo
    motivo: str = ""  # para a frase da resposta ("o Spotify já estava no máximo")


def planejar(acao: str, nivel: float | None, *, onde: str, spotify: dict | None, windows: int,
             windows_mudo: bool) -> Plano:
    """`spotify`: {"volume", "tocando"} do aparelho ativo, ou None. `windows`: nível atual em %."""
    if acao in ("mudo", "som") or onde == "pc":
        return Plano(windows=(acao, nivel))
    usa_spotify = spotify is not None and (spotify.get("tocando") or onde == "spotify")
    if not usa_spotify:
        return Plano(windows=(acao, nivel), motivo="" if onde != "spotify" else "sem_spotify")
    atual = int(spotify["volume"])
    if acao == "definir":
        return Plano(spotify=int(max(0, min(100, nivel or 0))))
    passo = PASSO_PADRAO if nivel is None else max(0.0, float(nivel))
    if acao == "aumentar":
        if atual >= 100 and onde == "spotify":
            return Plano(motivo="spotify_ja_no_maximo")  # "volume do Spotify": não é para mexer no Windows
        if atual >= 100:
            if windows >= 100 and not windows_mudo:
                return Plano(motivo="tudo_no_maximo")
            return Plano(windows=("aumentar", passo), tirar_mudo=windows_mudo, motivo="spotify_no_maximo")
        sobra = atual + passo - 100
        if sobra > 0 and windows < 100 and onde != "spotify":
            return Plano(spotify=100, windows=("aumentar", sobra), tirar_mudo=windows_mudo)
        return Plano(spotify=int(min(100, atual + passo)), tirar_mudo=windows_mudo)
    if acao == "diminuir":
        if atual <= 0 and onde == "spotify":
            return Plano(motivo="spotify_ja_no_zero")
        if atual <= 0:
            return Plano(windows=("diminuir", passo), motivo="spotify_no_zero")
        return Plano(spotify=int(max(0, atual - passo)))
    return Plano(windows=(acao, nivel))
