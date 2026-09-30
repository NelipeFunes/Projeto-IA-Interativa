// O orbe: plasma dentro de uma esfera com halo. Respira parado, reage ao microfone ouvindo,
// gira rápido pensando e ondula no ritmo da própria voz falando.
import { forwardRef, useEffect, useRef } from "react";
import { aproximar, iniciarShader, RUIDO_GLSL } from "../gl";
import { niveis } from "../niveis";
import type { Estado } from "../tipos";

const FRAG = `
precision highp float;
uniform vec2 uRes;
uniform float uT, uNivel, uGiro, uOndas;
uniform vec3 uCorA, uCorB;
${RUIDO_GLSL}
void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / (0.5 * min(uRes.x, uRes.y));
  float d = length(p);
  float ang = atan(p.y, p.x);
  float raio = 0.56 + 0.025 * sin(uT * 1.3) + uNivel * 0.1;
  float borda = raio + (fbm(vec2(ang * 2.0 + uT * 0.4, uT * 0.5)) - 0.5) * 0.06 * (1.0 + uNivel * 2.5);
  borda += sin(d * 22.0 - uT * 7.0) * uOndas * uNivel * 0.02;
  float dentro = smoothstep(borda, borda - 0.025, d);

  // Profundidade: coordenadas "de esfera" para o plasma parecer estar dentro de um volume, não pintado.
  float z = sqrt(max(0.0, 1.0 - pow(min(d / raio, 1.0), 2.0)));
  vec2 esfera = p / (raio * (0.6 + 0.4 * z));
  float rot = uT * (0.12 + uGiro * 0.9);
  vec2 q = mat2(cos(rot), -sin(rot), sin(rot), cos(rot)) * esfera * 1.6;
  float vel = 0.2 + uGiro * 0.6;
  float plasma = fbm(q + vec2(fbm(q * 1.4 + uT * vel), fbm(q * 1.4 - uT * vel + 3.7)) * 1.8);
  // Filamentos: ruído "ridged" dá linhas finas e brilhantes, como descargas dentro do orbe.
  float fil = pow(1.0 - abs(fbm(q * 2.2 + plasma * 1.5 + uT * vel * 0.7) * 2.0 - 1.0), 6.0);

  vec3 escuro = vec3(0.03, 0.02, 0.10);
  vec3 cor = mix(escuro, uCorA * 0.85, smoothstep(0.3, 0.75, plasma));
  cor += uCorB * fil * (1.1 + uNivel * 1.2);
  cor += vec3(1.0) * pow(fil, 3.0) * 0.6;
  cor += mix(uCorA, uCorB, 0.5) * pow(z, 3.0) * 0.25;          // núcleo levemente aceso
  cor *= 0.45 + 0.55 * z;                                       // sombra nas bordas da esfera
  cor += uCorB * pow(1.0 - z, 2.5) * 1.3;                        // aro de luz (fresnel)

  float halo = exp(-max(d - borda, 0.0) * 3.2) * (0.38 + uNivel * 0.6);
  float anel = exp(-pow((d - borda - 0.08 - uNivel * 0.05) * 30.0, 2.0)) * uOndas * uNivel * 0.7;
  vec3 brilho = mix(uCorA, uCorB, 0.65) * (halo + anel);
  float alfa = clamp(max(dentro, halo + anel), 0.0, 1.0);
  vec3 col = cor * dentro + brilho * (1.0 - dentro);
  gl_FragColor = vec4(col * alfa, alfa);
}
`;

type Cor = [number, number, number];
const VIOLETA: Cor = [0.49, 0.3, 1.0];
const AZUL: Cor = [0.24, 0.35, 1.0];
const CIANO: Cor = [0.25, 0.77, 1.0];
const MAGENTA: Cor = [0.78, 0.35, 1.0];
const APAGADO: Cor = [0.2, 0.18, 0.35];

const CORES: Record<Estado, [Cor, Cor]> = {
  ocioso: [VIOLETA, AZUL],
  ouvindo: [AZUL, CIANO],
  pensando: [VIOLETA, MAGENTA],
  falando: [CIANO, VIOLETA],
  dormindo: [APAGADO, VIOLETA],
  jogo: [APAGADO, APAGADO],
};

export const Orbe = forwardRef<HTMLDivElement, { estado: Estado; tamanho?: number }>(
  function Orbe({ estado, tamanho = 340 }, refExterno) {
    const ref = useRef<HTMLCanvasElement>(null);
    const alvo = useRef({ estado });
    alvo.current = { estado };

    useEffect(() => {
      let nivel = 0;
      let giro = 0;
      let ondas = 0;
      const a = [...VIOLETA];
      const b = [...AZUL];
      return iniciarShader(ref.current!, FRAG, ["uT", "uNivel", "uGiro", "uOndas", "uCorA", "uCorB"], 1, ({ gl, uniforms }, t) => {
        const s = alvo.current;
        const bruto = s.estado === "ouvindo" ? niveis.mic : s.estado === "falando" ? niveis.voz : 0;
        nivel = aproximar(nivel, bruto, 0.25);
        giro = aproximar(giro, s.estado === "pensando" ? 1 : 0, 0.05);
        ondas = aproximar(ondas, s.estado === "falando" ? 1 : 0, 0.08);
        const [ca, cb] = CORES[s.estado];
        for (let i = 0; i < 3; i++) {
          a[i] = aproximar(a[i], ca[i], 0.06);
          b[i] = aproximar(b[i], cb[i], 0.06);
        }
        gl.uniform1f(uniforms.uT, t);
        gl.uniform1f(uniforms.uNivel, nivel);
        gl.uniform1f(uniforms.uGiro, giro);
        gl.uniform1f(uniforms.uOndas, ondas);
        gl.uniform3f(uniforms.uCorA, a[0], a[1], a[2]);
        gl.uniform3f(uniforms.uCorB, b[0], b[1], b[2]);
      });
    }, []);

    return (
      <div className="orbe" ref={refExterno} style={{ width: tamanho, height: tamanho }}>
        <canvas ref={ref} />
      </div>
    );
  },
);
