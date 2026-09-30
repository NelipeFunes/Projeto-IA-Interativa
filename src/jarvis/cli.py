"""Linha de comando: uv run jarvis <comando>."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from jarvis import config


def _silenciar_logs() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for nome in ("httpx", "httpcore", "mcp", "openwakeword"):
        logging.getLogger(nome).setLevel(logging.WARNING)


async def _chat(cfg: config.Config) -> None:
    from jarvis.google_login import aviso_login
    from jarvis.montagem import montar

    nome = cfg.get("assistente.nome", "Vision")
    async with montar(cfg) as j:
        print(f"{nome} ({j.agente.llm.modelo}) — digite 'sair' para encerrar.")
        for servidor, st in j.host.status().items():
            if st != "ok":
                print(f"  [aviso] MCP {servidor}: {st[:160]}")
        if aviso := aviso_login(cfg):
            print(f"{nome}: {aviso}")
        while True:
            try:
                texto = (await asyncio.to_thread(input, "\nVocê: ")).lstrip("﻿")  # BOM vindo de pipe no PowerShell
            except (EOFError, KeyboardInterrupt):
                break
            if texto.strip().lower() in {"sair", "exit", "tchau"}:
                break
            if not texto.strip():
                continue
            print(f"{nome}: ", end="", flush=True)
            r = await j.agente.responder(texto, "texto", "terminal", ao_texto=lambda t: print(t, end="", flush=True))
            usadas = ", ".join(
                f"{f['nome']}{' (cancelada)' if f.get('cancelada') else '' if f['ok'] else ' (falhou)'}"
                for f in r.ferramentas
            )
            print(f"\n  [{r.segundos:.1f}s{' · ' + usadas if usadas else ''}]")


async def _dormir(cfg: config.Config, dormir: bool) -> None:
    import ollama

    flag = cfg.dados / "dormindo.flag"
    cliente = ollama.AsyncClient(host=cfg.get("modelo.host"))
    modelo = cfg.get("modelo.nome")
    if dormir:
        flag.write_text("1", encoding="utf-8")
        await cliente.generate(model=modelo, prompt="", keep_alive=0)
        print(f"{modelo} descarregado da VRAM. Palavra de ativação pausada (o atalho continua valendo).")
    else:
        flag.unlink(missing_ok=True)
        await cliente.generate(model=modelo, prompt="", keep_alive=cfg.get("modelo.manter_carregado", "30m"))
        print(f"{modelo} carregado e pronto.")


async def _memorias(cfg: config.Config) -> None:
    from jarvis.montagem import criar_memorias

    m = criar_memorias(cfg)
    todas = m.todas()
    print(f"{len(todas)} memória(s) em {m.arquivo}")
    for mem in todas:
        print(f"  [{mem.id}] ({mem.categoria}, {mem.criado_em[:10]}) {mem.texto}")
    m.fechar()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    ap = argparse.ArgumentParser(prog="jarvis", description="Assistente pessoal local do Felipe")
    ap.add_argument("--modelo", help="sobrescreve modelo.nome (ex.: qwen3.5:9b)")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("chat", help="conversa por texto no terminal")
    v = sub.add_parser("voz", help="modo voz: 'Hey Jarvis' ou o atalho")
    v.add_argument("--sem-ativacao", action="store_true", help="só o atalho, sem palavra de ativação")
    sub.add_parser("servidor", help="cérebro como serviço HTTP em 127.0.0.1")
    i = sub.add_parser("interface", help="abre a janela gráfica")
    i.add_argument("--demo", action="store_true", help="roda o roteiro de demonstração (Fase A)")
    t = sub.add_parser("teste", help="checagem geral do ambiente")
    t.add_argument("parte", nargs="?", default="tudo", choices=["tudo", "ollama", "agenda", "orbit", "voz"])
    sub.add_parser("google-login", help="refaz o login do Google Agenda (a cada 7 dias)")
    sub.add_parser("dormir", help="tira o modelo da VRAM e pausa a palavra de ativação")
    sub.add_parser("acordar", help="carrega o modelo e reativa a palavra de ativação")
    sub.add_parser("memorias", help="lista o que o assistente lembra")
    f = sub.add_parser("falar", help="fala um texto com a voz configurada (teste de voz)")
    f.add_argument("texto", nargs="+")
    args = ap.parse_args(argv)

    _silenciar_logs()
    sobrescrever = {"modelo.nome": args.modelo} if args.modelo else None
    cfg = config.carregar(sobrescrever=sobrescrever)

    if args.comando == "chat":
        asyncio.run(_chat(cfg))
    elif args.comando == "voz":
        from jarvis.voice.loop import rodar_voz

        asyncio.run(rodar_voz(cfg, com_ativacao=not args.sem_ativacao))
    elif args.comando == "servidor":
        from jarvis.server import rodar

        rodar(cfg)
    elif args.comando == "interface":
        if not args.demo:
            print("Por enquanto só existe a demonstração (a interface ligada ao cérebro vem na Fase C): jarvis interface --demo")
            return 1
        from jarvis.interface import abrir

        abrir(cfg, demo=True)
    elif args.comando == "teste":
        from jarvis.diagnostico import diagnosticar

        return asyncio.run(diagnosticar(cfg, args.parte))
    elif args.comando == "google-login":
        from jarvis.google_login import fazer_login

        return fazer_login(cfg)
    elif args.comando in ("dormir", "acordar"):
        asyncio.run(_dormir(cfg, args.comando == "dormir"))
    elif args.comando == "memorias":
        asyncio.run(_memorias(cfg))
    elif args.comando == "falar":
        from jarvis.voice.tts import falar_texto

        falar_texto(cfg, " ".join(args.texto))
    return 0


if __name__ == "__main__":
    sys.exit(main())
