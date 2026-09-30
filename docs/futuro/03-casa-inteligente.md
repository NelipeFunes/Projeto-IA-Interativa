# 03. Casa inteligente: luzes, dispositivos e portão

## Objetivo

Pedir ao Vision "acende a luz da sala", "apaga tudo" ou "abre o portão", como hoje se pede à Alexa, e no fim **substituir a Alexa**. O primeiro passo é o mais pedido: **as luzes que hoje a Alexa controla**.

## A peça central: Home Assistant como hub de dispositivos

Cada marca de lâmpada, tomada ou motor tem o seu jeito de ser controlada: aplicativo, nuvem, protocolo. O **Home Assistant (HA)** já fala com milhares delas. Em vez de o Vision aprender cada marca, ele fala só com o HA.

```
Vision (agente) ──ferramentas casa_*──► Home Assistant ──► lâmpadas, tomadas, portão, sensores
                    (API local com token)
```

- **O HA roda no mesmo PC** (Docker) ou no servidor dedicado (plano 01).
- **O Vision fala com o HA pela API local**, com um *token de acesso de longa duração* criado no HA e guardado no `.env`, nunca no git. Opção: o HA tem um **servidor MCP oficial**, e o Vision já sabe usar MCP, como faz com a agenda.

## As luzes que hoje estão na Alexa

Depende de **como** a luz chega na Alexa. Primeiro é preciso descobrir a marca e o aplicativo de cada lâmpada ou interruptor.

| Situação | Caminho no HA | Observação |
|---|---|---|
| Lâmpada Wi-Fi de marca comum (Tuya/Smart Life, Positivo, Intelbras, etc.) | Integração da marca no HA (Tuya, LocalTuya) | O melhor caso: o HA controla direto, sem a Alexa no meio |
| Lâmpada ou interruptor Zigbee (via Echo com hub) | Um adaptador Zigbee USB no servidor + Zigbee2MQTT/ZHA | Tira a Alexa do caminho de vez |
| Só funciona pela Alexa | Integração *Alexa Media Player* (não oficial): o HA dispara rotinas da Alexa | Provisório: depende da nuvem da Amazon e pode quebrar |

**Recomendação:** levantar a lista de dispositivos (marca e aplicativo de cada um) antes de escolher. A meta é o HA falar **direto** com cada um, e a Alexa virar só um alto-falante a mais, ou sair.

## O portão

Hoje o portão eletrônico é acionado pela Alexa. Três caminhos:

1. **A integração da marca no HA**, se o módulo do portão tiver uma: o caminho mais limpo.
2. **Um relé com ESP32 + ESPHome** ligado à central do motor, simulando o botão do controle. É barato e 100% local, mas mexe na instalação elétrica do motor: é preciso fazer com cuidado ou com um eletricista.
3. **Manter a Alexa só para o portão** por um tempo, disparada pelo HA.

**Segurança do portão:** é a única ação da casa com risco físico real, então tem regras próprias:
- **Sempre** pedir confirmação, mesmo que as luzes não peçam.
- Nunca abrir por um pedido vindo de texto de fora (evento da agenda, memória, resposta de ferramenta): só por pedido seu, dito ou digitado.
- Registrar em log cada abertura (quando e de onde veio o pedido).
- Satélite de fora de casa (se um dia houver), nunca.

## Ferramentas novas do Vision (esboço)

O agente vê poucas ferramentas simples, em português, como já é com a agenda. O modelo pequeno se perde em esquemas grandes.

| Ferramenta | Faz | Escrita? |
|---|---|---|
| `casa_listar` | Lista cômodos e dispositivos ("sala: luz do teto, abajur…") | Não |
| `casa_estado` | Diz se uma luz está acesa, a temperatura, se o portão está aberto | Não |
| `casa_ligar` / `casa_desligar` | Acende ou apaga uma luz ou tomada, ou um cômodo inteiro | Sim (ver abaixo) |
| `casa_ajustar` | Brilho, cor, temperatura do ar | Sim |
| `casa_portao` | Abre ou fecha o portão | **Sim, sempre com confirmação** |

**Confirmação das luzes: uma decisão para o Felipe.** Hoje *toda* escrita pede "Confirma?". Para acender uma luz, isso fica chato. Opções:
- **(a)** luzes e tomadas sem confirmação e portão sempre com (recomendado);
- **(b)** tudo com confirmação;
- **(c)** sem confirmação só quando o pedido for claro e de um cômodo só.

Em qualquer caso, o texto que vem de fora (agenda, memória) **nunca** dispara ação na casa sozinho.

**Nomes:** o HA dá nomes técnicos (`light.sala_teto`). O Vision precisa de apelidos em português ("luz da sala", "abajur do quarto") num arquivo de configuração próprio, editável à mão. O prompt mostra só os apelidos.

## Ordem sugerida

1. Levantar os dispositivos: o que é cada luz, a marca, o aplicativo e como chega na Alexa.
2. Subir o Home Assistant no Docker e integrar as luzes pelo melhor caminho de cada uma.
3. Criar as ferramentas `casa_*` no Vision (primeiro só luzes), com apelidos e testes com um HA falso, como já é feito com a agenda falsa.
4. Decidir a política de confirmação das luzes.
5. Portão: escolher o caminho (integração, relé ou Alexa) e aplicar as regras de segurança.
6. Com os satélites (plano 02), a casa inteira passa a obedecer por voz em qualquer cômodo.
7. Quando tudo estiver no HA, aposentar a Alexa ou deixá-la só como caixa de som.
