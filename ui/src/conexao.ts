// Conexão com o núcleo pelo WebSocket /ws (janela aberta pelo núcleo: ?nucleo=1#t=<token>).
// O token vem no fragmento da URL, que o navegador nunca manda ao servidor; ele é lido uma vez e apagado
// da barra de endereço. A primeira mensagem é o "olá" com o token; sem ele o núcleo fecha a conexão.
import type { Evento, ValorAjuste } from "./tipos";

export type Comando =
  | { tipo: "texto"; texto: string }
  | { tipo: "confirmar"; id: string; sim: boolean }
  | { tipo: "ouvir"; segurando: boolean }
  | { tipo: "parar_fala" }
  | { tipo: "ajustes" }
  | { tipo: "salvar_ajustes"; valores: Record<string, ValorAjuste> }
  | { tipo: "amostra_voz"; voz: string }
  | { tipo: "conexoes" }
  | { tipo: "conectar"; servico: string; dados: Record<string, string> }
  | { tipo: "desconectar"; servico: string }
  | { tipo: "ligar_conexao"; servico: string; ligado: boolean }
  | { tipo: "cancelar_conexao"; servico: string }
  | { tipo: "reiniciar" };

export interface Conexao {
  enviar: (c: Comando) => void;
  fechar: () => void;
}

let tokenGuardado: string | null = null;

/** Lê o token do fragmento (#t=...) uma vez e tira ele da URL. */
export function lerToken(): string | null {
  if (tokenGuardado) return tokenGuardado;
  const t = new URLSearchParams(window.location.hash.slice(1)).get("t");
  if (t) {
    tokenGuardado = t;
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return tokenGuardado;
}

export function conectar(aoEvento: (ev: Evento) => void, aoMudar: (ligado: boolean) => void): Conexao {
  const token = lerToken();
  let ws: WebSocket | null = null;
  let espera = 500;
  let fechado = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const abrir = () => {
    if (fechado || !token) return;
    ws = new WebSocket(`ws://${window.location.host}/ws`);
    ws.onopen = () => {
      ws?.send(JSON.stringify({ tipo: "ola", token }));
      espera = 500;
      aoMudar(true);
    };
    ws.onmessage = (m) => {
      try {
        aoEvento(JSON.parse(m.data) as Evento);
      } catch {
        // mensagem que não é JSON: ignora
      }
    };
    ws.onclose = () => {
      aoMudar(false);
      if (fechado) return;
      // O núcleo reiniciou ou ainda está subindo: tenta de novo, cada vez esperando um pouco mais (até 5 s).
      timer = setTimeout(abrir, espera);
      espera = Math.min(espera * 2, 5000);
    };
  };
  abrir();
  if (!token) aoMudar(false);

  return {
    enviar: (c) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(c));
    },
    fechar: () => {
      fechado = true;
      clearTimeout(timer);
      ws?.close();
    },
  };
}
