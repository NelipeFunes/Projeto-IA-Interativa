# Estudo (hipotético): separar o Vision em servidores e o que poderia ir para a nuvem

Só estudo. Nada aqui foi implementado nem deve ser sem decisão do dono. Contexto: hoje tudo roda em um PC (i7-8700, RTX 5060 Ti de 8 GB, 32 GB de RAM, Windows 11) e existe a ideia de um PC dedicado só à IA no futuro. Tudo continua **local** por escolha dele; a seção da nuvem só sinaliza o que *poderia* sair daqui.

## 1. Mapa do que existe hoje

| Peça | Onde roda | Recurso | Já fala por rede? | Acoplamento que prende ao PC |
|---|---|---|---|---|
| Modelo de linguagem (Qwen3.5 via Ollama) | processo do Ollama | VRAM (3,4 GB o 4B, com `modelo.contexto` 16.384) | **Sim**: `modelo.host` | nenhum; `127.0.0.1` só é o padrão |
| Embeddings da memória (embeddinggemma) | o mesmo Ollama | CPU | **Sim**: usa `modelo.host` | nenhum |
| Transcrição (Parakeet, ONNX) | processo do Vision | CPU, 6 threads | Só como *servidor* Wyoming (`vision wyoming`) | `voice/stt.py` carrega o modelo dentro do processo; não há cliente remoto |
| Voz (Piper na CPU, XTTS na GPU) | processo do Vision | CPU ou ~2 GB de VRAM | Só como servidor Wyoming | `voice/tts.py` idem |
| Palavra de ativação e detector de fala | processo do Vision | CPU | Não | precisam do áudio do microfone, que é local |
| Cérebro (agente, prompt, ferramentas) | processo do núcleo | CPU | API HTTP em `127.0.0.1` com token | `servidor.host` só aceita loopback (travado em `server.py`) |
| Ferramentas de PC (travar, volume, programas, mídia, energia) | processo do núcleo | — | Não | **Windows de verdade**: `ctypes.windll`, PowerShell, `os.startfile`, teclas de mídia |
| Agenda (Google Calendar), Orbit, Wispr | servidores MCP filhos (stdio) | — | Orbit e Google já são nuvem | os filhos são processos locais do núcleo |
| TV, luzes, música | rede local e nuvens (TV: LAN; Alexa e Spotify: nuvem) | — | Sim | a TV só responde dentro da casa |
| Interface (tela do núcleo) | janela + servidor estático em loopback | — | Não | `interface.py` aceita só `127.0.0.1` |

## 2. Fronteiras naturais (onde cortar custa pouco)

1. **LLM e embeddings → outro PC (custo: uma linha de config).** `modelo.host` já aponta para qualquer Ollama. É o corte mais barato e o que mais ganha: a placa do servidor de IA (T-012) carrega o modelo e o PC de uso diário não precisa de VRAM para ele. Cuidados:
   - o Ollama **não tem senha**: expor só na rede interna ou por túnel (WireGuard/Tailscale), com firewall liberando só o PC do Vision;
   - a latência da rede entra em toda chamada (ordem de grandeza: cabo ~1 ms; Wi-Fi alguns ms a dezenas, com variação; não medido aqui). O prompt de ~8.000 tokens fica no servidor (o cache de prefixo vale lá), então só o texto viaja;
   - o *pré-aquecimento* e o `keep_alive` passam a ser decisão do servidor, não mais do modo jogo.
2. **STT e TTS → servidor Wyoming (custo: um cliente novo, pequeno).** O `vision wyoming` já serve transcrição e voz para o Home Assistant. Falta o caminho inverso: `Transcritor` e `Voz` são interfaces estreitas (`transcrever(audio)->texto`, `sintetizar(texto)->áudio`), então um `TranscritorWyoming` e uma `VozWyoming` entram no mesmo lugar de `carregar_transcritor`/`carregar_voz`. Cuidados:
   - o Wyoming **não tem senha nem criptografia** (a própria documentação do módulo diz isso): só em rede de confiança ou dentro de túnel;
   - voz em streaming dividida em frases (o `falado.py` já faz) esconde parte da latência de rede; o tempo da transcrição remota não foi medido (medir antes de decidir);
   - o Piper ao lado do XTTS na mesma máquina mantém o fallback.
3. **Cérebro (agente e prompt) → servidor, com executor no PC (custo: alto; só se valer).** As ferramentas de PC têm de rodar no Windows do dono. Para separar, o servidor decidiria a ação e um *executor* pequeno no PC a cumpriria. Isso exige um protocolo novo (ação assinada, lista fechada de ações, confirmação preservada) e não deve ser improvisado: hoje a segurança depende de o modelo e as ferramentas estarem no mesmo processo (a confirmação, o `leu_de_fora`, a lista de ferramentas com `sempre_confirmar`). Recomendação: **não separar** enquanto não houver um motivo concreto.
4. **Microfone, alto-falante, palavra de ativação → ficam onde o dono está.** Se um dia houver satélites nos cômodos, o caminho é o Wyoming/ESPHome já documentado em `docs/futuro/`.

## 3. Pontos do código que quebrariam numa separação

- `server.py`: recusa qualquer `servidor.host` que não seja loopback, de propósito. Mudar exige autenticação mútua, não só trocar o endereço.
- `interface.py` e `nucleo.py` montam URLs `http://127.0.0.1:<porta>` na mão em vários pontos.
- `spotify.py` usa `socket.gethostname()` para escolher o "dispositivo deste PC": no servidor viraria o dispositivo errado.
- `montagem.py` conecta tudo no mesmo processo (Agente, Registro, MCP, timers); não há um ponto único de "serviços remotos".
- Os servidores MCP filhos (Google e Orbit) são processos do núcleo (`stdio`); em outra máquina precisam subir lá, com o `env:` declarado (a lista branca de variáveis já ajuda: nada vaza por herança).
- O modo jogo (descarregar o modelo quando o CS2 abre) hoje liga o Vision ao estado do PC; com o modelo em outro servidor, o modo jogo só precisa cuidar de STT/TTS locais.

## 4. O que *poderia* ir para a nuvem (sinalização, não proposta)

Nada disso é para fazer agora; o dono quer tudo local.

| Item | Ganho | Custo / risco | Veredito |
|---|---|---|---|
| Modelo grande só para pedidos difíceis (fallback) | qualidade em perguntas complexas | a fala do dono e o contexto saem de casa; preço por uso; latência | só opcional e desligado por padrão, nunca com memórias ou agenda no contexto |
| STT/TTS em nuvem | nenhum relevante (o Parakeet roda na CPU, sem VRAM) | áudio de dentro de casa sai da rede | não |
| Embeddings em nuvem | nenhum | as memórias pessoais saem | não |
| Treino do detector "Hey Vision" | já é feito no Colab | — | já é nuvem, pontual |
| Execução da bateria de avaliação | rodar modelos grandes sem ocupar a placa | custo por hora | útil só quando a máquina de IA dedicada não existir |
| Backup de `data/` (memórias, conversas, configuração) | sobrevive a falha de disco | dado pessoal: criptografar antes de subir | vale considerar, com chave do dono |
| Orbit, Tavily, Open-Meteo, Spotify, Alexa, piscina | — | — | **já** são nuvem |

## 5. Caminho em etapas, se um dia for fazer

1. Instalar o PC de IA, subir o Ollama lá (rede interna, firewall) e mudar só `modelo.host`. Medir com `vision medicao` e a bateria de `evals/` (a diferença deve ser a latência da rede).
2. Mover o XTTS/Piper para lá via Wyoming (cliente `VozWyoming`), mantendo o Piper local como reserva.
3. Só então avaliar STT remoto; a CPU do PC principal costuma bastar.
4. Reavaliar a separação do cérebro **apenas** se aparecer um motivo que as etapas 1 a 3 não resolvam.

Regra para qualquer etapa: serviço sem autenticação (Ollama, Wyoming) não escuta fora de `127.0.0.1` sem túnel ou rede isolada, e cada corte vem com um teste que mede o tempo antes e depois (ver `LICOES.md`).
