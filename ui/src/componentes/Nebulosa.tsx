// Fundo: tempestade cósmica roxa/azul com relâmpagos. Renderiza em meia resolução (a nuvem é difusa;
// ninguém nota, e a GPU agradece).
import { useEffect, useRef } from "react";
import { aproximar, iniciarShader, RUIDO_GLSL } from "../gl";
import type { Estado } from "../tipos";

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uT, uAgito, uFlash, uPulso;
uniform vec2 uFlashPos;
${RUIDO_GLSL}
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / uRes.y;
  float t = uT * 0.035 * (1.0 + uAgito * 1.6);
  vec2 q = vec2(fbm(p * 1.5 + vec2(0.0, t)), fbm(p * 1.5 + vec2(5.2, 1.3) - t));
  vec2 r = vec2(fbm(p * 1.5 + 3.0 * q + vec2(1.7, 9.2) + t * 1.3), fbm(p * 1.5 + 3.0 * q + vec2(8.3, 2.8) - t));
  float n = fbm(p * 1.5 + 3.5 * r);

  vec3 base = vec3(0.018, 0.014, 0.05);
  vec3 indigo = vec3(0.09, 0.06, 0.30);
  vec3 violeta = vec3(0.49, 0.30, 1.0);
  vec3 azul = vec3(0.24, 0.35, 1.0);
  vec3 ciano = vec3(0.25, 0.77, 1.0);

  // Uma "faixa" diagonal de tempestade concentra a nuvem (em vez de um véu uniforme).
  float faixa = exp(-pow((p.y + p.x * 0.35 - 0.05 * sin(uT * 0.05)) * 1.6, 2.0));
  float densidade = smoothstep(0.2, 0.8, n) * (0.35 + 0.9 * faixa);

  vec3 col = mix(base, indigo * 1.4, densidade);
  col = mix(col, violeta * 0.85, smoothstep(0.5, 0.9, n) * 0.75 * length(q) * (0.4 + faixa));
  col = mix(col, azul * 0.9, smoothstep(0.45, 0.95, r.x) * 0.45 * (0.3 + faixa));
  col += ciano * pow(smoothstep(0.6, 1.0, n * r.y), 3.0) * 0.55 * (0.3 + faixa);
  // Veios claros dentro da nuvem (a "eletricidade" da tempestade).
  float veio = pow(1.0 - abs(fbm(p * 3.0 + r * 2.0 + t) * 2.0 - 1.0), 12.0);
  col += mix(violeta, ciano, 0.4) * veio * 0.35 * faixa;

  vec2 celula = floor(gl_FragCoord.xy / 1.5);
  float estrela = pow(hash(celula), 380.0);
  col += vec3(0.8, 0.85, 1.0) * estrela * (0.55 + 0.45 * sin(uT * 1.7 + hash(celula + 3.1) * 6.28));

  float d = length(p - uFlashPos);
  col += (violeta * 0.55 + ciano * 0.45) * uFlash * exp(-d * 2.6) * (0.5 + n * 1.2);

  col *= 0.65 + 0.35 * smoothstep(1.3, 0.2, length(p));
  col *= 0.9 + uPulso * 0.25;
  gl_FragColor = vec4(col, 1.0);
}
`;

const AGITO: Record<Estado, number> = { ocioso: 0.15, ouvindo: 0.35, pensando: 1.0, falando: 0.55, dormindo: 0.0, jogo: 0.0 };

export function Nebulosa({ estado, nivelVoz }: { estado: Estado; nivelVoz: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const alvo = useRef({ estado, nivelVoz });
  alvo.current = { estado, nivelVoz };

  useEffect(() => {
    let agito = 0.15;
    let pulso = 0;
    let flash = 0;
    let flashPos = [0, 0];
    let proximoFlash = performance.now() / 1000 + 4;
    let segundo: number | null = null;
    return iniciarShader(ref.current!, FRAG, ["uT", "uAgito", "uFlash", "uPulso", "uFlashPos"], 0.5, ({ gl, uniforms }, t) => {
      const { estado: e, nivelVoz: v } = alvo.current;
      agito = aproximar(agito, AGITO[e], 0.04);
      pulso = aproximar(pulso, e === "falando" ? v : 0, 0.2);
      // Relâmpago: acende rápido, apaga devagar; às vezes pisca duas vezes.
      if (t > proximoFlash && e !== "jogo" && e !== "dormindo") {
        flash = 1;
        flashPos = [Math.random() * 1.6 - 0.8, Math.random() * 0.8 - 0.4];
        segundo = Math.random() < 0.4 ? t + 0.18 : null;
        proximoFlash = t + 6 + Math.random() * 9;
      }
      if (segundo !== null && t > segundo) {
        flash = Math.max(flash, 0.7);
        segundo = null;
      }
      flash *= 0.9;
      gl.uniform1f(uniforms.uT, t);
      gl.uniform1f(uniforms.uAgito, agito);
      gl.uniform1f(uniforms.uFlash, flash);
      gl.uniform1f(uniforms.uPulso, pulso);
      gl.uniform2f(uniforms.uFlashPos, flashPos[0], flashPos[1]);
    });
  }, []);

  return <canvas ref={ref} className="nebulosa" aria-hidden />;
}
