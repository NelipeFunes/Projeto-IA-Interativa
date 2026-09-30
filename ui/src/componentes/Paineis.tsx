// Os quatro painéis de vidro: agenda de hoje, memória, conversa e status.
import { AnimatePresence, motion } from "motion/react";
import { forwardRef, useEffect, useRef } from "react";
import type { Mensagem } from "../estado";
import { agoraHHMM, hoje, rotuloFerramenta } from "../rotulos";
import type { EventoAgenda, Memoria, Pendente, Status } from "../tipos";

const mola = { type: "spring", stiffness: 170, damping: 22, mass: 0.9 } as const;

export function Painel({ titulo, icone, extra, className, children }: {
  titulo: string;
  icone: string;
  extra?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={`painel ${className ?? ""}`}>
      <header className="painel-cabeca">
        <span className="painel-icone">{icone}</span>
        <h2>{titulo}</h2>
        {extra && <span className="painel-extra">{extra}</span>}
      </header>
      {children}
    </section>
  );
}

export function PainelAgenda({ agenda, varredura, pendente }: {
  agenda: EventoAgenda[];
  varredura: number;
  pendente: Pendente | null;
}) {
  const agora = agoraHHMM();
  const indiceAgora = agenda.findIndex((e) => e.inicio > agora);
  const alvoApagar = pendente?.ferramenta === "agenda_apagar" ? pendente.evento?.id : undefined;
  return (
    <Painel titulo="Agenda de hoje" icone="◷" extra={hoje()} className="painel-agenda">
      <div className="agenda-corpo">
        <AnimatePresence>
          {varredura > 0 && (
            <motion.div
              key={varredura}
              className="varredura"
              initial={{ top: "-10%", opacity: 0 }}
              animate={{ top: "105%", opacity: [0, 1, 1, 0] }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.1, ease: "easeInOut" }}
            />
          )}
        </AnimatePresence>
        <ul className="agenda-lista">
          <AnimatePresence initial={false}>
            {agenda.map((ev, i) => (
              <motion.li
                key={ev.id}
                layout
                layoutId={`evt-${ev.anima ?? ev.id}`}
                className={`agenda-item ${ev.id === alvoApagar ? "alvo-apagar" : ""} ${i === indiceAgora ? "depois-de-agora" : ""}`}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1, boxShadow: ["0 0 0px rgba(64,196,255,0)", "0 0 26px rgba(64,196,255,0.55)", "0 0 0px rgba(64,196,255,0)"] }}
                exit={{ opacity: 0, scale: 1.12, filter: "blur(12px)", transition: { duration: 0.7 } }}
                transition={mola}
              >
                <span className="agenda-hora">{ev.diaInteiro ? "dia" : ev.inicio}</span>
                <span className="agenda-texto">
                  <strong>{ev.titulo}</strong>
                  {(ev.fim || ev.local) && (
                    <small>
                      {ev.fim && `até ${ev.fim}`}
                      {ev.fim && ev.local && " · "}
                      {ev.local}
                    </small>
                  )}
                </span>
              </motion.li>
            ))}
          </AnimatePresence>
          {agenda.length === 0 && <li className="vazio">Nada marcado para hoje.</li>}
        </ul>
      </div>
    </Painel>
  );
}

export const PainelMemoria = forwardRef<HTMLElement, { memorias: Memoria[] }>(function PainelMemoria({ memorias }, ref) {
  return (
    <section className="painel painel-memoria" ref={ref}>
      <header className="painel-cabeca">
        <span className="painel-icone">✦</span>
        <h2>Memória</h2>
        <span className="painel-extra">{memorias.length} fatos</span>
      </header>
      <ul className="memoria-lista">
        <AnimatePresence initial={false}>
          {memorias.slice(0, 6).map((m) => (
            <motion.li
              key={m.id}
              layout
              className="memoria-item"
              initial={{ opacity: 0, x: -24, backgroundColor: "rgba(179,136,255,0.35)" }}
              animate={{ opacity: 1, x: 0, backgroundColor: "rgba(179,136,255,0.06)" }}
              exit={{ opacity: 0, x: 24 }}
              transition={{ ...mola, delay: 0.45 }}
            >
              {m.texto}
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </section>
  );
});

export function PainelConversa({ conversa }: { conversa: Mensagem[] }) {
  const fim = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Sem retornar nada: no Chromium novo o scrollIntoView devolve uma Promise, e o React a trataria como limpeza.
    fim.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversa]);
  return (
    <Painel titulo="Conversa" icone="◈" className="painel-conversa">
      <div className="conversa-lista">
        {conversa.length === 0 && <p className="vazio">Fale ou digite para começar.</p>}
        <AnimatePresence initial={false}>
          {conversa.map((m) => (
            <motion.div
              key={m.id}
              layout="position"
              className={`msg msg-${m.autor}`}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28 }}
            >
              <p>
                {m.texto}
                {m.parcial && <span className="cursor" />}
              </p>
              {m.ferramentas && (
                <div className="chips">
                  {m.ferramentas.map((f) => (
                    <span key={f} className="chip">
                      {rotuloFerramenta(f)}
                    </span>
                  ))}
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
        <div ref={fim} />
      </div>
    </Painel>
  );
}

export function PainelStatus({ status, estado }: { status: Status; estado: string }) {
  const google =
    status.googleDias === null ? "sem login" : status.googleDias <= 1 ? "vence amanhã" : `vence em ${status.googleDias} dias`;
  const linhas: [string, string, string?][] = [
    ["Modelo", status.modelo],
    ["VRAM", status.vram],
    ["Microfone", status.microfone],
    ["Google Agenda", google, status.googleDias !== null && status.googleDias <= 1 ? "alerta" : undefined],
    ["Modo jogo", status.modoJogo ? "ligado" : "desligado"],
  ];
  return (
    <Painel titulo="Status" icone="◉" extra={<span className={`ponto ponto-${estado}`} />} className="painel-status">
      <dl className="status-lista">
        {linhas.map(([k, v, classe]) => (
          <div key={k} className={classe}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Painel>
  );
}
