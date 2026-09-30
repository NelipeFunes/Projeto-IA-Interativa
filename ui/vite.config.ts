import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Caminhos relativos: a mesma build funciona servida pelo núcleo (/app) e pelo servidor local da janela.
  base: "./",
  server: { host: "127.0.0.1" },
  test: { environment: "node" },
});
