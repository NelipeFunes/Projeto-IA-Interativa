// Barra superior própria (a janela não tem bordas do Windows), campo de entrada e controles da demonstração.
import { useState } from "react";
import { chamar, noApp } from "../ponte";
import type { Estado } from "../tipos";

export function BarraTitulo({ nome, estado, demo }: { nome: string; estado: Estado; demo: boolean }) {
  return (
    <div className="barra pywebview-drag-region">
      <span className="marca">
        <span className="marca-orbe" />
        {nome.toUpperCase()}
      </span>
      <span className={`ponto ponto-${estado}`} />
      {demo && <span className="selo">demonstração</span>}
      <span className="espaco" />
      {noApp() && (
        <span className="botoes-janela">
          <button aria-label="Minimizar" onClick={() => chamar("minimizar")}>
            ─
          </button>
          <button aria-label="Fechar" className="fechar" onClick={() => chamar("fechar")}>
            ✕
          </button>
        </span>
      )}
    </div>
  );
}

export function BarraEntrada({ aoEnviar, ouvindo, aoMicrofone }: {
  aoEnviar: (texto: string) => void;
  ouvindo: boolean;
  aoMicrofone: (segurando: boolean) => void;
}) {
  const [texto, setTexto] = useState("");
  return (
    <form
      className="entrada"
      onSubmit={(e) => {
        e.preventDefault();
        if (texto.trim()) aoEnviar(texto.trim());
        setTexto("");
      }}
    >
      <input value={texto} onChange={(e) => setTexto(e.target.value)} placeholder="Digite uma mensagem…" />
      <button
        type="button"
        className={`microfone ${ouvindo ? "ativo" : ""}`}
        aria-label="Segure para falar"
        onPointerDown={() => aoMicrofone(true)}
        onPointerUp={() => aoMicrofone(false)}
        onPointerLeave={() => ouvindo && aoMicrofone(false)}
      >
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
          <path
            fill="currentColor"
            d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"
          />
        </svg>
      </button>
      <button type="submit" className="enviar" aria-label="Enviar">
        ➤
      </button>
    </form>
  );
}

export function ControlesDemo({ aoReiniciar }: { aoReiniciar: () => void }) {
  return (
    <div className="controles-demo">
      <button onClick={aoReiniciar}>↺ Reiniciar demonstração</button>
      <button onClick={() => (noApp() ? chamar("mostrar_bolha") : window.open("?janela=bolha&demo=1", "_blank", "width=400,height=130"))}>
        ◌ Simular bolha
      </button>
    </div>
  );
}
