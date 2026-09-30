# 04. Deixar o programa pré-pronto

Mudanças pequenas que podem ser feitas **aos poucos, já agora**, para que o servidor dedicado (01), os satélites (02) e a casa (03) encaixem sem reescrever o que existe. Cada item é independente e pode virar um PR próprio.

## Para o servidor dedicado (plano 01)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 1 | Endereço do servidor configurável (`servidor.endereco`), no lugar do `127.0.0.1` fixo na janela e no `pedir_janela`. `http://` só para `127.0.0.1`; o núcleo só escuta fora do `127.0.0.1` no modo servidor, com TLS e o item 3 prontos | `interface.py`, `nucleo.py`, `server.py` | A janela precisa se conectar a outra máquina |
| 2 | Lista de Origins permitidas vinda da configuração | `server.py` (`origens_permitidas`) | A interface pode vir de outro endereço |
| 3 | **Tokens por aparelho**, com nome, guardados em `data/aparelhos.json`, mais revogação. Pareamento com código que expira, vale uma vez, limita tentativas e só abre por comando no servidor ou sessão já autenticada. Token só no cabeçalho ou no fragmento, nunca em `?token=` | `server.py` | Revogar um aparelho perdido sem trocar todos |
| 4 | Núcleo sem obrigação de Windows: bandeja, atalhos, mutex e atalho de inicialização como peças opcionais, detectadas por sistema | `nucleo.py`, `bandeja.py`, `inicializacao.py` | Rodar no Ubuntu sem tela |
| 5 | Modo "só servidor" (`vision servidor-completo`): agente, voz remota e API, sem janela nem bandeja | `cli.py`, `nucleo.py` | O que o servidor Linux vai rodar |
| 6 | Login do Google sem navegador local: gera o link de consentimento, abre em outro aparelho e cola o endereço de retorno (o fluxo de aparelho do Google não aceita o escopo da Agenda) | `google_login.py` | Servidor sem tela |

## Para os satélites (plano 02)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 7 | **Fonte de áudio como interface**: `FonteDeAudio` (blocos de 80 ms) e `SaidaDeAudio` (tocar, interromper). O microfone do PC passa a ser só uma das fontes | `voice/audio.py`, `voice/loop.py` | Cada satélite é uma fonte |
| 8 | **Um `LoopVoz` por fonte**, cada um com a sua sessão (`sessao="pc"`, `"sala"`…) e o seu estado de conversa | `voice/loop.py`, `nucleo.py` | Conversa da sala não mistura com a do quarto |
| 9 | **Fila única para o modelo** (um pedido por vez na GPU) e aviso "só um instante" para quem está esperando | `brain/agent.py` ou `nucleo.py` | Dois cômodos falando juntos |
| 10 | Eventos da tela com a origem (`"fonte": "sala"`) | `eventos.py`, `ui/src/tipos.ts` | Mostrar de onde veio a fala |
| 11 | Confirmação amarrada à fonte: o "sim" vale só da mesma fonte, dentro do prazo | `brain/agent.py` (a sessão já é por fonte, se o item 8 for feito) | Um "sim" da TV em outro cômodo não confirma |
| 12 | Serviço **Wyoming** (fala → texto com Parakeet, texto → fala com Piper), **desligado por padrão**. O protocolo não tem autenticação nem TLS: escuta só no `127.0.0.1` ou na rede interna do Docker, e só o HA o alcança | módulo novo `voice/wyoming.py` | Caminho A dos satélites, pelo Home Assistant |

## Para a casa (plano 03)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 13 | **Política de confirmação por ferramenta**, e não só "escrita ou não": `confirmar="sempre" / "nunca" / "se_ambiguo"` | `tools/base.py`, `brain/agent.py` | Luzes sem "Confirma?", acessos físicos sempre com. Para acesso físico, "sempre" quer dizer confirmação pela interface autenticada (ou PIN), **nunca pela voz**, e nunca quando o turno leu conteúdo de fora ou veio pelo caminho A |
| 14 | **Origem por turno:** se o turno leu conteúdo de fora (agenda, memória, resultado de ferramenta externa), toda ferramenta `casa_*` passa a exigir confirmação, até as de `"nunca"`. A regra fica no código, não no prompt | `brain/agent.py` | Um texto da agenda nunca aciona nada na casa |
| 15 | Ferramentas `casa_*` com um HA falso nos testes (como o `calendario_falso.py`) | `tools/casa.py`, `tests/fakes/ha_falso.py` | Desenvolver sem mexer na casa de verdade |
| 16 | Apelidos dos dispositivos em arquivo próprio, editável (`data/casa.yaml`) | novo | O modelo vê "luz da sala", não `light.sala_teto` |

## O que já está pronto e ajuda

- **Núcleo e janela separados**, conversando por WebSocket com token (Fase B). É a base do plano 01.
- **Barramento de eventos** no formato da tela: um satélite ou um celular podem assinar os mesmos eventos.
- **Sessões por canal no agente**: base dos itens 8 e 11.
- **Confirmação de escrita com prazo por voz** e cancelada ao fechar a conversa: base dos itens 11 e 13.
- **MCP já em uso** (agenda): o HA tem servidor MCP, e o item 15 pode ir por esse caminho.

## Ordem sugerida

- **Primeiro, os que não mudam nada para o usuário** e deixam o código mais limpo: o 7. Depois 8, 9 e 10.
- **O 13 e o 14 entram juntos, no mesmo PR** (ou o 14 antes): uma ferramenta sem confirmação, sem a regra de origem, abre caminho para injeção de prompt.
- **Os do servidor (1 a 6)** só quando o servidor estiver perto de existir.
- **Os da casa (13 a 16)** junto com o plano 03.
