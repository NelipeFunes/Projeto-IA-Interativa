// Bolha flutuante: aparece no canto quando você fala com a janela fechada e some sozinha.
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useReducer, useState } from "react";
import { NUCLEO, useNucleo } from "./App";
import { Orbe } from "./componentes/Orbe";
import { type Acao, inicial, reduzir } from "./estado";
import { niveis } from "./niveis";
import { chamar, noApp } from "./ponte";
import { realcar } from "./realce";
import { ROTULO_ESTADO } from "./rotulos";
import type { Evento } from "./tipos";

const ROTEIRO: [number, Evento][] = [
  [200, { tipo: "estado", valor: "ouvindo" }],
  [2300, { tipo: "fala_usuario", texto: "O que eu tenho hoje à tarde?", canal: "voz" }],
  [0, { tipo: "estado", valor: "pensando" }],
  [1300, { tipo: "estado", valor: "falando" }],
  [0, { tipo: "resposta", texto: "Reunião do projeto às 14h e aula de inglês às 19h." }],
  [3200, { tipo: "estado", valor: "ocioso" }],
];

const SOME_DEPOIS_MS = 6000;

declare global {
  interface Window {
    __reiniciarBolha?: () => void;
  }
}

export function Bolha() {
  const [s, despachar] = useReducer(reduzir, inicial);
  const emitir = useCallback((ev: Acao) => {
    if (ev.tipo === "nivel") niveis[ev.fonte] = ev.valor;
    else despachar(ev);
  }, []);
  useNucleo(NUCLEO, emitir);

  // Núcleo: cada vez que a bolha aparece, começa com a conversa limpa (só a nova) e some 6 s depois
  // de o assistente voltar ao repouso, mesmo que nada tenha sido ouvido.
  const [aparecida, setAparecida] = useState(0);
  useEffect(() => {
    if (!NUCLEO) return;
    window.__reiniciarBolha = () => {
      emitir({ tipo: "limpar_conversa" });
      setAparecida((n) => n + 1);
    };
  }, [emitir]);
  const repouso = s.estado === "ocioso" || s.estado === "dormindo" || s.estado === "jogo";
  useEffect(() => {
    if (!NUCLEO || !aparecida || !repouso) return;
    const t = setTimeout(() => chamar("esconder_bolha"), SOME_DEPOIS_MS);
    return () => clearTimeout(t);
  }, [aparecida, repouso]);

  // Demonstração: um roteiro curto que se repete.
  useEffect(() => {
    if (NUCLEO) return;
    let timers: ReturnType<typeof setTimeout>[] = [];
    const tocar = () => {
      timers.forEach(clearTimeout);
      timers = [];
      emitir({ tipo: "reiniciar" });
      let t = 0;
      for (const [espera, ev] of ROTEIRO) {
        t += espera;
        timers.push(setTimeout(() => emitir(ev), t));
      }
      // Some 6 s depois da última fala (no app, a janela se esconde; no navegador, repete).
      timers.push(setTimeout(() => (noApp() ? chamar("esconder_bolha") : tocar()), t + SOME_DEPOIS_MS));
    };
    window.__reiniciarBolha = tocar;
    tocar();
    const onda = setInterval(() => {
      niveis.mic = niveis.voz = 0.3 + Math.random() * 0.6;
    }, 90);
    return () => {
      timers.forEach(clearTimeout);
      clearInterval(onda);
    };
  }, [emitir]);

  const fala = [...s.conversa].reverse().find((m) => m.autor === "voce")?.texto;
  const resposta = [...s.conversa].reverse().find((m) => m.autor === "assistente")?.texto;

  return (
    <motion.div className="bolha" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
      <Orbe estado={s.estado} tamanho={70} />
      <div className="bolha-texto">
        <span className={`bolha-estado legenda-${s.estado}`}>{ROTULO_ESTADO[s.estado]}</span>
        <AnimatePresence mode="popLayout">
          {fala && (
            <motion.p key={fala} className="bolha-fala" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              “{fala}”
            </motion.p>
          )}
          {resposta && (
            <motion.p key={resposta} className="bolha-resposta" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}>
              {realcar(resposta)}
            </motion.p>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
