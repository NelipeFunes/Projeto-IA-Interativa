import { describe, expect, it } from "vitest";
import { AGENDA_EXEMPLO, eventosAte, PASSOS } from "./demo";
import { inicial, LIMITE_CONVERSA, reduzir } from "./estado";
import type { Evento } from "./tipos";

const aplicar = (eventos: Evento[]) => eventos.reduce(reduzir, inicial);

describe("redutor", () => {
  it("resposta parcial acumula e a final fecha a mesma mensagem, com as ferramentas do turno", () => {
    const s = aplicar([
      { tipo: "fala_usuario", texto: "agenda?", canal: "voz" },
      { tipo: "ferramenta_inicio", nome: "agenda_listar" },
      { tipo: "resposta_parcial", texto: "Hoje você " },
      { tipo: "resposta_parcial", texto: "tem aula." },
      { tipo: "resposta", texto: "Hoje você tem aula." },
    ]);
    expect(s.conversa).toHaveLength(2);
    expect(s.conversa[1]).toMatchObject({ autor: "assistente", texto: "Hoje você tem aula.", ferramentas: ["agenda_listar"] });
    expect(s.conversa[1].parcial).toBeUndefined();
  });

  it("consultar a agenda dispara a varredura", () => {
    const s = aplicar([{ tipo: "ferramenta_inicio", nome: "agenda_listar" }]);
    expect(s.varredura).toBeGreaterThan(0);
  });

  it("confirmar a criação põe o evento no horário certo da agenda", () => {
    const novo = { id: "n1", titulo: "Barbeiro", inicio: "16:00", fim: "17:00" };
    const s = aplicar([
      { tipo: "painel", agenda: AGENDA_EXEMPLO },
      { tipo: "pendente", pendente: { id: "p1", ferramenta: "agenda_criar", descricao: "…", evento: novo } },
      { tipo: "pendente_resolvido", id: "p1", resultado: "executada" },
    ]);
    expect(s.pendente).toBeNull();
    const horarios = s.agenda.map((e) => e.inicio);
    expect(horarios).toEqual([...horarios].sort());
    expect(s.agenda.find((e) => e.id === "n1")).toBeTruthy();
  });

  it("cancelar não mexe na agenda", () => {
    const s = aplicar([
      { tipo: "painel", agenda: AGENDA_EXEMPLO },
      { tipo: "pendente", pendente: { id: "p1", ferramenta: "agenda_criar", descricao: "…", evento: { id: "n1", titulo: "X", inicio: "16:00" } } },
      { tipo: "pendente_resolvido", id: "p1", resultado: "cancelada" },
    ]);
    expect(s.pendente).toBeNull();
    expect(s.agenda).toHaveLength(AGENDA_EXEMPLO.length);
  });

  it("apagar confirmado remove o evento; guardar memória põe no topo e dispara a estrela", () => {
    const s = aplicar([
      { tipo: "painel", agenda: AGENDA_EXEMPLO, memorias: [{ id: 1, texto: "a" }] },
      { tipo: "pendente", pendente: { id: "p2", ferramenta: "agenda_apagar", descricao: "…", evento: AGENDA_EXEMPLO[4] } },
      { tipo: "pendente_resolvido", id: "p2", resultado: "executada" },
      { tipo: "ferramenta_fim", nome: "guardar_memoria", ok: true, dados: { id: 9, texto: "novo" } },
    ]);
    expect(s.agenda.find((e) => e.id === AGENDA_EXEMPLO[4].id)).toBeUndefined();
    expect(s.memorias[0]).toEqual({ id: 9, texto: "novo" });
    expect(s.estrela).toBeGreaterThan(0);
  });

  it("o volume não entra no estado da tela (vai direto para os shaders)", () => {
    const antes = aplicar([{ tipo: "estado", valor: "ouvindo" }]);
    expect(reduzir(antes, { tipo: "nivel", fonte: "mic", valor: 0.8 })).toBe(antes);
  });

  it("a conversa tem limite e reiniciar limpa tudo menos o nome", () => {
    const falas: Evento[] = Array.from({ length: LIMITE_CONVERSA + 30 }, (_, i) => ({
      tipo: "fala_usuario",
      texto: `fala ${i}`,
      canal: "texto",
    }));
    const s = aplicar([{ tipo: "painel", nome: "Nova", agenda: AGENDA_EXEMPLO }, ...falas]);
    expect(s.conversa).toHaveLength(LIMITE_CONVERSA);
    expect(s.conversa.at(-1)?.texto).toBe(`fala ${LIMITE_CONVERSA + 29}`);
    const limpo = reduzir(s, { tipo: "reiniciar" });
    expect(limpo.conversa).toEqual([]);
    expect(limpo.agenda).toEqual([]);
    expect(limpo.nome).toBe("Nova");
  });
});

describe("roteiro de demonstração", () => {
  it("do começo ao fim: barbeiro entra, memória nova, aula de inglês sai, termina ocioso", () => {
    const s = aplicar(eventosAte(PASSOS.length - 1));
    expect(s.agenda.some((e) => e.titulo === "Barbeiro")).toBe(true);
    expect(s.agenda.some((e) => e.titulo === "Aula de inglês")).toBe(false);
    expect(s.memorias[0].texto).toMatch(/sextas/);
    expect(s.pendente).toBeNull();
    expect(s.estado).toBe("ocioso");
  });

  it("não usa dados pessoais (repositório público)", () => {
    const texto = JSON.stringify(PASSOS);
    expect(texto).not.toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i); // nada de e-mail no roteiro
  });
});
