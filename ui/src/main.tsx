import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/space-grotesk/500.css";
import "@fontsource/space-grotesk/700.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { Bolha } from "./Bolha";
import { parametros } from "./ponte";
import "./tema.css";

const bolha = parametros.get("janela") === "bolha";
document.documentElement.dataset.janela = bolha ? "bolha" : "principal";

// Arrastar um link ou arquivo para a janela faria a WebView navegar para ele, e a página nova herdaria a
// ponte com o Python. Cancelar o dragover/drop impede essa navegação (achado da revisão de 30/09).
for (const evento of ["dragover", "drop"] as const) {
  window.addEventListener(evento, (e) => e.preventDefault());
}

createRoot(document.getElementById("raiz")!).render(<StrictMode>{bolha ? <Bolha /> : <App />}</StrictMode>);
