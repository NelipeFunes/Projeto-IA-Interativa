# Quadro de tarefas

Todas as tarefas do projeto num só lugar: **Pendentes**, **Em andamento** e **Concluídas**.

Como usar:
- Cada tarefa tem um código (`T-001`…) que não muda. Para mudar de coluna, mova o bloco inteiro.
- Marcas: `[ ]` pendente, `[~]` em andamento, `[x]` concluída. Prioridade: 🔴 alta, 🟡 média, 🟢 baixa.
- Concluída leva a data e o PR (quando houver). Mudança neste arquivo também passa por branch e PR.
- Este repositório é **público**: só tarefas técnicas aqui. Nada de dado pessoal, rotina ou credencial.

Próximo código livre: **T-019**

---

## Pendentes

- [ ] **T-001** 🟡 **Trocar tudo que é "Vision" para "balinha"** (pedido de 06/10) — **decidido, ainda não começou: só fazer quando o Felipe mandar.**
  - **Decisão (06/10):** é a troca total. O nome do assistente, a frase de ativação ("Hey Vision" vira "Hey balinha") e tudo que se chame Vision. Não é só o nome na janela.
  - **O que entra:**
    - Nome do assistente: `assistente.nome` (`config.yaml`, tela de Ajustes), prompt, janela, bandeja, saudação, textos fixos da UI e mensagens de erro.
    - Ativação por voz: `voz.palavra_ativacao`, as grafias e a calibração em `comandos.achar_ativacao`, e **um novo modelo openWakeWord** (T-006 e T-015). O `hey_vision.onnx` não serve para "Hey balinha".
    - Nome do projeto: comando `vision` (`pyproject.toml`, `cli.py`), pacote Python `vision`, `Vision.exe` e o lançador, pasta do projeto, README, nome do repositório no GitHub e links.
    - Textos de treino e scripts: `scripts/ativacao/` (`preparar_treino.py` com as grafias e frases negativas, `gravar_minha_voz.py`) e os testes.
    - **Identificadores internos que quebram em silêncio** (achados da revisão; o resumo é `git grep -i vision` em `src/`, `scripts/`, `tests/`, `ui/`, `evals/`, `README.md` e `config.yaml`, cerca de 1050 linhas):
      - Instância única e inicialização: o mutex `Local\VisionNucleo` (`nucleo.py`) e o `ID_APP` (`interface.py`), o atalho `Vision.lnk` e o script gráfico `visionw` (`inicializacao.py`, `pyproject.toml`). Renomear um lado só pode deixar **dois núcleos** rodando; o atalho antigo precisa ser removido, senão o Windows continua abrindo o executável antigo.
      - Variáveis de ambiente `VISION_*` (`VISION_HOME`, `VISION_TOKEN`, `VISION_URL`, `VISION_SEM_AJUSTES` etc.): quem já as define perde a configuração sem aviso. Aceitar os dois nomes por um tempo.
      - Nomes que outros sistemas veem: os programas Wyoming `vision-parakeet` e `vision-piper` (`voice/wyoming.py`; quem montou o pipeline do Assist precisa reconfigurar), o ícone da bandeja e `prog="vision"` no `cli.py`.
      - **Dados no disco:** a chave `"vision"` no histórico de conversa (`brain/agent.py`, `voice/loop.py`) é gravada em `data/`. Renomear sem migração deixa as conversas antigas ilegíveis.
      - Palavras do reconhecimento de fala: `NOME` e `GRAFIAS` (`voice/comandos.py`), `COMPLEMENTO_DO_SIM` (`brain/confirmacao.py`) e `ANTES_DO_VERBO` (`tools/casa.py`). Sem trocar, "balinha, sim" não confirma uma ação e "balinha, liga a luz" não é entendido pelo atalho da casa.
      - Arquivos e pasta: `src/vision/` (o pacote), `recursos/vision.ico` e `vision.png`, `scripts/lancador/Vision.cs`, `vision.cmd` na raiz (o README manda pôr a pasta no PATH), `ui/package.json` (`vision-ui`), `uv.lock`, a lista de processos em `tools/pc.py` e os prefixos de `evals/`.
      - Artefatos da ativação: refazer o verificador (`hey_vision_verificador.pkl`) e a calibração (`data/ativacao.json`), e mudar o padrão `hey_vision` em `diagnostico_voz.py` e `gravar_minha_voz.py`.
  - **Cuidados:**
    - Fazer em etapas, cada uma com PR (por exemplo: nome e textos → ativação e modelo → comando e pacote → renomear repositório e pasta). Renomear o pacote e a pasta quebra caminhos, atalhos, `data/` e o MSIX/atalho de inicialização.
    - "balinha" tem acento fácil de o Parakeet escrever de outro jeito ("balinha", "baliña", "ba linha"): calibrar a ativação por texto depois de trocar.
    - Manter `vision` como apelido do comando por um tempo, para não quebrar o que já usa.
    - Treinar o novo modelo para "Hey balinha", com vozes pt-BR e negativas parecidas ("balão", "balanço", "linha"), antes de gravar a própria voz.
- [ ] **T-002** 🔴 **Reiniciar o Vision** para carregar as ferramentas da TV (PRs #46–#48) e testar por voz: "abre o Spotify na TV", "toca blank space no YouTube da TV".
- [ ] **T-003** 🔴 **Parear de novo as teclas da TV** (o pareamento venceu em 06/10). Só dá com a TV ligada: Ajustes → Conexões → TV Samsung, ou `vision tv-parear`.
- [ ] **T-004** 🟡 **Descobrir por que o pareamento da TV vence** (suspeita: a TV reinicia ou renova a sessão). Olhar o log e anotar quando as teclas deixam de reagir.
- [ ] **T-005** 🟡 **Testar na TV de verdade** o que só passou em simulação: desligar, canal, fonte/HDMI, setas, abrir Netflix e navegador, tocar link de mídia (UPnP), timer "desliga a TV em N minutos".
- [ ] **T-006** 🟡 **Treinar o modelo de ativação no Google Colab** e pôr o `.onnx` em `modelos/openwakeword/` (passo a passo no README, no item **"Hey Vision" pelo modelo**). **Esperar a T-001:** como a frase vai virar "Hey balinha", treinar um modelo `hey_vision` agora seria trabalho jogado fora (~2h30 de Colab).
- [ ] **T-007** 🟡 **Gravar a própria voz para o verificador** (`scripts/ativacao/gravar_minha_voz.py`) depois do T-001 e do T-006 (a voz gravada é para a frase nova), e reiniciar o Vision. As gravações ficam em `data/` e não saem do PC.
- [ ] **T-008** 🟢 **Expor `voz.limiar_ativacao` na tela de Ajustes** (hoje só no `config.yaml`).
- [ ] **T-009** 🟢 **Login do Orbit pela tela de Conexões** (se o Orbit mandar código por e-mail, a tela pede).
- [ ] **T-010** 🟢 **Calibrar o microfone** com o headset ligado (`vision teste voz`).
- [ ] **T-011** 🟢 **Ligar a TV pela rede:** impossível (o Wi-Fi dela desliga junto). Alternativa: um controle infravermelho (Broadlink RM4 Mini, `python-broadlink`) como segundo backend. O cliente da TV já é separado das ferramentas para isso.
- [ ] **T-012** 🟢 **Servidor dedicado para o modelo** (PC com GPU menor, rodando só o Ollama; `modelo.host` aponta para ele). Antes: rever o modo jogo e o "modelo sempre carregado", que supõem o Ollama no mesmo PC, e travar a porta 11434 por firewall só para o IP do PC principal.
- [ ] **T-013** 🟢 **Pausar e retomar música na TV** por voz (tecla `pausa` já existe; depende do T-003).
- [ ] **T-017** 🟡 **Controlar a piscina (Tholz) pelo Vision** (pedido de 10/10) — **decidido em 13/10: cliente próprio na nuvem da Tholz (uso pessoal). Só pesquisa e análise feitas; nada de código ainda.**
  - **O que foi levantado (10/10, só leitura):**
    - **Equipamentos** (pela tela "Configurar dispositivos" do app Tholz Smart Home): módulo central **SmartPool 2ª Geração**; trocador de calor **Tholz TurboSilence**; clorador **THC45**; um módulo **TLZ Smart** do aquecimento solar. Cada um aparece como dispositivo separado no app. Não se sabe se cada um tem Wi-Fi próprio ou se passam pelo módulo central.
    - **O app funciona fora do Wi-Fi de casa (dados móveis): indica que há uma nuvem da Tholz no meio** (inferência; o módulo provavelmente se conecta à internet e o app fala com ele pelo servidor).
    - **Não encontrei API pública, biblioteca, integração de Home Assistant nem projeto de engenharia reversa da Tholz** (buscas em português e inglês, GitHub, fóruns, Control4, Alexa e Google Home; ausência de resultado não prova que não exista). Não encontrei APK da Tholz em listagens de terceiros; o app consta na App Store (id1565539566, "Tholz Smart Home", iOS 15+, feito no Brasil), e a ficha diz "compatível com assistentes de voz" sem dizer quais.
    - **Pista de rede local:** o driver Control4 da 4Drivers ("Tholz SmartPool", em Driver Central) conecta no módulo por **uma única conexão TCP/IP** e, segundo a descrição do driver, controla luz (RGBW, RGB ou monocromática), até 2 trocadores de calor (como termostato), até 5 relés auxiliares e até 4 sensores de temperatura extras, nos módulos "Modern" e "Legacy" (provável: a 2ª geração é o "Modern"; não confirmado). Porta, formato das mensagens e se passa pela nuvem **não** constam. Sugere que existe um protocolo de integração; vale perguntar ao suporte da Tholz (um driver de terceiros pode ter vindo de acordo comercial ou de engenharia reversa, então isso não prova um documento acessível).
    - Modelos de módulo que aparecem em lojas (só para pesquisar manual): `PDX1512N`, `MCX1512N` (SmartPool Wi-Fi 2ª Geração) e `MTZ1462N` (RGBW). Pesquisa de manual técnico no site da Tholz ainda não feita.
  - **Análise do app Android 4.1.0 (13/10, só leitura do pacote que o Felipe baixou; nada foi instalado nem executado).** **Tudo abaixo vem de strings do pacote: nada foi executado nem confirmado em tráfego real (região, identity pool e fluxo do authorizer incluídos).**
    - O app é **Flutter** e não traz biblioteca de plataforma pronta (nada de Tuya). O backend é da Tholz na **AWS (us-east-1)**.
    - **Login:** Amazon Cognito (user pool), fluxo **SRP** (a senha não vai pela rede) e renovação por token. Existe também identity pool.
    - **Dados:** uma API REST (API Gateway) autenticada pelo token do login, com rotas de lista de dispositivos, estado, parâmetros, programação, localização, compartilhamento e convites.
    - **Comandos e tempo real:** **AWS IoT Core por MQTT sobre WebSocket**, com um **authorizer personalizado** (o app busca o authorizer na API depois do login). Cada equipamento tem um *shadow* (`$aws/things/<id>/shadow/...`): o comando vai em `desired`, o equipamento responde em `reported`. Há shadows nomeados para as programações (`smartSchedule` e `solarSchedule`). Campos vistos: endpoint, canal, power, brightness, color, effect, setpoint, mode.
    - **Não registrado aqui de propósito:** pool e client IDs do Cognito, endpoints e IDs da API/IoT (repositório público; ficam só no pacote do app e em `data/`).
    - **Ainda desconhecido:** o nome e o cabeçalho do authorizer, o formato exato do comando de cada equipamento, e se a conexão é bloqueada contra inspeção (certificate pinning; não vi sinal).
    - **Integração oficial existe:** o manual do módulo (item 13.6) descreve a skill da Alexa e a action do Google "Tholz Casa Inteligente" (adicionar o produto no app e vincular no app do assistente). A Alexa costuma responder mais rápido que o Google em relatos de usuários (sem teste controlado), e, segundo a imprensa de tecnologia (9to5Google, 08/2026), o Google Assistant começou a sair do Android em 04/09/2026. Para o Vision a Alexa é a melhor das duas; hoje o código só controla luzes por ela.
    - **Termos de uso da Tholz (item 6, versão lida em 13/10) proíbem engenharia reversa** e não citam integrações de terceiros. **Aceito pelo Felipe em 13/10:** é uma IA local, só dele e sem fim comercial, na conta e nos equipamentos dele. Riscos que ele assumiu: a Tholz pode bloquear a conta ou mudar a API a qualquer momento. Nada disso será publicado como produto nem distribuído.
  - **Decisão (13/10): fazer o cliente da nuvem** (com a Alexa e um pedido de API oficial à Tholz como caminhos paralelos, se der). **Plano em etapas, cada uma com PR e revisão:**
    1. **Só leitura:** login Cognito SRP (a senha é digitada pelo próprio Felipe, nunca por mim; sessão e tokens só em `data/`), lista de dispositivos e estado pela API. Serve para descobrir os formatos reais com a conta dele. Nenhum comando ainda. O formato do `desired` só será conhecido com o estado real lido da conta; se a conexão for bloqueada contra inspeção (pinning), o plano trava aqui.
    2. **Estado em tempo real:** conectar no IoT (authorizer + MQTT sobre WebSocket) e ler o `reported`. Ferramenta `piscina_status` (temperatura, o que está ligado), só leitura.
    3. **Luz:** ligar/desligar, brilho, cor e efeito pelo `desired`, com confirmação do estado em `reported` (como a TV confirma cada tecla). Ações em **enum fixo**, valores numéricos validados por faixa, MQTT só no domínio descoberto, nunca log de token nem de payload com ID.
    4. **Bomba, cascata, hidromassagem:** só depois de 1 a 3 estáveis, com `sempre_confirmar=True` e as mesmas regras de enum e faixa da etapa 3. **Aquecedor e clorador por último**, com confirmação, temperatura máxima fixa e nunca ligados por timer (timer só desliga).
    5. Conexões: cartão "Piscina" com login pelo Felipe; aviso claro de sessão vencida.
  - **Decisão anterior (10/10), só histórico, substituída pela de 13/10:** ordem de tentativa Alexa, suporte da Tholz, proxy no celular, rede local. Sobra do histórico: (a) **Alexa:** o Vision só liga e desliga as **luzes** que a conta devolve (`src/vision/tools/casa.py`, `set_light_state` em `alexa.py`); bomba, aquecedor e clorador exigiriam estender `alexa.py` e `casa.py`; teste: ativar a skill no app da Alexa e ver os dispositivos (a mesma conta da Amazon do Vision). (b) **Suporte da Tholz:** perguntar por API ou protocolo de integração (o driver Control4 sugere que há) e procurar o manual técnico. (c) **Proxy no celular** (mitmproxy): só se a leitura do pacote e da API não bastar; higiene: remover o certificado e o proxy do celular ao terminar e manter a captura em `data/` ou no scratchpad. (d) **Rede local:** varredura de portas só leitura se um IP do módulo aparecer no roteador.
  - **Como implementar (quando chegar lá):** seguir o desenho da TV (`vision/tv.py` + `vision/tools/tv.py`): um cliente separado das ferramentas, ferramentas com `escrita=True, confirmar=False, confirmar_se_externo=True` (e `sempre_confirmar=True` para bomba, aquecedor e clorador), enum fixo de ações (nunca texto livre do modelo ao equipamento), `atalho()` para comandos curtos, cartão em Conexões e grupo próprio em `intencao.py`. Se for pela nuvem da Tholz: a senha da conta é digitada pelo próprio usuário (nunca por mim nem em arquivo versionado), sessão e token só em `data/`, token nunca em log, e a conexão só com o domínio descoberto (nunca um host vindo de texto do usuário, da web ou da memória).
  - **Cuidados:**
    - **Segurança dos equipamentos:** bomba, aquecedor e clorador agem em água e energia. Começar só com **leitura** (temperatura, estado) e **luz**; bomba, aquecedor e clorador só depois, e com confirmação por voz. Timers de ação só para desligar (como `desligar_tv`), nunca para ligar o aquecedor ou o clorador.
    - **Fragilidade:** API não oficial pode mudar sem aviso; tratar erro de sessão vencida (como o pareamento da TV) com mensagem clara.
    - **Repositório público:** não versionar login, e-mail da conta, IP, ID de dispositivo, nome que identifique a casa, token nem captura de tráfego. Capturas ficam no scratchpad ou em `data/`, e relatório de captura é mascarado antes de ir ao PR.
  - **Passos de agora, do Felipe:** (1) dizer quando iniciar a etapa 1 (ele digita o login no Vision); (2) opcional, em paralelo: testar a skill da Alexa no app e pedir a API ao suporte da Tholz (contato no site da empresa).

## Em andamento

- [~] **T-018** 🟡 **Cache único para tudo que dá para guardar** (pedido de 13/10) — **etapa 1 (medir) pronta em 14/10: `vision medicao` mostra o resumo; falta juntar alguns dias de uso normal antes da etapa 2. O tempo de validade de cada fonte fica para uma etapa própria, depois de medir.**
  - **Objetivo:** responder rápido sem esperar a internet nem os serviços, gastar menos cota (Tavily, Google, Spotify) e continuar útil quando uma fonte cair. Hoje cada fonte tem seu jeito de guardar; a ideia é um módulo comum, com regras iguais e fáceis de ajustar.
  - **O que já existe hoje (levantado em 13/10 no código; vários mecanismos diferentes):**
    - **Agenda** (`tools/agenda.py`): em memória, janela de ontem até +7 dias numa consulta só; vale `agenda.cache_min` (10 min); o núcleo a refaz a cada 5 min e depois de cada mudança feita pelo Vision (a escrita derruba o cache e um novo vem em segundo plano).
    - **Buscas na web e páginas lidas** (`web.py`, `CacheBuscas`): arquivo `data/cache/web.json`, 6 h (`web.cache_horas`); guarda também o texto das páginas lidas (`ler:<url>`, texto de terceiros em disco); corrompido ou apagado vira cache vazio. Há também o contador de cota (`web-cota.json`).
    - **Manchetes** (`web.py`): 30 min, no mesmo arquivo, com o prazo escrito na própria função.
    - **Clima** (`clima.py`): previsão 15 min em memória; as coordenadas da cidade ficam em `data/cache/clima.json`, sem validade.
    - **Apps do Menu Iniciar** (`tools/pc.py`): 10 min em memória.
    - **Luzes da Alexa** (`alexa.py`): lista em `data/alexa/luzes.json`, refeita no login e por `vision alexa-luzes`; sem validade. `tools/casa.py` só lê a lista.
    - **Memória do assistente** (`memory/store.py`): 128 consultas em memória (LRU).
    - **Credenciais com validade (não são cache e NÃO migram para o módulo comum):** token do Spotify (`spotify.py`, até expirar menos 60 s, no arquivo do login) e a sessão da TV (`data/tv/sessao.json`, sem validade; vence sozinha e o Vision detecta e avisa). A migração da etapa 2 é só agenda, web (buscas, manchetes e páginas lidas), clima, apps do PC e luzes.
    - **Resumo:** relógios diferentes (`time.monotonic` em memória, `time.time` em arquivo), validades fixas no código ou no config, nada mostra a idade do dado, nada mede acertos e erros, e não há como limpar tudo pela tela.
  - **O que ainda não tem cache e é candidato (confirmar a lentidão com medição; a busca no código só achou cache nas fontes acima):**
    - **Orbit** (`tools/orbit.py`, finanças e tarefas): hoje cada pergunta vai ao servidor. Hipótese: o Orbit fica num plano grátis que dorme e demora na primeira chamada (a confirmar medindo). Candidato: lista de tarefas, saldo e gastos do mês; derrubar na hora quando o Vision lançar, editar ou apagar.
    - **Reuniões e notas do Wispr** (`tools/reunioes.py`): "próximas reuniões" e notas recentes.
    - **Spotify**: dispositivos e playlists do usuário (para achar "tocar minha playlist X" sem listar tudo); "o que está tocando" por poucos segundos.
    - **TV** (`tv.py`): estado ligada/desligada e apps abertos, por poucos segundos (evita três consultas por pergunta).
    - **Piscina** (T-017): lista de dispositivos por muito tempo; estado por pouco (ou ao vivo, se o IoT empurrar o estado).
    - **Vídeo do YouTube**: o ID escolhido para uma busca ("blank space") por dias, com a opção de buscar de novo.
    - **Luz da Alexa**: o último estado conhecido, só como dica (a Alexa não avisa quando mudam por fora).
    - **Voz sintetizada (TTS)**: frases fixas (saudação, "feito", "não entendi", confirmações), pré-geradas ou guardadas em arquivo de áudio pelo texto e pela voz usada; deve ser o maior ganho de tempo de resposta percebido (a confirmar medindo). Não há cache de áudio no código hoje.
    - **Briefing e feriados**: resumo do dia montado uma vez; feriados do ano por muito tempo.
    - **Respostas repetidas do agente:** a mesma pergunta de leitura, no mesmo minuto, sem refazer as ferramentas.
  - **O que NÃO guardar em cache:**
    - Escritas e confirmações (criar, apagar, ligar, desligar): sempre ao vivo.
    - Estado do jogo e do PC em tempo real (modo jogo, CPU, GPU).
    - Segredos em arquivo de cache: tokens e logins continuam só onde estão (`data/` de cada serviço), nunca em `data/cache/`.
    - Texto de terceiros reaproveitado como se fosse confiável: página, e-mail, evento ou transcrição lidos do cache continuam sendo **conteúdo externo** para o modelo (a marcação não pode se perder no caminho).
  - **Desenho proposto** (`src/vision/cache.py`, um só para todos, gravando com segurança no estilo de `arquivos.py`):
    - Entrada: `{valor, quando, validade, versao}`, em memória e, se pedir, em `data/cache/<nome>.json` (gravação atômica; arquivo corrompido, de outra versão ou apagado vale cache vazio, nunca erro).
    - `obter(nome, chave, buscar, validade)`: acerto devolve na hora; falha da fonte devolve o velho **com a idade** ("dado de 20 min atrás") em vez de falhar (*stale-if-error*); opcionalmente devolve o velho e atualiza em segundo plano (*stale-while-revalidate*) para fontes lentas.
    - **Invalidação por evento:** uma escrita derruba o espaço de nomes (agenda, Orbit, luzes); desconectar ou trocar de conta apaga o espaço daquele serviço; o botão **Limpar cache** em Ajustes apaga tudo.
    - **Limites:** tamanho máximo por espaço de nomes e por arquivo (descarta os mais velhos); `versao` do formato dos dados para o cache antigo não quebrar uma atualização do Vision.
    - **Relógio injetável** (como `agenda.relogio`), para testar a validade sem esperar: `time.time` no disco (para sobreviver a reinício) e `time.monotonic` em memória (que não sofre com ajuste do relógio do Windows), os dois por um relógio injetável; a migração da etapa 2 mantém o que cada cache usa hoje.
    - **Medição:** acertos, erros, idade média e tempo economizado por fonte, no diagnóstico (`vision teste` e status do sistema) e na tela de Ajustes.
    - **Fala:** quando o dado vier velho por falha da fonte, o Vision diz que é a última informação conhecida.
  - **Tempo de validade (etapa própria, depois da medição):** não definir agora. Para decidir cada um é preciso saber (a) quanto a fonte demora (registrar a latência por ferramenta por alguns dias), (b) com que frequência o dado muda por fora (a agenda muda pelo celular; o Orbit pelo app; o clima de hora em hora; a TV a qualquer momento), (c) o custo de estar velho (um compromisso errado é pior que um clima de 15 min atrás) e (d) a cota (Tavily). Os tempos de hoje (acima) são o ponto de partida. Perguntas para decidir nessa etapa: valores por fonte em `config.yaml` (`cache.<fonte>_min`)? pré-aquecer ao iniciar? o que fazer na madrugada, quando o PC dorme?
  - **Etapas, cada uma com PR e revisão:**
    1. ✅ **Medir (feito em 14/10, `src/vision/medicao.py`):** cada chamada de ferramenta grava em `data/medicoes/` nome, duração, resultado e uma impressão curta dos argumentos (SHA-256 com sal da instalação; o conteúdo não é gravado em texto, mas a impressão pode ser adivinhada para argumento simples e por isso fica só em `data/`; escrita não grava impressão; apaga o que passa de `medicao.dias`). `vision medicao [--dias N]` mostra por ferramenta chamadas, % sem erro (a ferramenta não levantou erro; não quer dizer resultado útil), mediana, p95, e quantas chamadas um cache simulado de 5 min e de 1 h teria respondido sozinho, com a economia de tempo. **Falta:** rodar alguns dias de uso normal e olhar o resumo (consultas que não passam pelo registro de ferramentas, como a atualização da agenda para o painel, não entram nesta medição).
    2. **Módulo comum** (`vision/cache.py`) com testes, e migrar os caches de hoje sem mudar o comportamento (agenda, web, manchetes, páginas lidas, clima, apps do PC, luzes). O que veio do cache como texto de terceiros (buscas e páginas lidas) continua marcado como conteúdo externo (`do_cache` hoje), com teste.
    3. **Novas fontes** na ordem do ganho medido: Orbit, reuniões, Spotify, estado da TV e depois a piscina.
    4. **Áudio das frases fixas** (TTS).
    5. **Ajuste dos tempos** com os dados da etapa 1, *stale-if-error*, aviso da idade na resposta, botão de limpar e números no diagnóstico.
  - **Cuidados:** repositório público (nenhum dado pessoal em código, teste ou exemplo; o cache real fica em `data/cache/`, fora do git); cache de resultado de ferramenta de escrita é proibido; o cache nunca pode fazer o Vision agir sobre dado velho sem avisar (por exemplo, apagar um evento que já mudou: a escrita sempre relê o objeto pelo id na fonte; o id pode vir de uma lista velha do cache, então a confirmação mostra o que foi relido, não o que estava no cache).

- [~] **T-014** 🟡 **PR #37: pacote independente** (instalação sem a pasta do projeto). Aberto; não mesclar sem revisão.
- [~] **T-015** 🟡 **Ativação pelo som:** código e README prontos (PR #45); falta o treino no Colab e as gravações (T-006 e T-007), que agora esperam a T-001 (a frase vai virar "Hey balinha").

## Concluídas

- [x] **T-016** **Controle da TV Samsung (série J) pela rede** (05/10): teclas por PIN, volume e mudo pelo UPnP, YouTube pelo DIAL com OK no perfil, busca de vídeo, timer com ação, cartão em Conexões. PRs [#46](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/46) e [#47](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/47).
- [x] Abrir Spotify e navegador da TV pelo ID do app, "abre o X na TV" por voz (06/10), PR [#48](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/48).
- [x] "Hey Vision" por modelo e por transcrição ao mesmo tempo (`voz.ativacao: ambos`), verificador com a própria voz e scripts de treino (03/10), PR [#45](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/45).
- [x] Voz XTTS e busca na web com memória por assunto e cache da agenda (PRs #22 e #23).
- [x] Histórico anterior: veja os PRs mesclados no GitHub.
