# 04. Deixar o programa pré-pronto

Mudanças pequenas que podem ser feitas **aos poucos, já agora**, para que o servidor dedicado (01), os satélites (02) e a casa (03) encaixem sem reescrever o que existe. Cada item é independente e pode virar um PR próprio.

## Para o servidor dedicado (plano 01)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 1 | Endereço do servidor configurável (`servidor.endereco`), no lugar do `127.0.0.1` fixo na janela e no `pedir_janela` | `interface.py`, `nucleo.py`, `server.py` | A janela precisa se conectar a outra máquina |
| 2 | Lista de Origins permitidas vinda da configuração | `server.py` (`origens_permitidas`) | A interface pode vir de outro endereço |
| 3 | **Tokens por aparelho**, com nome, guardados em `data/aparelhos.json`, mais revogação | `server.py` | Revogar um aparelho perdido sem trocar todos |
| 4 | Núcleo sem obrigação de Windows: bandeja, atalhos, mutex e atalho de inicialização como peças opcionais, detectadas por sistema | `nucleo.py`, `bandeja.py`, `inicializacao.py` | Rodar no Ubuntu sem tela |
| 5 | Modo "só servidor" (`vision servidor-completo`): agente, voz remota e API, sem janela nem bandeja | `cli.py`, `nucleo.py` | O que o servidor Linux vai rodar |
| 6 | Login do Google sem navegador local (mostra um link para abrir no celular) | `google_login.py` | Servidor sem tela |

## Para os satélites (plano 02)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 7 | **Fonte de áudio como interface**: `FonteDeAudio` (blocos de 80 ms) e `SaidaDeAudio` (tocar, interromper). O microfone do PC passa a ser só uma das fontes | `voice/audio.py`, `voice/loop.py` | Cada satélite é uma fonte |
| 8 | **Um `LoopVoz` por fonte**, cada um com a sua sessão (`sessao="pc"`, `"sala"`…) e o seu estado de conversa | `voice/loop.py`, `nucleo.py` | Conversa da sala não mistura com a do quarto |
| 9 | **Fila única para o modelo** (um pedido por vez na GPU) e aviso "só um instante" para quem está esperando | `brain/agent.py` ou `nucleo.py` | Dois cômodos falando juntos |
| 10 | Eventos da tela com a origem (`"fonte": "sala"`) | `eventos.py`, `ui/src/tipos.ts` | Mostrar de onde veio a fala |
| 11 | Confirmação amarrada à fonte: o "sim" vale só da mesma fonte, dentro do prazo | `brain/agent.py` (a sessão já é por fonte, se o item 8 for feito) | Um "sim" da TV em outro cômodo não confirma |
| 12 | Serviço **Wyoming** (fala → texto com Parakeet, texto → fala com Piper), atrás de um parâmetro | módulo novo `voice/wyoming.py` | Caminho A dos satélites, pelo Home Assistant |

## Para a casa (plano 03)

| # | Mudança | Onde | Por quê |
|---|---|---|---|
| 13 | **Política de confirmação por ferramenta**, e não só "escrita ou não": `confirmar="sempre" / "nunca" / "se_ambiguo"` | `tools/base.py`, `brain/agent.py` | Luzes sem "Confirma?", portão sempre com |
| 14 | **Origem do pedido marcada** em cada chamada (fala sua, texto seu, ou conteúdo de fora como agenda e memória), para ferramentas sensíveis recusarem pedido vindo de fora | `brain/agent.py` | O portão nunca abre por um texto da agenda |
| 15 | Ferramentas `casa_*` com um HA falso nos testes (como o `calendario_falso.py`) | `tools/casa.py`, `tests/fakes/ha_falso.py` | Desenvolver sem mexer na casa de verdade |
| 16 | Apelidos dos dispositivos em arquivo próprio, editável (`data/casa.yaml`) | novo | O modelo vê "luz da sala", não `light.sala_teto` |

## O que já está pronto e ajuda

- **Núcleo e janela separados**, conversando por WebSocket com token (Fase B). É a base do plano 01.
- **Barramento de eventos** no formato da tela: um satélite ou um celular podem assinar os mesmos eventos.
- **Sessões por canal no agente**: base dos itens 8 e 11.
- **Confirmação de escrita com prazo por voz** e cancelada ao fechar a conversa: base dos itens 11 e 13.
- **MCP já em uso** (agenda): o HA tem servidor MCP, e o item 15 pode ir por esse caminho.

## Ordem sugerida

- **Primeiro, os que não mudam nada para o usuário** e deixam o código mais limpo: 7 e 13. Depois 8, 9 e 10.
- **Os do servidor (1 a 6)** só quando o servidor estiver perto de existir.
- **Os da casa (14 a 16)** junto com o plano 03.
