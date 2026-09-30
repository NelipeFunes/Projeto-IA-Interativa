// Estado da tela e o redutor que aplica cada evento. Sem efeitos colaterais: fácil de testar.
import type { Estado, Evento, EventoAgenda, Memoria, Pendente, Status } from "./tipos";

export interface Mensagem {
  id: number;
  autor: "voce" | "assistente";
  texto: string;
  parcial?: boolean;
  ferramentas?: string[];
}

/** Quantas mensagens a tela guarda (o app fica ligado dias: a conversa não pode crescer sem limite). */
export const LIMITE_CONVERSA = 50;

/** Eventos do núcleo + ações só da tela. */
export type Acao = Evento | { tipo: "reiniciar" };

export interface EstadoUI {
  nome: string;
  estado: Estado;
  conversa: Mensagem[];
  agenda: EventoAgenda[];
  memorias: Memoria[];
  status: Status;
  pendente: Pendente | null;
  ferramentasTurno: string[];
  varredura: number; // muda quando a agenda é consultada → dispara a linha de varredura
  estrela: number; // muda quando uma memória é guardada → dispara a estrela voando
  aviso: string | null;
  seq: number;
}

export const inicial: EstadoUI = {
  nome: "Vision",
  estado: "ocioso",
  conversa: [],
  agenda: [],
  memorias: [],
  status: { modelo: "—", vram: "—", microfone: "—", googleDias: null, modoJogo: false },
  pendente: null,
  ferramentasTurno: [],
  varredura: 0,
  estrela: 0,
  aviso: null,
  seq: 0,
};

const ordenar = (lista: EventoAgenda[]) => [...lista].sort((a, b) => a.inicio.localeCompare(b.inicio));

const comEvento = (lista: EventoAgenda[], ev: EventoAgenda) => ordenar([...lista.filter((e) => e.id !== ev.id), ev]);

const semEvento = (lista: EventoAgenda[], id: unknown) => lista.filter((e) => e.id !== id);

function ultimaDoAssistente(conversa: Mensagem[]): Mensagem | undefined {
  const ultima = conversa[conversa.length - 1];
  return ultima?.autor === "assistente" && ultima.parcial ? ultima : undefined;
}

const aparar = (conversa: Mensagem[]) =>
  conversa.length > LIMITE_CONVERSA ? conversa.slice(conversa.length - LIMITE_CONVERSA) : conversa;

export function reduzir(s: EstadoUI, ev: Acao): EstadoUI {
  const seq = s.seq + 1;
  switch (ev.tipo) {
    case "reiniciar":
      return { ...inicial, nome: s.nome, seq };

    case "estado":
      return { ...s, seq, estado: ev.valor };

    case "nivel":
      return s; // o volume vai para niveis.ts, não para o estado da tela

    case "fala_usuario":
      return {
        ...s,
        seq,
        ferramentasTurno: [],
        conversa: aparar([...s.conversa, { id: seq, autor: "voce", texto: ev.texto }]),
      };

    case "resposta_parcial": {
      const aberta = ultimaDoAssistente(s.conversa);
      if (aberta) {
        return {
          ...s,
          seq,
          conversa: s.conversa.map((m) => (m === aberta ? { ...m, texto: m.texto + ev.texto } : m)),
        };
      }
      return {
        ...s,
        seq,
        conversa: aparar([...s.conversa, { id: seq, autor: "assistente", texto: ev.texto, parcial: true }]),
      };
    }

    case "resposta": {
      const final: Omit<Mensagem, "id"> = {
        autor: "assistente",
        texto: ev.texto,
        ferramentas: s.ferramentasTurno.length ? s.ferramentasTurno : undefined,
      };
      const aberta = ultimaDoAssistente(s.conversa);
      const conversa = aberta
        ? s.conversa.map((m) => (m === aberta ? { ...final, id: m.id } : m))
        : aparar([...s.conversa, { ...final, id: seq }]);
      return { ...s, seq, conversa };
    }

    case "ferramenta_inicio": {
      const varre = ev.nome === "agenda_listar" || ev.nome === "agenda_buscar";
      return {
        ...s,
        seq,
        ferramentasTurno: s.ferramentasTurno.includes(ev.nome) ? s.ferramentasTurno : [...s.ferramentasTurno, ev.nome],
        varredura: varre ? seq : s.varredura,
      };
    }

    case "ferramenta_fim": {
      if (!ev.ok) return { ...s, seq };
      if (ev.nome === "agenda_listar" && Array.isArray(ev.dados)) {
        return { ...s, seq, agenda: ordenar(ev.dados as EventoAgenda[]) };
      }
      if (ev.nome === "guardar_memoria" && ev.dados) {
        const m = ev.dados as Memoria;
        return { ...s, seq, estrela: seq, memorias: [m, ...s.memorias.filter((x) => x.id !== m.id)] };
      }
      if ((ev.nome === "agenda_criar" || ev.nome === "agenda_alterar") && ev.dados) {
        return { ...s, seq, agenda: comEvento(s.agenda, ev.dados as EventoAgenda) };
      }
      if (ev.nome === "agenda_apagar" && ev.args?.evento_id) {
        return { ...s, seq, agenda: semEvento(s.agenda, ev.args.evento_id) };
      }
      return { ...s, seq };
    }

    case "pendente":
      return { ...s, seq, pendente: ev.pendente };

    case "pendente_resolvido": {
      const p = s.pendente;
      if (!p || p.id !== ev.id) return { ...s, seq };
      if (ev.resultado === "cancelada" || !p.evento) return { ...s, seq, pendente: null };
      if (p.ferramenta === "agenda_criar" || p.ferramenta === "agenda_alterar") {
        return { ...s, seq, pendente: null, agenda: comEvento(s.agenda, p.evento) };
      }
      if (p.ferramenta === "agenda_apagar") {
        return { ...s, seq, pendente: null, agenda: semEvento(s.agenda, p.evento.id) };
      }
      return { ...s, seq, pendente: null };
    }

    case "aviso":
      return { ...s, seq, aviso: ev.texto };

    case "painel":
      return {
        ...s,
        seq,
        nome: ev.nome ?? s.nome,
        agenda: ev.agenda ? ordenar(ev.agenda) : s.agenda,
        memorias: ev.memorias ?? s.memorias,
        status: { ...s.status, ...ev.status },
      };
  }
}
