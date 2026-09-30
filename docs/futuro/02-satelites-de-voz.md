# 02. Satélites de voz pela casa

## Objetivo

Falar com o Vision de qualquer cômodo, sem estar no PC. Cada cômodo ganha um aparelho pequeno com microfone e alto-falante (o **satélite**), que ouve o "Hey Vision", manda a sua fala para o servidor e toca a resposta.

## Hardware de um satélite (lista do rascunho, revisada)

| Peça | Para quê | Observação |
|---|---|---|
| **ESP32-S3 DevKitC N16R8** | O cérebro do satélite | Os 8 MB de PSRAM são necessários para rodar a palavra de ativação no próprio chip. A PSRAM é octal: `psram: mode: octal` no ESPHome |
| **Microfone I2S INMP441** | Ouvir | Digital, pouco ruído; ideal para protoboard |
| **Amplificador I2S MAX98357A** + alto-falante 3 W 4 Ω | Falar | Suficiente para um quarto; na sala, talvez um alto-falante maior |
| **Anel de LED WS2812** (opcional) | Mostrar o estado | Mesmas cores do orbe: roxo parado, azul ouvindo, magenta pensando, ciano falando |
| Fonte 5 V 2 A (USB-C) e caixa | Montagem | A caixa, no fim (impressão 3D ou pronta) |

**Alternativa pronta:** o *Home Assistant Voice Preview Edition* ou kits ESP32-S3 com microfone já montado. Custam mais, mas poupam solda e ajuste de áudio. Vale para o primeiro cômodo, para comparar.

## Firmware

**ESPHome** com o componente `voice_assistant` e **micro_wake_word**. A palavra de ativação roda **no próprio ESP32**: ele só manda áudio para o servidor depois de ouvir o nome. Isso é bom para a privacidade (nada sai do aparelho enquanto ele espera) e para a rede.

- **O nome:** o micro_wake_word tem modelos prontos ("hey jarvis", "okay nabu", "alexa"), mas não "hey vision". Existe um processo de treino de modelo próprio (gera amostras sintéticas com TTS e treina). Isso também resolveria o "Hey Vision" do PC, que hoje é por transcrição.
- **Enquanto não houver modelo próprio:** usar "hey jarvis" no satélite, que já vem pronto, ou mandar o áudio sempre ao servidor. Esta segunda opção é pior para a privacidade e a rede.

## Segurança do satélite

- **API do ESPHome cifrada:** `api: encryption: key:` em todo satélite, com uma chave por aparelho, e senha de OTA.
- **Segredos fora do git:** o `secrets.yaml` do ESPHome (Wi-Fi, chave da API, OTA) nunca entra no repositório; só um `secrets.exemplo.yaml` sem valores.
- **O Wyoming não cifra nem autentica:** o trajeto HA ↔ Wyoming fica dentro do servidor (rede interna do Docker), nunca exposto na rede de casa.
- **Rede separada** para satélites e dispositivos da casa, se o roteador permitir: um aparelho comprometido não enxerga o resto.

## Como o satélite conversa com o Vision

Há dois caminhos. A escolha depende de usarmos ou não o Home Assistant (plano 03).

### Caminho A: pelo Home Assistant (mais simples de montar)

```
satélite ESPHome ──► Home Assistant (pipeline Assist)
                        ├─ fala → texto: serviço Wyoming (o do Vision, com Parakeet)
                        ├─ conversa: agente de conversa que chama o Vision (/conversa)
                        └─ texto → fala: serviço Wyoming (Piper, a mesma voz do PC)
```

- O ESPHome e o HA já se entendem: adoção do aparelho, ajuste de volume e LED ficam prontos.
- **Do nosso lado, falta:**
  1. um servidor **Wyoming** para o Parakeet e o Piper do Vision. O protocolo é simples, e existem bibliotecas prontas em Python;
  2. um **agente de conversa** no HA que mande o texto para o `/conversa` do Vision, com token e o id do satélite como sessão. Pode ser uma integração própria pequena, ou uma já existente que fale com uma API compatível com a da OpenAI, se o Vision passar a oferecer uma.
- **O que se perde:** o HA fica no meio do caminho, e alguma latência entra.
- **Limite de confiança:** neste caminho, o HA é o único cliente do Vision, com um token só. O "id do satélite" é informado pelo HA, não provado. Quem acessa o HA (painel, Assist, um satélite comprometido) fala com o Vision como qualquer cômodo. Por isso o acesso ao HA é só do dono (o dia a dia com um usuário sem administrador), e as ações de alto risco (acessos físicos) não são aceitas por este caminho.

### Caminho B: direto no Vision (mais controle)

```
satélite ESPHome ──(API nativa do ESPHome)──► Vision (vira o "servidor de voz")
```

- No ESPHome, o aparelho é o servidor da API nativa e quem conecta nele é o cliente (hoje, o HA). Aqui o Vision passaria a ser esse **cliente** da API de voz. Outra opção é o **wyoming-satellite** num Raspberry Pi em vez do ESP32.
- **O que se ganha:** mais controle (conversa contínua, "pode desligar", cartão na tela).
- **O que custa:** muito mais código para manter.

**Recomendação:** começar pelo **caminho A**, que é menos código e testa o hardware rápido. Migrar para o B só se o HA atrapalhar a conversa contínua.

## O que muda no Vision

1. **Várias fontes de áudio ao mesmo tempo.** Hoje há um `LoopVoz` com um microfone. No futuro, cada satélite é uma fonte, com a sua sessão (`canal="voz"`, `sessao="sala"`). A conversa da sala não se mistura com a do quarto.
2. **A resposta volta para quem perguntou.** A fala da resposta toca no satélite que ouviu, não no PC.
3. **Um pedido por vez no modelo.** Com uma GPU, dois cômodos falando juntos entram numa fila. O segundo ouve "só um instante".
4. **A tela mostra de onde veio a fala** ("sala: marca o dentista…"), e a bolha só aparece se o PC estiver no mesmo cômodo.
5. **Confirmação por satélite:** o "sim" precisa vir **do mesmo satélite** que ouviu o pedido, dentro do prazo (hoje, 30 s), e só com confirmação explícita ("sim", "confirmo"), como já é no PC. Um "sim" da TV em outro cômodo não confirma nada. No caminho A, isso depende do id que o HA informa (ver o limite de confiança acima).
6. **Conversa contínua por satélite:** "Hey Vision" abre e "pode desligar" fecha, como no PC. O fim da conversa volta o satélite para o micro_wake_word. No Caminho A, isso depende de o HA deixar o microfone aberto entre as falas. Isso precisa de teste.

## Privacidade

- **Enquanto espera,** nada sai do satélite: é o micro_wake_word no chip.
- **O áudio** vai só para o servidor da casa, nunca para fora, e com a API do ESPHome cifrada (ver "Segurança do satélite").
- **O LED** mostra quando ele está ouvindo, e um botão físico de mudo (opcional) corta o microfone.
- **Transcrições das conversas** continuam em `data/conversas`, como hoje.

## Ordem sugerida (a do rascunho, ajustada)

1. Terminar o plano 04 (várias fontes de áudio, sessão por satélite).
2. Subir o Home Assistant (plano 03) e testar o Assist pelo navegador, já com o Vision como agente de conversa.
3. Montar o primeiro satélite na protoboard e gravar o ESPHome.
4. Adotar o satélite no HA; ajustar o ganho do microfone, o volume e o LED.
5. Treinar o "hey vision" para o micro_wake_word (vale também para o PC).
6. Montar a caixa; replicar para os outros cômodos.

## Dúvidas para quando chegar a hora

- Quantos cômodos? Isso define se uma GPU dá conta de conversas simultâneas.
- Num cômodo com TV ou som ligado, o INMP441 sozinho vai ouvir bem de longe? Talvez precise de dois microfones com cancelamento de eco.
- Se houver uma caixa de som boa, a resposta poderia tocar nela em vez do alto-falante pequeno do satélite.
