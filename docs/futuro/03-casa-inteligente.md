# 03. Casa inteligente: luzes, dispositivos e acessos

## Objetivo

Pedir ao Vision "acende a luz", "apaga tudo" ou acionar outros dispositivos da casa, e no fim **substituir o assistente comercial** que faz isso hoje. O primeiro passo é o mais pedido: **as luzes**.

> A lista real de dispositivos, cômodos e marcas fica em `data/casa.yaml`, fora do git. Este plano é genérico de propósito: o repositório é público.

## A peça central: Home Assistant como hub de dispositivos

Cada marca de lâmpada, tomada ou motor tem o seu jeito de ser controlada: aplicativo, nuvem, protocolo. O **Home Assistant (HA)** já fala com milhares delas. Em vez de o Vision aprender cada marca, ele fala só com o HA.

```
Vision (agente) ──ferramentas casa_*──► Home Assistant ──► lâmpadas, tomadas, sensores
                    (API local com token)
```

- **O HA roda no mesmo PC** (Docker) ou no servidor dedicado (plano 01).
- **O Vision fala com o HA pela API local**, com um *token de acesso de longa duração* guardado no `.env`, nunca no git, em log, exceção ou mensagem de commit. Opção: o HA tem um **servidor MCP oficial**, e o Vision já sabe usar MCP, como faz com a agenda.
- **Um usuário do HA só para o Vision, sem administrador.** O token herda as permissões de quem o criou. O Vision vê só a lista de entidades permitidas; o que tem risco físico (acessos físicos) fica fora dessa lista.

## As luzes

Depende de **como** cada luz é controlada hoje. Primeiro é preciso descobrir a marca e o aplicativo de cada lâmpada ou interruptor.

| Situação | Caminho no HA | Observação |
|---|---|---|
| Lâmpada Wi-Fi de marca comum (Tuya/Smart Life, Positivo, Intelbras, etc.) | Integração da marca no HA (Tuya, LocalTuya) | O melhor caso: o HA controla direto, sem intermediário |
| Lâmpada ou interruptor Zigbee (pareado num alto-falante com hub) | Um adaptador Zigbee USB no servidor + Zigbee2MQTT/ZHA | Tira o assistente comercial do caminho de vez |
| Só funciona pelo assistente comercial | Integração não oficial (por exemplo, *Alexa Media Player*): o HA dispara rotinas dele | Provisório: depende da nuvem do fabricante, pode quebrar e guarda o login da conta dentro do HA (mais uma credencial para proteger) |

**Recomendação:** levantar a lista de dispositivos (marca e aplicativo de cada um, em `data/casa.yaml`) antes de escolher. A meta é o HA falar **direto** com cada um, e o assistente comercial virar só um alto-falante a mais, ou sair.

## Acessos físicos (portão motorizado, fechadura)

Caminhos possíveis, se um dia fizer sentido:

1. **A integração da marca no HA**, se o módulo tiver uma: o caminho mais limpo.
2. **Um relé com ESP32 + ESPHome** ligado à central do motor, simulando o botão do controle. É barato e 100% local, mas mexe na instalação elétrica: é preciso fazer com cuidado ou com um eletricista. O relé liga **desligado** (`restore_mode: ALWAYS_OFF`, num pino que não pulsa no boot nem na atualização OTA), em pulso curto, e a botoeira física continua funcionando.
3. **Manter o assistente comercial só para isso** por um tempo, disparado pelo HA.

**Regras próprias**, porque é a única ação com risco físico real:
- **Nunca só pela voz.** Um "sim" falado pode vir da TV, de um vídeo ou de alguém perto da janela. O acionamento exige a interface autenticada (botão na janela do PC ou no celular) ou um segundo fator (PIN).
- **Só "abrir" remoto.** Fechar sem ver o portão pode ferir alguém ou um veículo: o fechamento fica com a central do motor (automático, com fotocélula).
- Nunca por pedido vindo de texto de fora (evento da agenda, memória, resposta de ferramenta).
- Nunca pelo caminho A dos satélites (o HA no meio, plano 02), nem por satélite perto de janela ou da rua.
- Registrar em log cada acionamento: quando, de qual aparelho e por qual caminho (sem áudio).

## Ferramentas novas do Vision (esboço)

O agente vê poucas ferramentas simples, em português, como já é com a agenda. O modelo pequeno se perde em esquemas grandes.

| Ferramenta | Faz | Escrita? |
|---|---|---|
| `casa_listar` | Lista cômodos e dispositivos ("sala: luz do teto, abajur…") | Não |
| `casa_estado` | Diz se uma luz está acesa, a temperatura, se algo está aberto | Não |
| `casa_ligar` / `casa_desligar` | Acende ou apaga uma luz ou tomada, ou um cômodo inteiro | Sim (ver abaixo) |
| `casa_ajustar` | Brilho, cor, temperatura do ar | Sim |
| `casa_acesso` | Aciona um acesso físico (só "abrir") | **Sim, e nunca só por voz** (ver acima) |

**Confirmação das luzes: uma decisão do dono do projeto.** Hoje *toda* escrita pede "Confirma?". Para acender uma luz, isso fica chato. Opções:
- **(a)** luzes e uma lista explícita de tomadas seguras sem confirmação; o resto com (recomendado). Tomada genérica pode ter aquecedor ou ferro ligado;
- **(b)** tudo com confirmação;
- **(c)** sem confirmação só quando o pedido for claro e de um cômodo só.

Em qualquer caso, o texto que vem de fora (agenda, memória) **nunca** dispara ação na casa sozinho. Se o turno leu conteúdo de fora, até as ações "sem confirmação" passam a pedir confirmação (plano 04, item 14).

**Nomes:** o HA dá nomes técnicos (`light.sala_teto`). O Vision precisa de apelidos em português ("luz da sala", "abajur do quarto") num arquivo de configuração próprio, editável à mão. O prompt mostra só os apelidos.

## Ordem sugerida

1. Levantar os dispositivos em `data/casa.yaml`: o que é cada luz, a marca, o aplicativo e como é controlada hoje.
2. Subir o Home Assistant no Docker e integrar as luzes pelo melhor caminho de cada uma.
3. Criar as ferramentas `casa_*` no Vision (primeiro só luzes), com apelidos e testes com um HA falso, como já é feito com a agenda falsa.
4. Decidir a política de confirmação das luzes.
5. Acessos físicos, se fizer sentido: escolher o caminho e aplicar as regras próprias.
6. Com os satélites (plano 02), a casa inteira passa a obedecer por voz em qualquer cômodo.
7. Quando tudo estiver no HA, aposentar o assistente comercial ou deixá-lo só como caixa de som.
