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
  Se ele não entende o seu "Hey Vision", use **⚙ → Geral → Calibrar**: a cada bipe, diga "Hey Vision" (5 vezes).
  Ele mostra como o STT escreveu o seu "Vision" (só fala curta que se repete na maioria das vezes e não é palavra
  comum) e pergunta se pode usar; aceitando, guarda em `data/ativacao.json` (até 5; o × tira uma). O que você fala
  na calibração aparece só na tela; no log, só se acordou ou não.
  Para entender por que ele não acorda, `voz.registrar_ativacao: true` no `config.yaml` escreve no log o que ele
  ouviu no começo de cada fala (desligue depois: com isso ligado, o que se fala perto do PC vai para o log).
- **"Hey Vision" pelo modelo** (`voz.ativacao: ambos`, padrão): além de achar o nome no texto, um modelo
  openWakeWord reconhece o **som** do "Hey Vision", sem passar pelo STT, e o que vem depois do nome é o pedido.
  O modelo não vem pronto (é seu): `scripts/ativacao/preparar_treino.py` gera amostras com sotaque brasileiro
  (vozes Piper pt-BR) e o notebook do Colab (`data/ativacao/`); no Colab (GPU T4, ~2h30) ele treina e baixa
  `hey_vision.onnx`, que vai em `modelos/openwakeword/`. Depois, `scripts/ativacao/gravar_minha_voz.py` grava
  20 "Hey Vision" seus e 4 frases normais e treina o **verificador da sua voz** (um 2º filtro, local, que corrige o
  sotaque e corta a TV) e mostra as notas para escolher `voz.limiar_ativacao` (que também está na tela de Ajustes, como "Sensibilidade da ativação", de 0,15 a 0,95; vale ao reiniciar). As gravações ficam em
  `data/ativacao/minha_voz/` e não saem do PC (o Colab só recebe as vozes sintéticas). Sem o modelo, fica só a
  transcrição, como antes.
- **Microfone:** usa o primeiro da lista `voz.microfone` que tiver som. Se ele ficar mudo ou desligado por
  1 minuto (headset sem fio), passa para o próximo; quando o primeiro volta, volta para ele. Fica no log.
- **Atalhos:** `ctrl+alt+j` fala com ele; `ctrl+alt+k` abre a janela.
- **Janela fechada:** falando com ele, aparece uma bolha no canto, sem tirar o foco (não aparece no modo jogo).
- **Conexões (⚙ → Conexões):** Google Agenda, Spotify, Alexa, Wispr Flow, a chave da Tavily, o Orbit e a TV, sem
  terminal. Cada um tem um interruptor; os logins abrem a página do próprio serviço no navegador (o Vision não
  vê a senha dessas contas). Chave e senha digitadas ali vão para o `.env` e nunca voltam para a tela. Como as
  conexões são montadas ao iniciar, a tela oferece "Reiniciar agora" quando algo muda. Os comandos
  `vision google-login`, `spotify-login` etc. continuam valendo.
- **Volume:** com o Spotify tocando, "aumenta/abaixa o volume" mexe no volume do próprio Spotify; com ele no
  máximo, sobe o do Windows (e tira do mudo). Sem Spotify tocando, ou dizendo "volume do PC", é o Windows.
- **Lista de fazeres:** as tarefas do Orbit. "O que tem na minha lista?", "adiciona trocar o óleo para sexta",
  "já paguei o IPVA" (marca como feita), "muda a prioridade de X"; apagar pede "sim". O login é pela tela de
  Conexões (se o Orbit mandar um código por e-mail, a tela pede o código).
- **TV Samsung (2015, série J):** "desliga a TV", "volume da TV no 10", "canal 13", "abre o Spotify na TV",
  "toca blank space no YouTube da TV" (busca no DuckDuckGo e toca o clipe mais visto), "desliga a TV em
  30 minutos" (timer que desliga, sem alarme). Pareie uma vez com a TV ligada: ⚙ → Conexões → TV Samsung (IP da
  TV e o PIN que aparece nela) ou `vision tv-parear`. O IP fica em `data/config-local.yaml` e o pareamento em
  `data/tv/`, fora do git. Volume e YouTube vão pelo UPnP e pelo DIAL (sem pareamento); as teclas, pelo protocolo
  criptografado dessas TVs. Ligar não dá: com a TV desligada o Wi-Fi dela também desliga. Um link de mídia
  ("toca este link na TV") sempre pede "sim".
- **Piscina Tholz (em construção, só leitura, T-017 etapa 1):** o app da Tholz fala com uma API na AWS autenticada
  por login Cognito; `vision piscina-login` entra com a **sua** conta (e-mail e senha digitados no terminal, a senha
  não é guardada; só a sessão, em `data/piscina/sessao.json`, **em texto, só neste PC**: quem lê o arquivo entra na
  conta até o token vencer ou ser revogado) e `vision piscina-ler` lê a lista de dispositivos, guarda o JSON cru em
  `data/piscina/ultima-leitura.json` (pode ter identificadores: não cole o arquivo) e mostra só o formato. Os identificadores do backend (pool e client do login e
  endereço da API) ficam em `data/piscina/app.json`, fora do git (o repositório é público): o cliente só fala com
  uma API `*.execute-api.<região>.amazonaws.com` e com o Cognito da mesma região (isso garante "uma API da AWS", não
  "a da Tholz": confira o `app.json`). Ainda não há ferramenta para o
  modelo nem comando para a piscina. Os termos de uso da Tholz proíbem engenharia reversa: uso pessoal, por sua
  conta e risco (a Tholz pode bloquear a conta ou mudar a API).
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
- **Pacote e projeto não rodam juntos:** o núcleo é único por usuário do Windows. Com o núcleo do projeto ligado,
  abrir o `Vision.exe` só pede a janela do núcleo do projeto (com os dados e o config dele).
- **Limites conhecidos:** o servidor MCP do Orbit (finanças e tarefas por voz) chama o Python do ambiente de
  desenvolvimento (`.venv`) e não sobe no pacote; as outras conexões funcionam. O verificador da sua voz
  (`*_verificador.pkl`) nunca é copiado para o pacote. Um pacote refeito não atualiza modelos já copiados.

## Comandos (de qualquer pasta)

```bash
vision chat              # conversa por texto
vision voz               # "Hey Vision" (ou ctrl+alt+j) abre a conversa; "Vision, standby" fecha
vision teste             # checagem geral (ollama, agenda, orbit, voz)
vision teste voz         # testa o "Hey Vision" e o "standby" com a sua voz
vision google-login      # a cada 7 dias (app do Google em modo teste)
vision tv-parear         # pareia com a TV Samsung (PIN na tela dela)
vision piscina-login     # entra na sua conta da Tholz (piscina; só leitura por enquanto)
vision piscina-ler       # lê os dispositivos da piscina e mostra só o formato da resposta
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
microfone → openWakeWord ("Hey Vision" pelo som) ┐
          → Silero VAD (fala) → Parakeet (texto) → "Hey Vision"? ┴ abre a conversa → …
         → Agente (Qwen3.5 + ferramentas) → Piper (voz, frase a frase) → fone
                  │
                  ├── agenda_*   → MCP @cocal/google-calendar-mcp (node/)
                  ├── financas_*, tarefas_* → MCP do Orbit (mcp_servers/orbit)
                  ├── web_buscar / web_ler → Tavily (ou DuckDuckGo, sem chave)
                  ├── tv_*        → TV Samsung na rede de casa (teclas, UPnP, DIAL)
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
- **Cache:** um módulo só (`vision/cache.py`) para a agenda (ontem até +7 dias, em memória, refeita a cada 5 min),
  as buscas e páginas lidas da web (6 h) e as manchetes (30 min), em `data/cache/web.json` (pode apagar a pasta quando
  quiser), a previsão do clima (15 min) e a lista de programas do PC (10 min), em memória. Arquivo estragado ou de
  outra versão vale cache vazio. Tokens e logins nunca ficam aqui, e a lista de luzes da Alexa, as coordenadas da
  cidade do clima e o contador da Tavily (também em `data/cache/`) não são desse cache: apagar a pasta zera o
  contador do mês.
- **Medição:** cada ferramenta deixa em `data/medicoes/` o nome, quanto demorou, se terminou sem erro e uma
  impressão curta dos argumentos (para saber se a mesma pergunta voltou). O conteúdo não é gravado em texto, mas a
  impressão pode ser adivinhada para argumento simples; por isso fica em `data/`, fora do git, e ferramentas de
  escrita não gravam impressão. `vision medicao` mostra o resumo e quanto um cache poderia ter poupado.
  Desligue com `medicao.ativo: false`.
- **Modo jogo:** com o `cs2.exe` aberto, o modelo sai da VRAM e o "Hey Vision" pausa (o atalho continua).

## Arquivos que você edita

- `config.yaml`: modelo, voz, microfones, atalho, limiares.
- `.env` (copie de `.env.example`): credenciais do Orbit e `TAVILY_API_KEY` (crie a conta em tavily.com e
  copie a chave; sem ela, a busca usa o DuckDuckGo).
- `data/perfil.md`: quem você é, em poucas linhas.
- `data/google-oauth.json`: credencial do Google (veja `docs/guia-google-cloud.md`).
