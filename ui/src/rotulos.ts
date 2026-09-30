import type { Estado } from "./tipos";

export const ROTULO_ESTADO: Record<Estado, string> = {
  ocioso: "Pronto",
  ouvindo: "Ouvindo…",
  pensando: "Pensando…",
  falando: "Falando",
  dormindo: "Escuta pausada",
  jogo: "Modo jogo",
};

const FERRAMENTAS: Record<string, string> = {
  agenda_listar: "consultou a agenda",
  agenda_buscar: "buscou na agenda",
  agenda_criar: "criou evento",
  agenda_alterar: "alterou evento",
  agenda_apagar: "apagou evento",
  guardar_memoria: "guardou na memória",
  buscar_memoria: "buscou na memória",
  esquecer: "esqueceu",
};

export const rotuloFerramenta = (nome: string) =>
  FERRAMENTAS[nome] ?? (nome.startsWith("financas_") ? "finanças" : nome.startsWith("tarefas_") ? "tarefas" : nome);

const DIAS = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
const MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

export function hoje(): string {
  const d = new Date();
  return `${DIAS[d.getDay()]}, ${d.getDate()} ${MESES[d.getMonth()]}`;
}

export function agoraHHMM(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
