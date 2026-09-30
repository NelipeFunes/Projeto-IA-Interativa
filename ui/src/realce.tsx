// O modelo às vezes responde com **negrito** e *itálico*. Aqui isso vira elemento de verdade, sem
// injetar HTML: o texto é quebrado em pedaços e cada um vira um nó do React (nada vira código).
import type { ReactNode } from "react";

const MARCAS = /(\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;

export function realcar(texto: string): ReactNode[] {
  return texto.split(MARCAS).map((pedaco, i) => {
    if (pedaco.startsWith("**") && pedaco.endsWith("**") && pedaco.length > 4) {
      return <strong key={i}>{pedaco.slice(2, -2)}</strong>;
    }
    if (pedaco.startsWith("*") && pedaco.endsWith("*") && pedaco.length > 2) {
      return <em key={i}>{pedaco.slice(1, -1)}</em>;
    }
    return pedaco;
  });
}
