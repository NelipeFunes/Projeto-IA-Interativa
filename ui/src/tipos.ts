// Contrato de eventos entre o núcleo (Python) e a interface.
// Na Fase A quem emite é o roteiro de demonstração; na Fase C, o WebSocket /ws do núcleo.

export type Estado = "ocioso" | "ouvindo" | "pensando" | "falando" | "dormindo" | "jogo";

export interface EventoAgenda {
  id: string;
  titulo: string;
  inicio: string; // "HH:MM" (hoje)
  fim?: string;
  local?: string;
  feriado?: boolean;
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

export type Evento =
  | { tipo: "estado"; valor: Estado }
  | { tipo: "nivel"; fonte: "mic" | "voz"; valor: number }
  | { tipo: "fala_usuario"; texto: string; canal: "voz" | "texto" }
  | { tipo: "resposta_parcial"; texto: string }
  | { tipo: "resposta"; texto: string; aguardando_confirmacao?: boolean }
  | { tipo: "ferramenta_inicio"; nome: string; args?: Record<string, unknown> }
  | { tipo: "ferramenta_fim"; nome: string; ok: boolean; args?: Record<string, unknown>; dados?: unknown }
  | { tipo: "pendente"; pendente: Pendente }
  | { tipo: "pendente_resolvido"; id: string; resultado: "executada" | "cancelada" }
  | { tipo: "aviso"; texto: string | null }
  | {
      tipo: "painel";
      nome?: string;
      agenda?: EventoAgenda[];
      memorias?: Memoria[];
      status?: Partial<Status>;
    };
