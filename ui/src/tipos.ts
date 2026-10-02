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

/** Um resultado de busca na web (web_buscar): vira link clicável embaixo da resposta. */
export interface Link {
  titulo: string;
  url: string;
  site: string;
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

/** Um campo do formulário de conexão. Segredos (tipo "senha") nunca vêm preenchidos do núcleo. */
export interface CampoConexao {
  nome: string;
  rotulo: string;
  tipo: "texto" | "email" | "senha" | "arquivo";
  valor: string;
  dica: string;
  obrigatorio: boolean;
}

export type SituacaoConexao = "ok" | "atencao" | "falta" | "desligado";

/** Um cartão da tela de Conexões (vision/conexoes.py). */
export interface Servico {
  id: string;
  nome: string;
  descricao: string;
  ligado: boolean;
  situacao: SituacaoConexao;
  detalhe: string;
  campos: CampoConexao[];
  acao: string | null;
  desconectar: boolean;
  ajuda?: { rotulo: string; url: string };
}

/** A última frase de um login (o núcleo manda enquanto ele anda e quando acaba). */
export interface Andamento {
  texto: string;
  rodando: boolean;
  ok: boolean | null;
}

export interface DadosConexoes {
  servicos: Servico[];
  andamento: Record<string, Andamento>;
  /** Nomes dos serviços que mudaram desde que o Vision iniciou: só valem depois de reiniciar. */
  reiniciar: string[];
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
  | ({ tipo: "conexoes" } & DadosConexoes)
  | {
      tipo: "painel";
      nome?: string;
      agenda?: EventoAgenda[];
      memorias?: Memoria[];
      status?: Partial<Status>;
    };
