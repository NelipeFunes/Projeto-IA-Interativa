import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type Aba, Ajustes } from "./componentes/Ajustes";
import { Centro } from "./componentes/Centro";
import { type AcoesConexoes, Conexoes } from "./componentes/Conexoes";
import { BarraEntrada, ControlesDemo, ControlesJanela } from "./componentes/Moldura";
import { Nebulosa } from "./componentes/Nebulosa";
import { PainelAgenda, PainelConversa, PainelMemoria, PainelStatus } from "./componentes/Paineis";
import { type Conexao, conectar } from "./conexao";
import { AJUSTES_DEMO, CONEXOES_DEMO, Demo, eventosAte } from "./demo";
import { type Acao, inicial, reduzir } from "./estado";
import { pausaGl } from "./gl";
import { niveis } from "./niveis";
import { chamar, noApp, parametros } from "./ponte";

// Duas fontes de eventos: o núcleo (?nucleo=1, a janela de verdade) ou o roteiro de demonstração.
export const NUCLEO = parametros.get("nucleo") === "1";
const DEMO = !NUCLEO;
const PASSO_FIXO = parametros.get("passo"); // ?passo=N congela a demo num momento do roteiro (capturas)

/** true depois que o pywebview injeta a ponte (ele faz isso DEPOIS de a página montar). */
export function useNoApp(): boolean {
  const [app, setApp] = useState(noApp);
  useEffect(() => {
    const pronto = () => setApp(true);
    window.addEventListener("pywebviewready", pronto);
    return () => window.removeEventListener("pywebviewready", pronto);
  }, []);
  return app;
}

/** Liga a tela no núcleo. Devolve a conexão (para mandar comandos) e se ela está de pé. */
export function useNucleo(ligado: boolean, emitir: (ev: Acao) => void): [React.RefObject<Conexao | null>, boolean] {
  const conexao = useRef<Conexao | null>(null);
  const [online, setOnline] = useState(false);
  useEffect(() => {
    if (!ligado) return;
    conexao.current = conectar(emitir, setOnline);
    return () => conexao.current?.fechar();
  }, [ligado, emitir]);
  return [conexao, online];
}

export function App() {
  const [s, despachar] = useReducer(reduzir, inicial, (i) =>
    DEMO && PASSO_FIXO !== null ? eventosAte(Number(PASSO_FIXO)).reduce(reduzir, i) : i,
  );
  // O volume do microfone/voz vai direto para os shaders (niveis.ts), sem redesenhar a tela.
  const emitir = useCallback((ev: Acao) => {
    if (ev.tipo === "nivel") niveis[ev.fonte] = ev.valor;
    else despachar(ev);
  }, []);
  const app = useNoApp();
  const [conexao, online] = useNucleo(NUCLEO, emitir);
  const demo = useRef<Demo | null>(null);
  const orbeRef = useRef<HTMLDivElement>(null);
  const memoriaRef = useRef<HTMLElement>(null);
  const [voo, setVoo] = useState<{ id: number; de: [number, number]; para: [number, number] } | null>(null);
  const [avisoFechado, setAvisoFechado] = useState<string | null>(null);
  // ?ajustes=1 abre os ajustes; ?ajustes=conexoes, já na aba de Conexões (capturas da demo).
  const [ajustesAbertos, setAjustesAbertos] = useState(parametros.has("ajustes"));
  const [aba, setAba] = useState<Aba>(parametros.get("ajustes") === "conexoes" ? "conexoes" : "geral");

  const trocarAba = useCallback((nova: Aba) => {
    setAba(nova);
    if (nova === "conexoes") {
      if (NUCLEO) conexao.current?.enviar({ tipo: "conexoes" });
      else emitir({ tipo: "conexoes", ...CONEXOES_DEMO });
    } else if (NUCLEO) conexao.current?.enviar({ tipo: "ajustes" });
    else emitir({ tipo: "ajustes", ...AJUSTES_DEMO });
  }, [conexao, emitir]);
  const abrirAjustes = (abaInicial: Aba = "geral") => {
    setAjustesAbertos(true);
    trocarAba(abaInicial);
  };
  const acoesConexoes: AcoesConexoes = {
    conectar: (servico, dados) => (NUCLEO ? conexao.current?.enviar({ tipo: "conectar", servico, dados }) : undefined),
    desconectar: (servico) => (NUCLEO ? conexao.current?.enviar({ tipo: "desconectar", servico }) : undefined),
    ligar: (servico, ligado) => (NUCLEO ? conexao.current?.enviar({ tipo: "ligar_conexao", servico, ligado }) : undefined),
    cancelar: (servico) => (NUCLEO ? conexao.current?.enviar({ tipo: "cancelar_conexao", servico }) : undefined),
    reiniciar: () => (NUCLEO ? conexao.current?.enviar({ tipo: "reiniciar" }) : undefined),
    // Na janela, o navegador padrão (a ponte só aceita http/https); na demo, uma aba nova.
    abrirLink: (url) => (app ? chamar("abrir_link", url) : window.open(url, "_blank", "noopener")),
  };
  const fecharAjustes = useCallback(() => setAjustesAbertos(false), []);
  useEffect(() => {
    pausaGl.jogo = s.estado === "jogo"; // a nebulosa e o orbe param: a GPU é do jogo
  }, [s.estado]);
  useEffect(() => {
    if (DEMO && parametros.has("ajustes")) {
      emitir({ tipo: "ajustes", ...AJUSTES_DEMO });
      emitir({ tipo: "conexoes", ...CONEXOES_DEMO });
    }
  }, [emitir]);

  const reiniciar = useCallback(() => {
    if (!DEMO || PASSO_FIXO !== null) return;
    demo.current?.parar();
    emitir({ tipo: "reiniciar" });
    // No app espera mais antes de confirmar sozinha (dá tempo de clicar); no navegador, anda rápido.
    demo.current = new Demo(emitir, { autoConfirmarMs: () => (noApp() ? 9000 : 2500), repetir: true });
    demo.current.iniciar();
  }, [emitir]);

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
  const aviso = NUCLEO && !online ? "Sem conexão com o núcleo. Tentando de novo…" : s.aviso;

  const enviarTexto = (texto: string) => {
    if (NUCLEO) {
      conexao.current?.enviar({ tipo: "texto", texto });
      return;
    }
    emitir({ tipo: "fala_usuario", texto, canal: "texto" });
    setTimeout(() => emitir({ tipo: "resposta", texto: "Isto é só a demonstração: aqui eu não respondo de verdade." }), 700);
  };

  return (
    <LayoutGroup>
      <Nebulosa estado={s.estado} />
      <div className="app">
        <ControlesJanela app={app} />
        <button className="botao-ajustes" aria-label="Ajustes" title="Ajustes" onClick={() => abrirAjustes()}>
          ⚙
        </button>
        <AnimatePresence>
          {aviso && aviso !== avisoFechado && (
            <motion.div
              key={aviso}
              className="aviso"
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
            >
              <span>{aviso}</span>
              {online && (
                <button aria-label="Fechar aviso" onClick={() => setAvisoFechado(aviso)}>
                  ✕
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>
        <main className="principal">
          <div className="coluna">
            <PainelAgenda agenda={s.agenda} varredura={s.varredura} pendente={s.pendente} />
            <PainelMemoria ref={memoriaRef} memorias={s.memorias} />
          </div>
          <div className="coluna coluna-centro">
            <Centro
              ref={orbeRef}
              estado={s.estado}
              ultimaFala={ultimaFala}
              pendente={s.pendente}
              aoResponder={(id, sim) =>
                NUCLEO ? conexao.current?.enviar({ tipo: "confirmar", id, sim }) : demo.current?.responder(id, sim)
              }
            />
            <BarraEntrada
              ouvindo={s.estado === "ouvindo"}
              aoEnviar={enviarTexto}
              aoMicrofone={(segurando) =>
                NUCLEO
                  ? conexao.current?.enviar({ tipo: "ouvir", segurando })
                  : emitir({ tipo: "estado", valor: segurando ? "ouvindo" : "ocioso" })
              }
            />
          </div>
          <div className="coluna">
            <PainelConversa conversa={s.conversa} />
            <PainelStatus status={s.status} estado={s.estado} />
          </div>
        </main>
        {DEMO && PASSO_FIXO === null && <ControlesDemo app={app} aoReiniciar={reiniciar} />}
        <AnimatePresence>
          {ajustesAbertos && (
            <Ajustes
              dados={s.ajustes}
              aoFechar={fecharAjustes}
              aoSalvar={(valores) =>
                NUCLEO
                  ? conexao.current?.enviar({ tipo: "salvar_ajustes", valores })
                  : emitir({ tipo: "ajustes", ...AJUSTES_DEMO, valores: { ...AJUSTES_DEMO.valores, ...valores }, salvo: true,
                      reiniciar: "assistente.nome" in valores || "voz.microfone" in valores })
              }
              aoOuvir={(voz) => NUCLEO && conexao.current?.enviar({ tipo: "amostra_voz", voz })}
              aba={aba}
              aoTrocarAba={trocarAba}
              conexoes={<Conexoes dados={s.conexoes} acoes={acoesConexoes} />}
              calibracao={s.calibracao}
              aoCalibrar={() => NUCLEO
                ? conexao.current?.enviar({ tipo: "calibrar_ativacao" })
                : emitir({ tipo: "calibracao", rodando: false, ouvidos: [{ texto: "Hey Vision", acordou: true },
                    { texto: "Deliving", acordou: false }, { texto: "Deliving.", acordou: false },
                    { texto: "Hey deliving", acordou: false }, { texto: "", acordou: false }],
                  sugeridos: ["deliving"], apelidos: [], acertos: 1,
                  dica: "Ele escreve o seu \"Vision\" como: deliving. Se foi mesmo você chamando, aceite." })}
              aoEsquecerApelidos={(apelido) => NUCLEO
                ? conexao.current?.enviar({ tipo: "esquecer_apelidos", ...(apelido ? { apelido } : {}) })
                : emitir({ tipo: "calibracao", rodando: false, apelidos: [], dica: "Esqueci." })}
              aoAceitarApelidos={(apelidos) => NUCLEO
                ? conexao.current?.enviar({ tipo: "aceitar_apelidos", apelidos })
                : emitir({ tipo: "calibracao", rodando: false, apelidos, dica: "Pronto: teste o \"Hey Vision\" agora." })}
            />
          )}
        </AnimatePresence>
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
