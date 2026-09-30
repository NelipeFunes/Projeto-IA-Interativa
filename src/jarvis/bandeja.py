"""Ícone na bandeja do Windows: um mini-orbe que muda de cor com o estado, e o menu do assistente.

Roda numa thread própria (pystray.run_detached); os cliques voltam para o loop do núcleo pelas funções
que o núcleo passa (elas mesmas usam call_soon_threadsafe).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Mesmas cores do orbe da tela (ui/src/componentes/Orbe.tsx), em RGB.
CORES = {
    "ocioso": ((124, 77, 255), (61, 90, 254)),
    "ouvindo": ((61, 90, 254), (64, 196, 255)),
    "pensando": ((124, 77, 255), (199, 91, 255)),
    "falando": ((64, 196, 255), (124, 77, 255)),
    "dormindo": ((70, 64, 110), (110, 90, 170)),
    "jogo": ((60, 58, 80), (60, 58, 80)),
}


def desenhar_icone(estado: str, tamanho: int = 64):
    """Orbe com degradê radial, borda clara e fundo transparente."""
    from PIL import Image, ImageDraw

    centro, borda = CORES.get(estado, CORES["ocioso"])
    img = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    meio = (tamanho - 1) / 2
    raio = meio - 2
    # Círculos do maior (cor da borda) para o menor (centro, um pouco mais claro): degradê radial.
    for r in range(int(raio), 0, -1):
        t = r / raio  # 1 = borda, 0 = centro
        cor = tuple(min(255, int(borda[k] * t + centro[k] * (1 - t) + 70 * (1 - t) ** 3)) for k in range(3))
        d.ellipse((meio - r, meio - r, meio + r, meio + r), fill=(*cor, 255))
    d.ellipse((meio - raio, meio - raio, meio + raio, meio + raio), outline=(232, 230, 255, 200), width=2)
    return img


@dataclass
class Acoes:
    abrir: Callable[[], None]
    falar_agora: Callable[[], None]
    alternar_escuta: Callable[[], None]
    escuta_pausada: Callable[[], bool]
    alternar_inicio: Callable[[], None]
    inicia_com_windows: Callable[[], bool]
    sair: Callable[[], None]


class Bandeja:
    def __init__(self, nome: str, acoes: Acoes):
        import pystray

        self.nome = nome
        self.estado = "ocioso"
        menu = pystray.Menu(
            pystray.MenuItem(f"Abrir {nome}", lambda: acoes.abrir(), default=True),
            pystray.MenuItem("Falar agora", lambda: acoes.falar_agora()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pausar escuta", lambda: acoes.alternar_escuta(), checked=lambda _i: acoes.escuta_pausada()),
            pystray.MenuItem("Iniciar com o Windows", lambda: acoes.alternar_inicio(),
                             checked=lambda _i: acoes.inicia_com_windows()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", lambda: acoes.sair()),
        )
        self.icone = pystray.Icon("vision", desenhar_icone("ocioso"), nome, menu)

    def iniciar(self) -> None:
        self.icone.run_detached()

    def mostrar_estado(self, estado: str) -> None:
        if estado == self.estado:
            return
        self.estado = estado
        try:
            self.icone.icon = desenhar_icone(estado)
            self.icone.title = f"{self.nome}: {estado}"
        except Exception:  # noqa: BLE001 - ícone que não atualiza não pode derrubar o núcleo
            log.exception("não consegui atualizar o ícone da bandeja")

    def atualizar_menu(self) -> None:
        """Depois de mudar uma opção marcável (o win32 monta o menu uma vez)."""
        try:
            self.icone.update_menu()
        except Exception:  # noqa: BLE001
            log.exception("não consegui atualizar o menu da bandeja")

    def avisar(self, texto: str) -> None:
        try:
            self.icone.notify(texto, self.nome)
        except Exception:  # noqa: BLE001
            log.exception("notificação da bandeja falhou")

    def parar(self) -> None:
        try:
            self.icone.stop()
        except Exception:  # noqa: BLE001
            pass
