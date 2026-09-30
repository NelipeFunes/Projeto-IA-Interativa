// WebGL mínimo: um quadrado na tela inteira com um fragment shader. Limitado a 30 fps e parado quando
// a janela está escondida (o app fica em segundo plano quase o tempo todo; não pode gastar GPU à toa).

export const RUIDO_GLSL = `
float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  float a = hash(i), b = hash(i + vec2(1.0, 0.0)), c = hash(i + vec2(0.0, 1.0)), d = hash(i + vec2(1.0, 1.0));
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 r = mat2(0.8, 0.6, -0.6, 0.8);
  for (int i = 0; i < 6; i++) { v += a * noise(p); p = r * p * 2.02; a *= 0.5; }
  return v;
}
`;

const VERTEX = `attribute vec2 p; void main() { gl_Position = vec4(p, 0.0, 1.0); }`;

export interface Desenho {
  uniforms: Record<string, WebGLUniformLocation | null>;
  gl: WebGLRenderingContext;
}

export function iniciarShader(
  canvas: HTMLCanvasElement,
  fragment: string,
  nomesUniforms: string[],
  escala: number,
  aoDesenhar: (d: Desenho, tempo: number) => void,
): () => void {
  const gl = canvas.getContext("webgl", { premultipliedAlpha: true, antialias: false });
  if (!gl) {
    canvas.dataset.semWebgl = "1";
    return () => {};
  }
  const compilar = (tipo: number, fonte: string) => {
    const s = gl.createShader(tipo)!;
    gl.shaderSource(s, fonte);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "erro no shader");
    return s;
  };
  const prog = gl.createProgram()!;
  gl.attachShader(prog, compilar(gl.VERTEX_SHADER, VERTEX));
  gl.attachShader(prog, compilar(gl.FRAGMENT_SHADER, fragment));
  gl.linkProgram(prog);
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const uniforms: Desenho["uniforms"] = { uRes: gl.getUniformLocation(prog, "uRes") };
  for (const n of nomesUniforms) uniforms[n] = gl.getUniformLocation(prog, n);

  let quadro = 0;
  let ultimo = 0;
  const inicio = performance.now();
  const ajustar = () => {
    const w = Math.max(1, Math.round(canvas.clientWidth * escala));
    const h = Math.max(1, Math.round(canvas.clientHeight * escala));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  };
  const passo = (agora: number) => {
    quadro = requestAnimationFrame(passo);
    if (document.hidden || agora - ultimo < 1000 / 30) return;
    ultimo = agora;
    ajustar();
    gl.uniform2f(uniforms.uRes, canvas.width, canvas.height);
    aoDesenhar({ gl, uniforms }, (agora - inicio) / 1000);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  };
  quadro = requestAnimationFrame(passo);
  return () => cancelAnimationFrame(quadro);
}

/** Aproxima `atual` de `alvo` suavemente (para cores e níveis não pularem). */
export const aproximar = (atual: number, alvo: number, taxa: number) => atual + (alvo - atual) * taxa;
