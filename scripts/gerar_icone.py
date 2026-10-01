"""Gera o ícone do app (src/vision/recursos/vision.ico e .png) a partir do orbe da tela.

É o mesmo shader de ui/src/componentes/Orbe.tsx (estado "ocioso", violeta e azul), portado para numpy e
congelado num instante. Rode de novo se o orbe mudar: `uv run python scripts/gerar_icone.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

SAIDA = Path(__file__).resolve().parents[1] / "src" / "vision" / "recursos"
LADO = 512
INSTANTE = 7.0  # segundos de animação: um quadro com filamentos bonitos
ZOOM = 1.45  # o ícone corta o halo largo da tela: a esfera ocupa quase todo o quadrado
VIOLETA = np.array([0.49, 0.3, 1.0])
AZUL = np.array([0.24, 0.35, 1.0])
BRILHO = 0.95  # na tela é 0,75 (pedido de 30/09); ícone pequeno precisa de mais luz


def _fract(x):
    return x - np.floor(x)


def _hash(px, py):
    px, py = _fract(px * 123.34), _fract(py * 456.21)
    d = px * (px + 45.32) + py * (py + 45.32)
    px, py = px + d, py + d
    return _fract(px * py)


def _noise(px, py):
    ix, iy = np.floor(px), np.floor(py)
    fx, fy = px - ix, py - iy
    a, b = _hash(ix, iy), _hash(ix + 1, iy)
    c, d = _hash(ix, iy + 1), _hash(ix + 1, iy + 1)
    ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    return a + (b - a) * ux + (c - a) * uy * (1 - ux) + (d - b) * ux * uy


def _fbm(px, py):
    v, a = 0.0, 0.5
    for _ in range(6):
        v = v + a * _noise(px, py)
        # GLSL mat2(0.8, 0.6, -0.6, 0.8) é por colunas: r * p = (0.8x - 0.6y, 0.6x + 0.8y)
        px, py = (0.8 * px - 0.6 * py) * 2.02, (0.6 * px + 0.8 * py) * 2.02
        a *= 0.5
    return v


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def orbe(lado: int = LADO, t: float = INSTANTE) -> Image.Image:
    eixo = (np.arange(lado) + 0.5 - lado / 2) / (lado / 2) / ZOOM
    px, py = np.meshgrid(eixo, -eixo)
    d = np.hypot(px, py)
    ang = np.arctan2(py, px)
    raio = 0.56 + 0.025 * np.sin(t * 1.3)
    borda = raio + (_fbm(ang * 2 + t * 0.4, np.full_like(ang, t * 0.5)) - 0.5) * 0.06
    dentro = _smooth(borda, borda - 0.025, d)

    z = np.sqrt(np.maximum(0, 1 - np.minimum(d / raio, 1) ** 2))
    ex, ey = px / (raio * (0.6 + 0.4 * z)), py / (raio * (0.6 + 0.4 * z))
    rot = t * 0.12
    qx = (np.cos(rot) * ex + np.sin(rot) * ey) * 1.6
    qy = (-np.sin(rot) * ex + np.cos(rot) * ey) * 1.6
    vel = 0.2
    w1 = _fbm(qx * 1.4 + t * vel, qy * 1.4 + t * vel)
    w2 = _fbm(qx * 1.4 - t * vel + 3.7, qy * 1.4 - t * vel + 3.7)
    plasma = _fbm(qx + w1 * 1.8, qy + w2 * 1.8)
    fil = (1 - np.abs(_fbm(qx * 2.2 + plasma * 1.5 + t * vel * 0.7, qy * 2.2 + plasma * 1.5 + t * vel * 0.7)
                      * 2 - 1)) ** 6

    escuro = np.array([0.03, 0.02, 0.10])
    m = _smooth(0.3, 0.75, plasma)[..., None]
    cor = escuro * (1 - m) + VIOLETA * 0.85 * m
    cor = cor + AZUL * (fil * 1.1)[..., None]
    cor = cor + (fil ** 3 * 0.4)[..., None]
    cor = cor + (VIOLETA + AZUL) / 2 * (z ** 3 * 0.25)[..., None]
    cor = cor * (0.45 + 0.55 * z)[..., None]
    cor = cor + AZUL * ((1 - z) ** 2.5 * 1.3)[..., None]

    halo = np.exp(-np.maximum(d - borda, 0) * 3.2) * 0.38 * (1 - _smooth(0.72, 0.98, d))
    brilho = (VIOLETA * 0.35 + AZUL * 0.65) * halo[..., None]
    # Na tela o halo soma luz no fundo escuro; no ícone ele vira alfa, para funcionar em barra clara e escura.
    rgb_esfera = np.clip(cor * BRILHO, 0, 1)
    alfa_halo = np.clip(halo * 1.6, 0, 1) * (1 - dentro)
    canto = np.hypot(px, py) * ZOOM  # 1 = borda do quadrado
    alfa_halo = alfa_halo * (1 - _smooth(0.86, 1.0, canto))  # o halo some antes da borda: sem quadrado
    alfa = np.clip(dentro + alfa_halo, 0, 1)
    rgb_halo = np.clip(brilho * BRILHO / np.maximum(halo, 1e-6)[..., None] * 1.4, 0, 1)
    rgb = rgb_esfera * dentro[..., None] + rgb_halo * (1 - dentro)[..., None]
    rgba = np.dstack([rgb, alfa])
    return Image.fromarray((rgba * 255).round().astype(np.uint8), "RGBA")


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    img = orbe()
    img.resize((256, 256), Image.LANCZOS).save(SAIDA / "vision.png")
    img.save(SAIDA / "vision.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"ícone gravado em {SAIDA}")


if __name__ == "__main__":
    main()
