// Roteiro de demonstração da Fase A: simula uma conversa inteira para aprovar o visual sem o núcleo.
// Dados de EXEMPLO genéricos (o repositório é público: nada da agenda real aqui).
import type { Evento, EventoAgenda, Memoria } from "./tipos";

export const AGENDA_EXEMPLO: EventoAgenda[] = [
  { id: "e1", titulo: "Café e planejamento", inicio: "08:30", fim: "09:00" },
  { id: "e2", titulo: "Trabalho — bloco focado", inicio: "09:00", fim: "12:00" },
  { id: "e3", titulo: "Almoço", inicio: "12:00", fim: "13:00" },
  { id: "e4", titulo: "Reunião do projeto", inicio: "14:00", fim: "15:30", local: "Online" },
  { id: "e5", titulo: "Aula de inglês", inicio: "19:00", fim: "20:30" },
  { id: "e6", titulo: "Academia", inicio: "21:00", fim: "22:00" },
];

export const MEMORIAS_EXEMPLO: Memoria[] = [
  { id: 3, texto: "Está aprendendo inglês." },
  { id: 2, texto: "Treina às segundas, quartas e sextas." },
  { id: 1, texto: "Prefere reuniões depois das 10h." },
];

const BARBEIRO: EventoAgenda = { id: "novo-barbeiro", titulo: "Barbeiro", inicio: "16:00", fim: "17:00" };

export interface Passo {
  nome: string;
  espera: number; // ms antes de aplicar
  eventos: Evento[];
  aguardaConfirmacao?: string; // id da pendência: o roteiro para até Confirmar/Cancelar
  seCancelar?: Evento[]; // o que acontece se cancelar
  pulaSeCancelado?: boolean; // passo que só existe quando a pendência anterior foi confirmada
}

export const PASSOS: Passo[] = [
  {
    nome: "inicio",
    espera: 0,
    eventos: [
      {
        tipo: "painel",
        nome: "Jarvis",
        agenda: AGENDA_EXEMPLO,
        memorias: MEMORIAS_EXEMPLO,
        status: { modelo: "qwen3.5:4b", vram: "3,1 GB", microfone: "Headset", googleDias: 6, modoJogo: false },
      },
      { tipo: "estado", valor: "ocioso" },
    ],
  },
  { nome: "ouvindo", espera: 1800, eventos: [{ tipo: "estado", valor: "ouvindo" }] },
  {
    nome: "fala",
    espera: 2600,
    eventos: [
      { tipo: "fala_usuario", texto: "Marca barbeiro hoje às quatro da tarde", canal: "voz" },
      { tipo: "estado", valor: "pensando" },
      { tipo: "ferramenta_inicio", nome: "agenda_listar", args: { data_inicio: "hoje" } },
    ],
  },
  {
    nome: "consultou",
    espera: 1400,
    eventos: [{ tipo: "ferramenta_fim", nome: "agenda_listar", ok: true, dados: AGENDA_EXEMPLO }],
  },
  {
    nome: "pendente",
    espera: 700,
    eventos: [
      { tipo: "ferramenta_inicio", nome: "agenda_criar" },
      {
        tipo: "pendente",
        pendente: {
          id: "p1",
          ferramenta: "agenda_criar",
          descricao: "Vou criar 'Barbeiro' hoje das 16:00 às 17:00. Confirma?",
          evento: BARBEIRO,
        },
      },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta_parcial", texto: "Vou criar 'Barbeiro' hoje " },
    ],
  },
  {
    nome: "pergunta",
    espera: 900,
    eventos: [
      { tipo: "resposta", texto: "Vou criar 'Barbeiro' hoje das 16:00 às 17:00. Confirma?", aguardando_confirmacao: true },
    ],
  },
  {
    nome: "aguarda",
    espera: 1600,
    eventos: [{ tipo: "estado", valor: "ouvindo" }],
    aguardaConfirmacao: "p1",
    seCancelar: [
      { tipo: "fala_usuario", texto: "Não", canal: "voz" },
      { tipo: "pendente_resolvido", id: "p1", resultado: "cancelada" },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Beleza, cancelei." },
    ],
  },
  {
    nome: "confirmado",
    espera: 500,
    pulaSeCancelado: true,
    eventos: [
      { tipo: "fala_usuario", texto: "Sim", canal: "voz" },
      { tipo: "pendente_resolvido", id: "p1", resultado: "executada" },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Feito. Criei 'Barbeiro' hoje das 16:00 às 17:00." },
    ],
  },
  { nome: "ocioso-1", espera: 2600, eventos: [{ tipo: "estado", valor: "ocioso" }] },
  { nome: "ouvindo-2", espera: 1500, eventos: [{ tipo: "estado", valor: "ouvindo" }] },
  {
    nome: "memoria",
    espera: 2400,
    eventos: [
      { tipo: "fala_usuario", texto: "Lembra que eu prefiro cortar o cabelo às sextas", canal: "voz" },
      { tipo: "estado", valor: "pensando" },
      { tipo: "ferramenta_inicio", nome: "guardar_memoria" },
    ],
  },
  {
    nome: "memoria-fim",
    espera: 1100,
    eventos: [
      { tipo: "ferramenta_fim", nome: "guardar_memoria", ok: true, dados: { id: 4, texto: "Prefere cortar o cabelo às sextas." } },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Anotado: você prefere cortar o cabelo às sextas." },
    ],
  },
  { nome: "ocioso-2", espera: 2600, eventos: [{ tipo: "estado", valor: "ocioso" }] },
  { nome: "ouvindo-3", espera: 1500, eventos: [{ tipo: "estado", valor: "ouvindo" }] },
  {
    nome: "apagar",
    espera: 2200,
    eventos: [
      { tipo: "fala_usuario", texto: "Cancela a aula de inglês de hoje", canal: "voz" },
      { tipo: "estado", valor: "pensando" },
      { tipo: "ferramenta_inicio", nome: "agenda_buscar", args: { texto: "inglês" } },
    ],
  },
  {
    nome: "apagar-pendente",
    espera: 1300,
    eventos: [
      { tipo: "ferramenta_fim", nome: "agenda_buscar", ok: true },
      { tipo: "ferramenta_inicio", nome: "agenda_apagar" },
      {
        tipo: "pendente",
        pendente: {
          id: "p2",
          ferramenta: "agenda_apagar",
          descricao: "Vou apagar 'Aula de inglês' de hoje às 19:00. Confirma?",
          evento: AGENDA_EXEMPLO[4],
        },
      },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Vou apagar 'Aula de inglês' de hoje às 19:00. Confirma?", aguardando_confirmacao: true },
    ],
  },
  {
    nome: "aguarda-2",
    espera: 1800,
    eventos: [{ tipo: "estado", valor: "ouvindo" }],
    aguardaConfirmacao: "p2",
    seCancelar: [
      { tipo: "fala_usuario", texto: "Não, deixa", canal: "voz" },
      { tipo: "pendente_resolvido", id: "p2", resultado: "cancelada" },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Beleza, cancelei." },
    ],
  },
  {
    nome: "apagado",
    espera: 500,
    pulaSeCancelado: true,
    eventos: [
      { tipo: "fala_usuario", texto: "Pode", canal: "voz" },
      { tipo: "pendente_resolvido", id: "p2", resultado: "executada" },
      { tipo: "estado", valor: "falando" },
      { tipo: "resposta", texto: "Feito. Apaguei 'Aula de inglês' de hoje às 19:00." },
    ],
  },
  { nome: "fim", espera: 2600, eventos: [{ tipo: "estado", valor: "ocioso" }] },
];

/** Todos os eventos até o passo `n` (inclusive), confirmando o que estiver pendente. Para capturas de tela. */
export function eventosAte(n: number): Evento[] {
  return PASSOS.slice(0, n + 1).flatMap((p) => p.eventos);
}

type Emitir = (ev: Evento) => void;

/** Toca o roteiro no tempo certo e simula o volume do microfone e da voz para o orbe. */
export class Demo {
  private indice = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private onda: ReturnType<typeof setInterval> | null = null;
  private esperando: string | null = null;
  private cancelado = false;
  private automatico: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private emitir: Emitir,
    private opcoes: { autoConfirmarMs?: number; repetir?: boolean } = {},
  ) {}

  iniciar(): void {
    this.parar();
    this.indice = 0;
    this.cancelado = false;
    this.agendar();
    let fase = 0;
    this.onda = setInterval(() => {
      fase += 0.35;
      const v = 0.35 + 0.3 * Math.sin(fase) + 0.25 * Math.sin(fase * 2.7) * Math.random();
      this.emitir({ tipo: "nivel", fonte: "mic", valor: Math.max(0, Math.min(1, v)) });
      this.emitir({ tipo: "nivel", fonte: "voz", valor: Math.max(0, Math.min(1, v * 0.9 + 0.1)) });
    }, 70);
  }

  parar(): void {
    for (const t of [this.timer, this.automatico]) if (t) clearTimeout(t);
    if (this.onda) clearInterval(this.onda);
    this.timer = this.automatico = this.onda = null;
    this.esperando = null;
  }

  /** Botões Confirmar/Cancelar da tela. */
  responder(id: string, sim: boolean): void {
    if (this.esperando !== id) return;
    this.esperando = null;
    if (this.automatico) clearTimeout(this.automatico);
    const passo = PASSOS[this.indice - 1];
    if (!sim && passo?.seCancelar) {
      this.cancelado = true;
      passo.seCancelar.forEach(this.emitir);
    }
    this.agendar();
  }

  private agendar(): void {
    while (this.indice < PASSOS.length && PASSOS[this.indice].pulaSeCancelado && this.cancelado) {
      this.cancelado = false;
      this.indice++;
    }
    if (this.indice >= PASSOS.length) {
      if (this.opcoes.repetir) this.timer = setTimeout(() => this.iniciar(), 4000);
      return;
    }
    const passo = PASSOS[this.indice];
    this.timer = setTimeout(() => {
      passo.eventos.forEach(this.emitir);
      this.indice++;
      if (passo.aguardaConfirmacao) {
        this.esperando = passo.aguardaConfirmacao;
        if (this.opcoes.autoConfirmarMs) {
          const id = passo.aguardaConfirmacao;
          this.automatico = setTimeout(() => this.responder(id, true), this.opcoes.autoConfirmarMs);
        }
        return;
      }
      this.cancelado = false;
      this.agendar();
    }, passo.espera);
  }
}
