"""Ritmo da fala do XTTS: pausas curtas, sem som parado no começo e sem pontuação que vira silêncio.

Medido em 16/10 com uma voz de referência em português (docs/melhorias/voz-xtts.md): o ritmo da fala em si era
~15 letras/s, perto de uma voz natural (16–17), mas ~22% do tempo era pausa (mediana 0,54 s, de uma a três por frase),
quase toda em vírgula, dois-pontos e ponto. É isso que dava a impressão de voz lenta e travada.

- `preparar_texto`: o que o XTTS recebe. Tira o ponto final (sem isso ele falava "ponto" no fim de 58% das frases curtas)
  e, se pedido, as vírgulas, que ele transforma em pausas de ~0,5 s.
- `CompressorDePausas`: no áudio que sai, em tempo real (pedaço a pedaço): descarta o silêncio antes da fala, corta
  qualquer pausa dentro da frase acima de `pausa_max_s` e deixa `fim_s` de silêncio no fim, para separar as frases."""

from __future__ import annotations

import re

import numpy as np

PAUSA_MAX_S = 0.15  # pausa mais longa DENTRO da frase: a voz natural de referência quase não tinha; o XTTS fazia ~0,55 s
FIM_S = 0.25  # silêncio entre uma frase e a próxima (a voz de referência tinha ~0,25 s)
LIMIAR_SILENCIO = 0.006  # RMS por quadro, com o áudio já no volume final (fala ~0,08): ~-22 dB abaixo da fala
QUADRO_S = 0.010
FADE_S = 0.003  # entrada suave na 1ª fala depois de uma pausa cortada (sem estalo)

# Vírgula, ponto e vírgula e dois-pontos fora de números ("1,5", "7:30" ficam como estão).
_PAUSA_NO_TEXTO = re.compile(r"(?<!\d)[,;:]|[,;:](?!\d)")


def preparar_texto(texto: str, tirar_virgulas: bool = False) -> str:
    """O texto como o XTTS deve receber. `?`, `!` e reticências ficam (dão a entonação); só o `.` final sai."""
    texto = " ".join(texto.split())
    if tirar_virgulas:
        texto = " ".join(_PAUSA_NO_TEXTO.sub("", texto).split())
    return re.sub(r"(?<![.])\.\s*$", "", texto)


class CompressorDePausas:
    """Um por frase (guarda o estado entre os pedaços do streaming). `processar` devolve o que pode tocar."""

    def __init__(self, taxa: int, pausa_max_s: float = PAUSA_MAX_S, limiar: float = LIMIAR_SILENCIO,
                 fim_s: float = FIM_S):
        self.quadro = max(int(taxa * QUADRO_S), 1)
        self.max_quadros = max(int(round(pausa_max_s / QUADRO_S)), 0)
        self.fim_quadros = max(int(round(fim_s / QUADRO_S)), 0)
        self.fade = max(int(taxa * FADE_S), 1)
        self.limiar = limiar
        self._resto = np.zeros(0, dtype=np.float32)  # sobra de um pedaço que não fechou um quadro
        self._falou = False  # já houve fala nesta frase
        self._parados = 0  # quadros de silêncio seguidos
        # O que vier agora é descartado (pausa acima do máximo) ou é a 1ª fala da frase: ela entra com a rampa.
        self._cortou = True

    def processar(self, audio: np.ndarray) -> np.ndarray:
        dados = np.concatenate([self._resto, np.asarray(audio, dtype=np.float32).reshape(-1)])
        n = len(dados) // self.quadro
        self._resto = dados[n * self.quadro:]
        saida: list[np.ndarray] = []
        for i in range(n):
            quadro = dados[i * self.quadro:(i + 1) * self.quadro]
            if float(np.sqrt(np.mean(quadro**2))) >= self.limiar:
                if self._cortou:  # voltou a falar depois de uma pausa cortada: entrada suave
                    quadro = quadro.copy()
                    f = min(self.fade, len(quadro))
                    quadro[:f] *= np.linspace(0.0, 1.0, f, dtype=np.float32)
                self._falou, self._parados, self._cortou = True, 0, False
                saida.append(quadro)
            elif not self._falou:
                continue  # silêncio antes da 1ª fala: a 1ª palavra sai antes
            else:
                self._parados += 1
                if self._parados <= self.max_quadros:
                    saida.append(quadro)
                else:
                    self._cortou = True
        return np.concatenate(saida) if saida else np.zeros(0, dtype=np.float32)

    def terminar(self) -> np.ndarray:
        """O fim da frase: a sobra (menos de um quadro), se for fala, e o silêncio que completa `fim_s` (o que já saiu
        de silêncio no fim conta). Frase sem fala nenhuma não devolve nada."""
        resto, self._resto = self._resto, np.zeros(0, dtype=np.float32)
        if resto.size and float(np.sqrt(np.mean(resto**2))) >= self.limiar:
            self._parados, self._cortou = 0, False  # a sobra é fala: o silêncio que veio antes dela não é o do fim
        else:
            resto = np.zeros(0, dtype=np.float32)
            if not self._falou:
                return resto
        faltam = max(self.fim_quadros - min(self._parados, self.max_quadros), 0)
        return np.concatenate([resto, np.zeros(faltam * self.quadro, dtype=np.float32)])
