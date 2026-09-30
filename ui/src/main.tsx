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

createRoot(document.getElementById("raiz")!).render(<StrictMode>{bolha ? <Bolha /> : <App />}</StrictMode>);
