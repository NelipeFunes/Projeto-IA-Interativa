// Volume do microfone e da voz (0..1), ~15 vezes por segundo. Fica FORA do estado do React de propósito:
// só os shaders usam, e eles leem a cada quadro. No estado do React, cada atualização redesenhava a tela
// inteira (achado da revisão de 30/09).
export const niveis = { mic: 0, voz: 0 };
