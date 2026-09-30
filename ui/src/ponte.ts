// Ponte com a janela nativa (pywebview). No navegador comum ela simplesmente não existe e tudo vira no-op.

type Api = Record<string, (...args: unknown[]) => Promise<unknown>>;

declare global {
  interface Window {
    pywebview?: { api: Api };
  }
}

export const noApp = () => Boolean(window.pywebview?.api);

export function chamar(metodo: string, ...args: unknown[]): Promise<unknown> | undefined {
  return window.pywebview?.api?.[metodo]?.(...args);
}

export const parametros = new URLSearchParams(window.location.search);
