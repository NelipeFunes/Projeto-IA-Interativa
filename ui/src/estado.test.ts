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

  it("links da busca na web vão para a resposta do turno, só http(s), e não vazam para o próximo", () => {
    const links = [
      { titulo: "Casas para alugar", url: "https://imoveis.exemplo.com/casas", site: "imoveis.exemplo.com" },
      { titulo: "Ruim", url: "javascript:alert(1)", site: "x" },
    ];
    const s = aplicar([
      { tipo: "fala_usuario", texto: "busca casas", canal: "voz" },
      { tipo: "ferramenta_fim", nome: "web_buscar", ok: true, dados: { consulta: "casas", links } },
      { tipo: "resposta", texto: "Achei três." },
      { tipo: "fala_usuario", texto: "obrigado", canal: "voz" },
      { tipo: "resposta", texto: "De nada." },
    ]);
    expect(s.conversa[1].links).toEqual([links[0]]);
    expect(s.conversa[3].links).toBeUndefined();
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

describe("eventos do núcleo", () => {
  const cartao = { id: "p1", ferramenta: "agenda_criar", descricao: "Vou criar…", evento: { id: "p1", titulo: "Barbeiro", inicio: "16:00" } };

  it("criar confirmado: o evento real entra UMA vez, com o id do Google, e mantém o voo do cartão", () => {
    const real = { id: "google123", titulo: "Barbeiro", inicio: "16:00", fim: "17:00" };
    const s = aplicar([
      { tipo: "painel", agenda: AGENDA_EXEMPLO },
      { tipo: "pendente", pendente: cartao },
      { tipo: "ferramenta_fim", nome: "agenda_criar", ok: true, dados: real }, // chega antes do resolvido
      { tipo: "pendente_resolvido", id: "p1", resultado: "executada", evento: real },
    ]);
    const barbeiros = s.agenda.filter((e) => e.titulo === "Barbeiro");
    expect(barbeiros).toHaveLength(1);
    expect(barbeiros[0]).toMatchObject({ id: "google123", anima: "p1" });
  });

  it("criado sem cartão na tela (outro canal) entra direto", () => {
    const s = aplicar([{ tipo: "ferramenta_fim", nome: "agenda_criar", ok: true, dados: { id: "g1", titulo: "X", inicio: "10:00" } }]);
    expect(s.agenda.map((e) => e.id)).toEqual(["g1"]);
  });

  it("alterado para outro dia sai do painel; esquecer tira a memória", () => {
    const s = aplicar([
      { tipo: "painel", agenda: AGENDA_EXEMPLO, memorias: [{ id: 1, texto: "a" }, { id: 2, texto: "b" }] },
      { tipo: "ferramenta_fim", nome: "agenda_alterar", ok: true, args: { evento_id: AGENDA_EXEMPLO[0].id }, dados: null },
      { tipo: "ferramenta_fim", nome: "esquecer", ok: true, dados: { id: 1 } },
    ]);
    expect(s.agenda.find((e) => e.id === AGENDA_EXEMPLO[0].id)).toBeUndefined();
    expect(s.memorias.map((m) => m.id)).toEqual([2]);
  });

  it("limpar a conversa (bolha reaparecendo) não mexe no estado do orbe", () => {
    const s = aplicar([
      { tipo: "fala_usuario", texto: "oi", canal: "voz" },
      { tipo: "estado", valor: "ouvindo" },
    ]);
    const limpo = reduzir(s, { tipo: "limpar_conversa" });
    expect(limpo.conversa).toEqual([]);
    expect(limpo.estado).toBe("ouvindo");
  });

  it("ajustes do núcleo ficam guardados para a tela de ajustes", () => {
    const s = aplicar([{ tipo: "ajustes", valores: { "voz.velocidade_fala": 1.1 }, campos: [], opcoes: {}, salvo: true }]);
    expect(s.ajustes).toEqual({ valores: { "voz.velocidade_fala": 1.1 }, campos: [], opcoes: {}, salvo: true });
  });
});
