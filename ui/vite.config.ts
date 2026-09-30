import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

// Política de conteúdo da build: só carrega o que vem do próprio servidor local. Nada de CDN, nada de
// script de fora. (Fica só na build: o servidor de desenvolvimento do Vite injeta scripts inline.)
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self' ws://127.0.0.1:* http://127.0.0.1:*",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

const politicaDeConteudo = (): Plugin => ({
  name: "politica-de-conteudo",
  apply: "build",
  transformIndexHtml: (html) =>
    html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
});

export default defineConfig({
  plugins: [react(), politicaDeConteudo()],
  // Caminhos relativos: a mesma build funciona servida pelo núcleo (/app) e pelo servidor local da janela.
  base: "./",
  server: { host: "127.0.0.1" },
  test: { environment: "node" },
});
