# Vision

Assistente pessoal local do Felipe (o pacote e o comando ainda se chamam `jarvis`): agenda (Google), finanças e tarefas (Orbit) e memória, por texto ou voz,
100% no PC, sem pagar tokens. Cérebro: Qwen3.5 no Ollama (GPU). Ouvido: Parakeet (CPU). Voz: Piper (CPU).

## Instalação (Windows)

Precisa de: GPU NVIDIA com 6+ GB de VRAM, [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node.js e git.

```bash
git clone https://github.com/NelipeFunes/Projeto-IA-Interativa.git jarvis
cd jarvis
uv sync --python 3.12
ollama pull qwen3.5:4b
ollama pull embeddinggemma
.venv\Scripts\python scripts\baixar_modelos.py
npm install --prefix node
npm install --prefix ui && npm run build --prefix ui
```

Depois, ponha a pasta do projeto no PATH para o comando `jarvis` (o `jarvis.cmd`) funcionar de qualquer pasta.
Para conectar o Google Agenda, veja `docs/guia-google-cloud.md`.

## No dia a dia: em segundo plano

O **núcleo** (`jarvisw`, sem console) junta tudo: voz, a janela com o orbe, o ícone na bandeja e o servidor local.
Na primeira vez que roda, ele se coloca na pasta Inicializar do Windows (desliga pela bandeja).

```bash
jarvis abrir             # abre a janela (liga o núcleo se ele não estiver rodando)
jarvis nucleo            # o mesmo núcleo, com log no console (para ver o que acontece)
```

- **Bandeja:** abrir, falar agora, pausar escuta, iniciar com o Windows, sair. O ícone muda de cor com o estado.
- **Conversa:** "Hey Vision" acorda ("Oi, Felipe. Pode falar."); daí tudo o que você fala vai para ele, sem repetir
  o nome, até "Beleza, Vision, pode desligar" ou 2 minutos de silêncio. Fora da conversa, o que você fala perto do PC
  é transcrito só para achar o nome e descartado (não vai para a tela, o log nem o disco).
- **Atalhos:** `ctrl+alt+j` fala com ele; `ctrl+alt+k` abre a janela.
- **Janela fechada:** falando com ele, aparece uma bolha no canto, sem tirar o foco (não aparece no modo jogo).
- **Log:** `data/logs/nucleo.log` e `data/logs/janela.log`.

## Comandos (de qualquer pasta)

```bash
jarvis chat              # conversa por texto
jarvis voz               # "Hey Vision" (ou ctrl+alt+j) abre a conversa; "Beleza, Vision, pode desligar" fecha
jarvis teste             # checagem geral (ollama, agenda, orbit, voz)
jarvis teste voz         # testa o "Hey Vision" e o "pode desligar" com a sua voz
jarvis google-login      # a cada 7 dias (app do Google em modo teste)
jarvis memorias          # o que o Jarvis lembra de você
jarvis dormir            # tira o modelo da VRAM (antes de jogar) / pausa o "Hey Vision"
jarvis acordar
jarvis falar "Oi, Felipe"   # testa a voz
jarvis servidor          # só o cérebro como API em 127.0.0.1:8765 (token em data/nucleo.json)
jarvis interface --demo  # a demonstração da tela, com dados de exemplo
jarvis --modelo qwen3.5:9b chat   # troca de modelo sem mexer no config
```

Na pasta do projeto:

```bash
.venv\Scripts\python -m pytest      # testes do Python, sem precisar de login nem microfone
npm test --prefix ui                 # testes da tela
git config core.hooksPath .githooks  # uma vez: barra push com termo de data/termos-privados.txt
.venv\Scripts\python evals\run.py   # avaliação 4B × 9B com agenda e Orbit falsos
```

## Como funciona

```
microfone → Silero VAD (fala) → Parakeet (texto) → "Hey Vision"? abre a conversa → …
         → Agente (Qwen3.5 + ferramentas) → Piper (voz, frase a frase) → fone
                  │
                  ├── agenda_*   → MCP @cocal/google-calendar-mcp (node/)
                  ├── financas_*, tarefas_* → MCP do Orbit (mcp_servers/orbit)
                  └── guardar_memoria / buscar_memoria / esquecer → data/memoria.db
```

- **Ferramentas simples em português** (src/jarvis/tools): o modelo pequeno não vê os esquemas enormes dos
  MCPs; cada ferramenta traduz para o MCP.
- **Escrita sempre confirmada:** criar, alterar, apagar, lançar ou esquecer só roda depois de um "sim".
  A frase de confirmação é montada pelo código, não pelo modelo.
- **Rede de segurança:** se a frase tem cara de agenda/finanças e o modelo responde sem consultar nada,
  o agente insiste uma vez.
- **Memória:** `data/perfil.md` entra em toda conversa (edite à mão); `data/memoria.db` guarda fatos, e os
  3 mais parecidos com cada fala entram no prompt.
- **Modo jogo:** com o `cs2.exe` aberto, o modelo sai da VRAM e o "Hey Jarvis" pausa (o atalho continua).

## Arquivos que você edita

- `config.yaml`: modelo, voz, microfones, atalho, limiares.
- `.env` (copie de `.env.example`): credenciais do Orbit.
- `data/perfil.md`: quem você é, em poucas linhas.
- `data/google-oauth.json`: credencial do Google (veja `docs/guia-google-cloud.md`).
