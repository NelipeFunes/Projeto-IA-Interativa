import { isValidElement } from "react";
import { describe, expect, it } from "vitest";
import { realcar } from "./realce";

describe("realce do texto do modelo", () => {
  it("negrito e itálico viram elementos; o resto continua texto puro", () => {
    const partes = realcar("Hoje tem **Cinema** com *amigos* às 19h.");
    expect(partes.filter(isValidElement).map((p) => (p as { type: string }).type)).toEqual(["strong", "em"]);
    expect(partes.join("")).toContain("Hoje tem ");
  });

  it("HTML no texto nunca vira elemento", () => {
    const partes = realcar('<img src=x onerror="alert(1)"> **oi**');
    expect(partes[0]).toBe('<img src=x onerror="alert(1)"> ');
  });
});
