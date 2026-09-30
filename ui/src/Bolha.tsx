// Bolha flutuante: aparece no canto quando você fala com a janela fechada e some sozinha.
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useReducer } from "react";
import { Orbe } from "./componentes/Orbe";
import { inicial, reduzir } from "./estado";
import { chamar, noApp } from "./ponte";
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

declare global {
  interface Window {
    __reiniciarBolha?: () => void;
  }
}

export function Bolha() {
  const [s, emitir] = useReducer(reduzir, inicial);

  useEffect(() => {
    let timers: ReturnType<typeof setTimeout>[] = [];
    const tocar = () => {
      timers.forEach(clearTimeout);
      timers = [];
      emitir({ tipo: "painel" });
      let t = 0;
      for (const [espera, ev] of ROTEIRO) {
        t += espera;
        timers.push(setTimeout(() => emitir(ev), t));
      }
      // Some 6 s depois da última fala (no app, a janela se esconde; no navegador, repete).
      timers.push(setTimeout(() => (noApp() ? chamar("esconder_bolha") : tocar()), t + 6000));
    };
    window.__reiniciarBolha = tocar;
    tocar();
    const onda = setInterval(() => {
      const v = 0.3 + Math.random() * 0.6;
      emitir({ tipo: "nivel", fonte: "mic", valor: v });
      emitir({ tipo: "nivel", fonte: "voz", valor: v });
    }, 90);
    return () => {
      timers.forEach(clearTimeout);
      clearInterval(onda);
    };
  }, []);

  const fala = [...s.conversa].reverse().find((m) => m.autor === "voce")?.texto;
  const resposta = [...s.conversa].reverse().find((m) => m.autor === "assistente")?.texto;

  return (
    <motion.div className="bolha" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
      <Orbe estado={s.estado} nivelMic={s.nivelMic} nivelVoz={s.nivelVoz} tamanho={74} />
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
              {resposta}
            </motion.p>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
