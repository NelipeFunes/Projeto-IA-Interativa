import { useEffect, useState } from "react";
import type { Andamento, CampoConexao, DadosConexoes, Servico } from "../tipos";

export interface AcoesConexoes {
  conectar: (servico: string, dados: Record<string, string>) => void;
  desconectar: (servico: string) => void;
  ligar: (servico: string, ligado: boolean) => void;
  cancelar: (servico: string) => void;
  reiniciar: () => void;
  abrirLink: (url: string) => void;
}

const ROTULO_SITUACAO: Record<Servico["situacao"], string> = {
  ok: "Conectado",
  atencao: "Atenção",
  falta: "Não conectado",
  desligado: "Desligado",
};

/** Aba "Conexões" dos ajustes: um cartão por serviço. O núcleo diz o estado; aqui só se pede. */
export function Conexoes({ dados, acoes }: { dados: DadosConexoes | null; acoes: AcoesConexoes }) {
  const [reiniciando, setReiniciando] = useState(false);
  if (!dados) return <p className="vazio">Carregando…</p>;
  return (
    <div className="conexoes">
      {dados.reiniciar.length > 0 && (
        <div className="conexoes-reiniciar" role="status">
          <span>
            {reiniciando
              ? "Reiniciando… a janela fecha e abre de novo em alguns segundos."
              : `${juntar(dados.reiniciar)} ${dados.reiniciar.length > 1 ? "mudaram" : "mudou"}: vale quando o Vision reiniciar.`}
          </span>
          {!reiniciando && (
            <button type="button" className="botao botao-sim" onClick={() => {
              setReiniciando(true);
              acoes.reiniciar();
            }}>
              Reiniciar agora
            </button>
          )}
        </div>
      )}
      {dados.servicos.map((s) => (
        <Cartao key={s.id} servico={s} andamento={dados.andamento[s.id]} acoes={acoes} />
      ))}
    </div>
  );
}

function juntar(nomes: string[]): string {
  return nomes.length <= 1 ? (nomes[0] ?? "") : `${nomes.slice(0, -1).join(", ")} e ${nomes[nomes.length - 1]}`;
}

function Cartao({ servico: s, andamento, acoes }: { servico: Servico; andamento?: Andamento; acoes: AcoesConexoes }) {
  const [aberto, setAberto] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [arquivo, setArquivo] = useState<string | null>(null);
  const [confirmarSaida, setConfirmarSaida] = useState(false);
  const rodando = andamento?.rodando === true;

  useEffect(() => {
    // Terminou bem: fecha o formulário e esquece o que foi digitado (a senha não fica na tela).
    if (andamento && !andamento.rodando && andamento.ok) {
      setAberto(false);
      setForm({});
      setArquivo(null);
    }
  }, [andamento]);
  useEffect(() => {
    if (!confirmarSaida) return;
    const t = setTimeout(() => setConfirmarSaida(false), 4000);
    return () => clearTimeout(t);
  }, [confirmarSaida]);

  const valor = (c: CampoConexao) => form[c.nome] ?? (c.tipo === "senha" || c.tipo === "arquivo" ? "" : c.valor);
  const faltando = s.campos.some((c) => c.obrigatorio && !valor(c).trim());
  const enviar = () => {
    const dados: Record<string, string> = {};
    for (const c of s.campos) {
      const v = valor(c);
      if (v) dados[c.nome] = v;
    }
    acoes.conectar(s.id, dados);
  };
  const id = (c: CampoConexao) => `conexao-${s.id}-${c.nome}`;

  return (
    <section className={`conexao conexao-${s.situacao}`} aria-label={s.nome}>
      <div className="conexao-cabeca">
        <span className="conexao-nome">{s.nome}</span>
        <span className={`conexao-situacao situacao-${s.situacao}`}>{ROTULO_SITUACAO[s.situacao]}</span>
        <label className="interruptor" title={s.ligado ? "Desligar" : "Ligar"}>
          <input type="checkbox" checked={s.ligado} disabled={rodando}
            onChange={(e) => acoes.ligar(s.id, e.target.checked)} aria-label={`${s.nome} ligado`} />
          <span />
        </label>
      </div>
      <p className="conexao-descricao">{s.descricao}</p>
      {s.ligado && <p className="conexao-detalhe">{s.detalhe}</p>}
      {andamento && <Linha andamento={andamento} abrirLink={acoes.abrirLink} />}

      {s.ligado && aberto && !rodando && s.campos.length > 0 && (
        <form className="conexao-form" onSubmit={(e) => {
          e.preventDefault();
          if (!faltando) enviar();
        }}>
          {s.campos.map((c) => (
            <div className="ajuste" key={c.nome}>
              <label htmlFor={id(c)}>{c.rotulo}</label>
              {c.tipo === "arquivo" ? (
                <div className="ajuste-linha">
                  <input id={id(c)} type="file" accept=".json,application/json" onChange={async (e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    setArquivo(f.name);
                    setForm((v) => ({ ...v, [c.nome]: "" }));
                    if (f.size > 64_000) return; // a credencial do Google tem ~400 bytes
                    const texto = await f.text();
                    setForm((v) => ({ ...v, [c.nome]: texto }));
                  }} />
                  {arquivo && <span className="ajuste-nota">{arquivo}</span>}
                </div>
              ) : (
                <input id={id(c)} type={c.tipo === "senha" ? "password" : c.tipo === "email" ? "email" : "text"}
                  autoComplete="off" spellCheck={false} value={valor(c)} maxLength={200}
                  onChange={(e) => setForm((v) => ({ ...v, [c.nome]: e.target.value }))} />
              )}
              {c.dica && <span className="conexao-dica">{c.dica}</span>}
            </div>
          ))}
          <div className="ajustes-botoes">
            <button type="button" className="botao botao-nao" onClick={() => setAberto(false)}>Voltar</button>
            <button type="submit" className="botao botao-sim" disabled={faltando}>{s.acao ?? "Conectar"}</button>
          </div>
        </form>
      )}

      {s.ligado && !(aberto && s.campos.length > 0) && (
        <div className="conexao-botoes">
          {rodando ? (
            <button type="button" className="botao botao-nao" onClick={() => acoes.cancelar(s.id)}>Cancelar</button>
          ) : (
            <>
              {s.acao && (
                <button type="button" className="botao botao-sim"
                  onClick={() => (s.campos.length > 0 ? setAberto(true) : enviar())}>
                  {s.acao}
                </button>
              )}
              {s.desconectar && (
                <button type="button" className="botao botao-nao" onClick={() => {
                  if (confirmarSaida) {
                    setConfirmarSaida(false);
                    acoes.desconectar(s.id);
                  } else setConfirmarSaida(true);
                }}>
                  {confirmarSaida ? "Confirmar?" : "Desconectar"}
                </button>
              )}
            </>
          )}
          {s.ajuda && (
            <button type="button" className="conexao-ajuda" onClick={() => acoes.abrirLink(s.ajuda!.url)}>
              {s.ajuda.rotulo} ↗
            </button>
          )}
        </div>
      )}
    </section>
  );
}

const URL = /(https?:\/\/\S+)/;

/** A frase do login. Um endereço nela ("se não abrir, cole este endereço") vira botão, não texto enorme. */
function Linha({ andamento, abrirLink }: { andamento: Andamento; abrirLink: (url: string) => void }) {
  const [antes, url] = andamento.texto.split(URL);
  const classe = andamento.rodando ? "rodando" : andamento.ok ? "ok" : "erro";
  return (
    <p className={`conexao-andamento ${classe}`} role="status">
      {andamento.rodando && <span className="girando" aria-hidden="true" />}
      <span>{url ? antes.replace(/[:\s]+$/, "") : andamento.texto}</span>
      {url && (
        <button type="button" className="conexao-ajuda" onClick={() => abrirLink(url)}>abrir no navegador ↗</button>
      )}
    </p>
  );
}
