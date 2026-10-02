# Vision

Assistente pessoal local do Felipe (comando `vision`): agenda (Google), finanças e tarefas (Orbit) e memória, por texto ou voz,
100% no PC, sem pagar tokens. Cérebro: Qwen3.5 no Ollama (GPU). Ouvido: Parakeet (CPU). Voz: Piper (CPU).

## Instalação (Windows)

Precisa de: GPU NVIDIA com 6+ GB de VRAM, [Ollama](https://ollama.com), [uv](https://docs.astral.sh/uv/), Node.js e git.

```bash
git clone https://github.com/NelipeFunes/Projeto-IA-Interativa.git vision
cd vision
uv sync --python 3.12
ollama pull qwen3.5:4b
ollama pull embeddinggemma
.venv\Scripts\python scripts\baixar_modelos.py
npm install --prefix node
npm install --prefix ui && npm run build --prefix ui
```

Depois, ponha a pasta do projeto no PATH para o comando `vision` (o `vision.cmd`) funcionar de qualquer pasta.
Para conectar o Google Agenda, veja `docs/guia-google-cloud.md`.

## No dia a dia: em segundo plano

O **núcleo** (`visionw`, sem console) junta tudo: voz, a janela com o orbe, o ícone na bandeja e o servidor local.
Na primeira vez que roda, ele se coloca na pasta Inicializar do Windows (desliga pela bandeja).

```bash
vision abrir             # abre a janela (liga o núcleo se ele não estiver rodando)
vision nucleo            # o mesmo núcleo, com log no console (para ver o que acontece)
```

- **Bandeja:** abrir, falar agora, pausar escuta, iniciar com o Windows, sair. O ícone muda de cor com o estado.
- **Conversa:** "Hey Vision" acorda (um bipe, e ele já ouve); daí tudo o que você fala vai para ele, sem repetir
  o nome, até "Vision, standby" ou 2 minutos de silêncio. Fora da conversa, o que você fala perto do PC
  é transcrito só para achar o nome e descartado (não vai para a tela, o log nem o disco).
- **Atalhos:** `ctrl+alt+j` fala com ele; `ctrl+alt+k` abre a janela.
- **Janela fechada:** falando com ele, aparece uma bolha no canto, sem tirar o foco (não aparece no modo jogo).
- **Log:** `data/logs/nucleo.log` e `data/logs/janela.log`.

### Vision.exe (duplo clique)

```bash
python scripts/gerar_exe.py    # gera Vision.exe na pasta do projeto (usa o csc.exe que vem no Windows)
```

O `Vision.exe` é um lançador de ~170 KB com o ícone do orbe: liga o Vision (ou só abre a janela, se ele já
estiver rodando). Dá para fixar na barra de tarefas ou criar um atalho na área de trabalho. Ele precisa ficar
dentro da pasta do projeto (a que tem o `.venv`), ou usar a variável `VISION_HOME`. Se o Vision não iniciar,
um aviso aponta para `data\logs\nucleo.log`. Não vai para o git.

### Pacote independente (sem Python instalado)

Uma pasta `dist\Vision\` com o `Vision.exe` de verdade (PyInstaller): o programa tem uns 300 MB, contra 8 GB do
ambiente de desenvolvimento, porque não leva o torch do XTTS.

```bash
# 1 vez: um ambiente à parte, só com o necessário (+ PyInstaller)
$env:UV_PROJECT_ENVIRONMENT=".venv-pacote"; uv sync --no-default-groups --group empacotar
# sempre que quiser um pacote novo
.venv-pacote\Scripts\python scripts\empacotar.py --modelos copiar   # ou: ligar (atalhos de pasta) | nenhum
```

- **Ao lado do `.exe` ficam:** `config.yaml`, `ui\dist`, `modelos\` (só a voz Piper, o Parakeet e a palavra de
  ativação), `node\` (Google Agenda) e a pasta `data\`, que começa **vazia**: cada instalação refaz os seus
  logins (`Vision.exe google-login`, `spotify-login`...) e cria a sua memória. Nunca entram `.env`, `data\` do projeto
  nem os modelos do XTTS.
- **Usos:** `Vision.exe` abre a janela; `Vision.exe --nucleo` é o núcleo em segundo plano (é o que a pasta
  Inicializar executa); `Vision.exe <comando>` roda qualquer comando do `vision` (`chat`, `teste`, `wyoming`...).
- **Ollama** continua sendo instalado à parte (é ele que roda o modelo de IA). O Node vai junto no pacote
  (`--modelos copiar` leva o `node.exe` e aponta o `config.yaml` para ele).
- **Onde pôr a pasta:** num lugar só seu (`%LOCALAPPDATA%` ou `Program Files`). O `config.yaml` ao lado do `.exe`
  diz o que o Vision executa, então uma pasta que outros usuários podem alterar (`C:\Users\Public`, `C:\Temp`)
  não é um bom lugar. O `.exe` não é assinado: o Windows pode avisar na primeira vez.
- O pacote não toma o lugar de um atalho de inicialização que já exista no Windows.

## Comandos (de qualquer pasta)

```bash
vision chat              # conversa por texto
vision voz               # "Hey Vision" (ou ctrl+alt+j) abre a conversa; "Vision, standby" fecha
vision teste             # checagem geral (ollama, agenda, orbit, voz)
vision teste voz         # testa o "Hey Vision" e o "standby" com a sua voz
vision google-login      # a cada 7 dias (app do Google em modo teste)
vision memorias          # o que o Vision lembra de você
vision dormir            # tira o modelo da VRAM (antes de jogar) / pausa o "Hey Vision"
vision acordar
vision falar "Oi, Felipe"   # testa a voz
vision servidor          # só o cérebro como API em 127.0.0.1:8765 (token em data/nucleo.json)
vision interface --demo  # a demonstração da tela, com dados de exemplo
vision --modelo qwen3.5:9b chat   # troca de modelo sem mexer no config
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
                  ├── web_buscar / web_ler → Tavily (ou DuckDuckGo, sem chave)
                  └── guardar_memoria / buscar_memoria / esquecer → data/memoria.db
```

- **Ferramentas simples em português** (src/vision/tools): o modelo pequeno não vê os esquemas enormes dos
  MCPs; cada ferramenta traduz para o MCP.
- **Confirmação só no que é sensível** (`assistente.confirmacao: sensiveis`): apagar evento, esquecer memória e
  lançar gasto pedem um "sim"; o resto é feito na hora e a resposta diz o que mudou. `todas` pede para toda escrita.
  A frase de confirmação é montada pelo código, não pelo modelo.
- **Rede de segurança:** se a frase tem cara de agenda/finanças e o modelo responde sem consultar nada,
  o agente insiste uma vez.
- **Memória:** `data/perfil.md` entra em toda conversa (edite à mão); `data/memoria.db` guarda fatos, e os
  3 mais parecidos com cada fala entram no prompt. Cada fato pode ter um `vale_para` (os assuntos em que ele
  importa): "tem um carro", com `vale_para: moradia, vaga de garagem`, aparece quando você pede casas para alugar.
- **Internet:** `web_buscar` e `web_ler`. A busca usa a memória para pôr seus critérios na consulta. Os links
  aparecem embaixo da resposta, na tela. Texto de página conta como conteúdo de fora: a próxima ação sensível
  pede "sim", e `web_ler` não abre endereço da rede local.
- **Cache:** a agenda (ontem até +7 dias) fica em memória e é refeita a cada 5 min; buscas na web ficam 6 h em
  `data/cache/` (pode apagar a pasta quando quiser).
- **Modo jogo:** com o `cs2.exe` aberto, o modelo sai da VRAM e o "Hey Vision" pausa (o atalho continua).

## Arquivos que você edita

- `config.yaml`: modelo, voz, microfones, atalho, limiares.
- `.env` (copie de `.env.example`): credenciais do Orbit e `TAVILY_API_KEY` (crie a conta em tavily.com e
  copie a chave; sem ela, a busca usa o DuckDuckGo).
- `data/perfil.md`: quem você é, em poucas linhas.
- `data/google-oauth.json`: credencial do Google (veja `docs/guia-google-cloud.md`).
