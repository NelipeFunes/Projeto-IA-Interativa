// Contrato de eventos entre o núcleo (Python) e a interface.
// Quem emite: o roteiro de demonstração (?demo=1) ou o WebSocket /ws do núcleo (?nucleo=1).

export type Estado = "ocioso" | "ouvindo" | "pensando" | "falando" | "dormindo" | "jogo";

export interface EventoAgenda {
  id: string;
  titulo: string;
  inicio: string; // "HH:MM" (hoje)
  fim?: string;
  local?: string;
  feriado?: boolean;
  diaInteiro?: boolean;
  /** Id do cartão pendente que virou este evento: mantém o voo do cartão até a agenda (layoutId). */
  anima?: string;
}

export interface Memoria {
  id: number;
  texto: string;
}

export interface Status {
  modelo: string;
  vram: string;
  microfone: string;
  googleDias: number | null; // dias restantes do login do Google (modo teste: 7)
  modoJogo: boolean;
}

export interface Pendente {
  id: string;
  ferramenta: string;
  descricao: string;
  evento?: EventoAgenda;
}

export type ValorAjuste = string | number | boolean | null;

export interface CampoAjuste {
  chave: string;
  tipo: "texto" | "numero" | "escolha";
  rotulo: string;
  aoVivo: boolean;
  minimo?: number | null;
  maximo?: number | null;
}

/** Tela de ajustes: o que vale agora, os campos, as opções (vozes, microfones) e o resultado de salvar. */
export interface DadosAjustes {
  valores: Record<string, ValorAjuste>;
  campos: CampoAjuste[];
  opcoes: Record<string, string[]>;
  erro?: string;
  salvo?: boolean;
  reiniciar?: boolean;
}

export type Evento =
  | { tipo: "estado"; valor: Estado }
  | { tipo: "nivel"; fonte: "mic" | "voz"; valor: number }
  | { tipo: "fala_usuario"; texto: string; canal: "voz" | "texto" }
  | { tipo: "resposta_parcial"; texto: string }
  | { tipo: "resposta"; texto: string; aguardando_confirmacao?: boolean }
  | { tipo: "ferramenta_inicio"; nome: string; args?: Record<string, unknown> }
  | { tipo: "ferramenta_fim"; nome: string; ok: boolean; args?: Record<string, unknown>; dados?: unknown }
  | { tipo: "pendente"; pendente: Pendente }
  | { tipo: "pendente_resolvido"; id: string; resultado: "executada" | "cancelada"; evento?: EventoAgenda | null }
  | { tipo: "aviso"; texto: string | null }
  | ({ tipo: "ajustes" } & DadosAjustes)
  | {
      tipo: "painel";
      nome?: string;
      agenda?: EventoAgenda[];
      memorias?: Memoria[];
      status?: Partial<Status>;
    };
