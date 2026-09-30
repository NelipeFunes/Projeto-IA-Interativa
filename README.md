# Jarvis

Assistente pessoal local do Felipe: agenda (Google), finanças e tarefas (Orbit) e memória, por texto ou voz,
100% no PC, sem pagar tokens. Cérebro: Qwen3.5 no Ollama (GPU). Ouvido: Parakeet (CPU). Voz: Piper (CPU).

## Comandos

```bash
.\jarvis chat              # conversa por texto
.\jarvis voz               # "Hey Jarvis" ou ctrl+alt+j
.\jarvis teste             # checagem geral (ollama, agenda, orbit, voz)
.\jarvis teste voz         # calibra o "Hey Jarvis" e compara os STT com a sua voz
.\jarvis google-login      # a cada 7 dias (app do Google em modo teste)
.\jarvis memorias          # o que o Jarvis lembra de você
.\jarvis dormir            # tira o modelo da VRAM (antes de jogar) / pausa o "Hey Jarvis"
.\jarvis acordar
.\jarvis falar "Oi, Felipe"   # testa a voz
.\jarvis servidor          # cérebro como API em 127.0.0.1:8765 (para a Alexa, depois)
.\jarvis --modelo qwen3.5:9b chat   # troca de modelo sem mexer no config
.venv\Scripts\python -m pytest                   # testes (74, sem precisar de login nem microfone)
.venv\Scripts\python evals\run.py      # avaliação 4B × 9B com agenda e Orbit falsos
```

## Como funciona

```
microfone → openWakeWord ("hey jarvis") → Silero VAD (fim da fala) → Parakeet (texto)
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
