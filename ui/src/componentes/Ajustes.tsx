import { motion } from "motion/react";
import { useEffect, useState } from "react";
import type { CampoAjuste, DadosAjustes, ValorAjuste } from "../tipos";

const INICIO = "inicia_com_windows";

/** Tela de ajustes: o núcleo manda os valores e as opções; aqui só se escolhe e manda o que mudou. */
export function Ajustes({ dados, aoFechar, aoSalvar, aoOuvir }: {
  dados: DadosAjustes | null;
  aoFechar: () => void;
  aoSalvar: (valores: Record<string, ValorAjuste>) => void;
  aoOuvir: (voz: string) => void;
}) {
  const [form, setForm] = useState<Record<string, ValorAjuste>>(dados?.valores ?? {});
  useEffect(() => {
    if (dados) setForm(dados.valores); // chegou do núcleo (abriu ou acabou de salvar): mostra o que vale
  }, [dados]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && aoFechar();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [aoFechar]);

  const mudancas = dados
    ? Object.fromEntries(Object.entries(form).filter(([k, v]) => dados.valores[k] !== v))
    : {};
  const mudou = Object.keys(mudancas).length > 0;
  const mudar = (chave: string, valor: ValorAjuste) => setForm((f) => ({ ...f, [chave]: valor }));

  return (
    <motion.div className="ajustes-fundo" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      onMouseDown={(e) => e.target === e.currentTarget && aoFechar()}>
      <motion.form
        className="painel ajustes"
        role="dialog"
        aria-label="Ajustes"
        initial={{ y: 12, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (mudou) aoSalvar(mudancas);
        }}
      >
        <div className="painel-cabeca">
          <span className="painel-icone">⚙</span>
          <h2>Ajustes</h2>
          <button type="button" className="ajustes-fechar" aria-label="Fechar ajustes" onClick={aoFechar}>✕</button>
        </div>
        {!dados ? (
          <p className="vazio">Carregando…</p>
        ) : (
          <div className="ajustes-corpo">
            {dados.campos.map((c) => (
              <Campo key={c.chave} campo={c} valor={form[c.chave] ?? null} opcoes={dados.opcoes[c.chave] ?? []}
                aoMudar={(v) => mudar(c.chave, v)} aoOuvir={c.chave === "voz.voz_piper" ? aoOuvir : undefined} />
            ))}
            <label className="ajuste ajuste-linha">
              <input type="checkbox" checked={form[INICIO] === true} onChange={(e) => mudar(INICIO, e.target.checked)} />
              <span>Iniciar com o Windows</span>
            </label>
            <Mensagem dados={dados} />
            <div className="ajustes-botoes">
              <button type="button" className="botao botao-nao" onClick={aoFechar}>Fechar</button>
              <button type="submit" className="botao botao-sim" disabled={!mudou}>Salvar</button>
            </div>
          </div>
        )}
      </motion.form>
    </motion.div>
  );
}

function Campo({ campo, valor, opcoes, aoMudar, aoOuvir }: {
  campo: CampoAjuste;
  valor: ValorAjuste;
  opcoes: string[];
  aoMudar: (v: ValorAjuste) => void;
  aoOuvir?: (voz: string) => void;
}) {
  const id = `ajuste-${campo.chave}`;
  return (
    <div className="ajuste">
      <label htmlFor={id}>
        {campo.rotulo}
        {!campo.aoVivo && <span className="ajuste-nota"> · vale ao reiniciar</span>}
      </label>
      <div className="ajuste-linha">
        {campo.tipo === "escolha" ? (
          <select id={id} value={typeof valor === "string" ? valor : ""} onChange={(e) => aoMudar(e.target.value)}>
            {typeof valor !== "string" && <option value="">—</option>}
            {opcoes.map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        ) : campo.tipo === "numero" ? (
          <input id={id} type="number" value={typeof valor === "number" ? valor : ""}
            min={campo.minimo ?? undefined} max={campo.maximo ?? undefined}
            step={campo.maximo !== null && campo.maximo !== undefined && campo.maximo <= 5 ? 0.05 : 10}
            onChange={(e) => aoMudar(e.target.value === "" ? null : Number(e.target.value))} />
        ) : (
          <input id={id} type="text" maxLength={20} value={typeof valor === "string" ? valor : ""}
            onChange={(e) => aoMudar(e.target.value)} />
        )}
        {aoOuvir && typeof valor === "string" && valor && (
          <button type="button" className="ajuste-ouvir" onClick={() => aoOuvir(valor)} title="Ouvir esta voz">
            ▶ Ouvir
          </button>
        )}
      </div>
    </div>
  );
}

function Mensagem({ dados }: { dados: DadosAjustes }) {
  if (dados.erro) return <p className="ajustes-msg erro">{dados.erro}</p>;
  if (!dados.salvo) return null;
  return (
    <p className="ajustes-msg">
      {dados.reiniciar
        ? "Salvo. O que diz \"vale ao reiniciar\" entra quando o Vision reiniciar (bandeja → Sair, e abrir de novo)."
        : "Salvo."}
    </p>
  );
}
