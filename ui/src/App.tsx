import { LayoutGroup, motion } from "motion/react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Centro } from "./componentes/Centro";
import { BarraEntrada, BarraTitulo, ControlesDemo } from "./componentes/Moldura";
import { Nebulosa } from "./componentes/Nebulosa";
import { PainelAgenda, PainelConversa, PainelMemoria, PainelStatus } from "./componentes/Paineis";
import { Demo, eventosAte } from "./demo";
import { inicial, reduzir } from "./estado";
import { noApp, parametros } from "./ponte";

// Fase A: a tela roda o roteiro de demonstração. Na Fase C a fonte vira o WebSocket do núcleo.
const DEMO = true;
const PASSO_FIXO = parametros.get("passo"); // ?passo=N congela a tela num momento do roteiro (capturas)

export function App() {
  const [s, emitir] = useReducer(reduzir, inicial, (i) =>
    PASSO_FIXO !== null ? eventosAte(Number(PASSO_FIXO)).reduce(reduzir, i) : i,
  );
  const demo = useRef<Demo | null>(null);
  const orbeRef = useRef<HTMLDivElement>(null);
  const memoriaRef = useRef<HTMLElement>(null);
  const [voo, setVoo] = useState<{ id: number; de: [number, number]; para: [number, number] } | null>(null);

  const reiniciar = useCallback(() => {
    if (!DEMO || PASSO_FIXO !== null) return;
    demo.current?.parar();
    emitir({ tipo: "painel", agenda: [], memorias: [] });
    demo.current = new Demo(emitir, { autoConfirmarMs: noApp() ? 9000 : 2500, repetir: true });
    demo.current.iniciar();
  }, []);

  useEffect(() => {
    reiniciar();
    return () => demo.current?.parar();
  }, [reiniciar]);

  // Estrela: quando uma memória é guardada, uma luz sai do orbe e entra no painel de memória.
  useEffect(() => {
    if (!s.estrela) return;
    const o = orbeRef.current?.getBoundingClientRect();
    const m = memoriaRef.current?.getBoundingClientRect();
    if (o && m) setVoo({ id: s.estrela, de: [o.left + o.width / 2, o.top + o.height / 2], para: [m.left + 48, m.top + 64] });
  }, [s.estrela]);

  const ultimaFala = [...s.conversa].reverse().find((m) => m.autor === "voce")?.texto;

  return (
    <LayoutGroup>
      <Nebulosa estado={s.estado} nivelVoz={s.nivelVoz} />
      <div className="app">
        <BarraTitulo nome={s.nome} estado={s.estado} demo={DEMO} />
        <main className="principal">
          <div className="coluna">
            <PainelAgenda agenda={s.agenda} varredura={s.varredura} pendente={s.pendente} />
            <PainelMemoria ref={memoriaRef} memorias={s.memorias} />
          </div>
          <div className="coluna coluna-centro">
            <Centro
              ref={orbeRef}
              estado={s.estado}
              nivelMic={s.nivelMic}
              nivelVoz={s.nivelVoz}
              ultimaFala={ultimaFala}
              pendente={s.pendente}
              aoResponder={(id, sim) => demo.current?.responder(id, sim)}
            />
            <BarraEntrada
              ouvindo={s.estado === "ouvindo"}
              aoEnviar={(texto) => {
                emitir({ tipo: "fala_usuario", texto, canal: "texto" });
                setTimeout(
                  () => emitir({ tipo: "resposta", texto: "Isto é só a demonstração: quando eu estiver ligado ao núcleo, respondo de verdade." }),
                  700,
                );
              }}
              aoMicrofone={(segurando) => emitir({ tipo: "estado", valor: segurando ? "ouvindo" : "ocioso" })}
            />
          </div>
          <div className="coluna">
            <PainelConversa conversa={s.conversa} />
            <PainelStatus status={s.status} estado={s.estado} />
          </div>
        </main>
        {DEMO && PASSO_FIXO === null && <ControlesDemo aoReiniciar={reiniciar} />}
      </div>
      {voo && (
        <motion.div
          key={voo.id}
          className="estrela-voando"
          initial={{ x: voo.de[0], y: voo.de[1], scale: 0.3, opacity: 0 }}
          animate={{ x: voo.para[0], y: voo.para[1], scale: [0.3, 1.6, 0.5], opacity: [0, 1, 1, 0] }}
          transition={{ duration: 1, ease: [0.45, 0, 0.2, 1] }}
          onAnimationComplete={() => setVoo(null)}
        />
      )}
    </LayoutGroup>
  );
}
