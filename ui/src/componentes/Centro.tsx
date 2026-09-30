// Centro da tela: o orbe, a legenda do estado e o cartão de ação pendente.
// O cartão de "criar evento" usa o mesmo layoutId do item da agenda: quando você confirma, ele some daqui
// e reaparece no painel, e o motion anima o voo entre os dois lugares.
import { AnimatePresence, motion } from "motion/react";
import { forwardRef } from "react";
import { ROTULO_ESTADO } from "../rotulos";
import type { Estado, Pendente } from "../tipos";
import { Orbe } from "./Orbe";

export const Centro = forwardRef<HTMLDivElement, {
  estado: Estado;
  nivelMic: number;
  nivelVoz: number;
  ultimaFala?: string;
  pendente: Pendente | null;
  aoResponder: (id: string, sim: boolean) => void;
}>(function Centro({ estado, nivelMic, nivelVoz, ultimaFala, pendente, aoResponder }, refOrbe) {
  const voa = pendente?.evento && (pendente.ferramenta === "agenda_criar" || pendente.ferramenta === "agenda_alterar");
  return (
    <div className="centro">
      <div className="orbe-area">
        <Orbe ref={refOrbe} estado={estado} nivelMic={nivelMic} nivelVoz={nivelVoz} />
        <motion.p key={estado} className={`legenda legenda-${estado}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
          {ROTULO_ESTADO[estado]}
        </motion.p>
        <AnimatePresence mode="wait">
          {ultimaFala && estado !== "ocioso" && (
            <motion.p
              key={ultimaFala}
              className="ultima-fala"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              “{ultimaFala}”
            </motion.p>
          )}
        </AnimatePresence>
      </div>

      <div className="pendente-area">
        <AnimatePresence>
          {pendente && (
            <motion.div
              key={pendente.id}
              className={`pendente pendente-${pendente.ferramenta}`}
              initial={{ opacity: 0, y: -40, scale: 0.6 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ type: "spring", stiffness: 200, damping: 20 }}
            >
              {voa && pendente.evento && (
                <motion.div layoutId={`evt-${pendente.evento.id}`} className="agenda-item fantasma">
                  <span className="agenda-hora">{pendente.evento.inicio}</span>
                  <span className="agenda-texto">
                    <strong>{pendente.evento.titulo}</strong>
                    {pendente.evento.fim && <small>até {pendente.evento.fim}</small>}
                  </span>
                </motion.div>
              )}
              <p className="pendente-texto">{pendente.descricao}</p>
              <div className="pendente-botoes">
                <button className="botao botao-sim" onClick={() => aoResponder(pendente.id, true)}>
                  Confirmar
                </button>
                <button className="botao botao-nao" onClick={() => aoResponder(pendente.id, false)}>
                  Cancelar
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
});
