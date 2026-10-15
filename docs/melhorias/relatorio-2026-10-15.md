# Análise geral do Vision — relatório de 15/10/2026

Leitura de 3 minutos. Medições no `qwen3.5:4b` com as 53 ferramentas de produção, 81 a 82 casos × 2 rodadas (`evals/run.py --suite todas`).

## Em 30 segundos

- **A resposta ficou mais que 2× mais rápida sem perder qualidade**: tempo médio por pedido (da fala já transcrita até o fim da
  resposta) **2,75 s → 1,2 s**, p90 3,22 s → 1,7 s; acertos 154/162 → 155/164 (mesma faixa).
- **O culpado estava no prompt, não no modelo**: a hora com minutos e as memórias ficavam no meio do prompt de sistema e o Ollama
  reprocessava ~7.800 tokens em todo pedido (preparo de 1,94 s). Agora o preparo é de 0,42 s (0,53 s numa conversa realista de 12 pedidos, contra 2,01 s).
- **A avaliação antiga subestimava o tempo** ("~1 s por resposta"): só registrava agenda, memória e Orbit (1/3 das ferramentas) e rodava tudo no
  mesmo minuto, com o cache sempre quente. A nova usa as 53 ferramentas com aparelhos falsos (provado isolado: não age no PC nem na internet).
- **Modelo maior não ajudou** (tabela abaixo): o 4B continua sendo o melhor para voz neste PC.
- **Meta de 1 s**: pedido simples pelo modelo já está em ~0,8 a 1,2 s (mediana por categoria abaixo). O que falta para "fim da fala →
  primeira palavra em 1 s" está fora do cérebro: silêncio de fim de fala (800 ms), bipe bloqueante (~450 ms), transcrição e síntese
  → PR de voz separado, **aberto para você validar de ouvido** (T-021).

## PRs

| PR | O quê | Estado |
|---|---|---|
| [#60](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/60) | Avaliação de produção (53 ferramentas, relógio sintético, memórias, 7 casos de segurança, métricas de prefill) | mergeado; 3 revisões do Opus |
| [#61](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/61) | Refator: limpeza de texto de comando num lugar só (`vision.frases`) | mergeado; Opus: aprovar |
| [#62](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/62) | Segurança e robustez: ambiente dos servidores filhos, rede local, janela, tarefas soltas, logs, retenção | mergeado; Opus: aprovar com ressalvas (todas tratadas) |
| [#63](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/63) | Prompt estável + contexto do pedido (o ganho de 2×); `modelo.contexto` 12.288 → 16.384 | mergeado; 3 rodadas do Opus (a 2ª bloqueou, corrigido) |
| (esta documentação) | relatório, lições, estudo de servidores/nuvem, tarefas | este PR |
| [#64](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/64) | Voz: bipe "entendi" sem bloquear, silêncio de fim de fala 800 → 600 ms, 4 bugs do laço de voz | **ABERTO para você validar de ouvido**; Opus: aprovar com ressalvas (tratadas) |
| resposta pronta | pular a 2ª chamada ao modelo quando a ferramenta já devolve a frase | **branch descartada antes do PR** (o Opus mandou refazer): T-022 |

## Onde o tempo vai (mediana do tempo por categoria, 4B, fala já transcrita; bateria "depois" do desenho final do prompt, valores da bateria de confirmação diferem ±0,1 s)

| Categoria | Antes | Depois |
|---|---|---|
| conversa | 2,14 s | **0,52 s** |
| correção ("não, às 11") | 2,69 s | **1,07 s** (o "sim" não passa pelo modelo: ~0 s) |
| PC | 2,46 s | **0,91 s** |
| TV | 2,49 s | **0,98 s** |
| música | 2,66 s | **1,04 s** |
| timer | 2,67 s | **1,11 s** |
| finanças | 2,73 s | **1,19 s** |
| luzes | 2,89 s | **1,25 s** |
| agenda | 2,93 s | **1,53 s** |
| pedido com várias ações | 2,99 s | **1,36 s** |
| reuniões (Wispr) | 5,53 s | 3,69 s |

Por número de chamadas ao modelo (depois, **média**): **1 chamada 0,79 s · 2 chamadas 1,29 s · 3 chamadas 1,56 s**. Cada volta extra custa ~0,3 a 0,5 s:
a "resposta pronta" (T-022) e menos `luzes_listar` antes de `luz_apagar` são o próximo ganho. Os atalhos sem modelo (<0,5 s) não passam por aqui.

## Modelos testados

Mesma avaliação de produção (53 ferramentas, prompt de antes do PR #63), **uma rodada por caso** (margem de ±3 pontos); `qwen3.5:4b` é a referência
(rodada 0 da bateria "antes"). Os modelos que cabem na placa (8 GB) rodaram os 81 casos; os que precisam da RAM (offload) rodaram um
subconjunto de 27 casos e com prazo. Os tempos dos modelos maiores têm algum ruído: eu rodava testes na CPU ao mesmo tempo, em rajadas de 2 minutos.

| Modelo | Acertos | Tempo médio | Mediana | Preparo do prompt | Observação |
|---|---|---|---|---|---|
| `qwen3.5:4b (atual)` | 77/81 (95%) | 2.8 s | 2.7 s | 1.9 s | 81 casos; referência |
| `qwen3.5:4b (atual), só os 27 casos` | 27/27 (100%) | 2.8 s | 2.8 s | 1.9 s | mesmos 27 casos dos modelos grandes |
| `granite4.1:3b` | 62/81 (77%) | 2.2 s | 2.0 s | 1.3 s | 81 casos; carga 24 s |
| `granite4.1:8b` | 60/67 (90%) | 12.0 s | 10.5 s | 5.0 s | 67 de 81 casos — parcial (prazo); carga 55 s |
| `nemotron-3.5-lightning:30b` | 13/16 (81%) | 57.6 s | 56.1 s | 54.5 s | 27 casos (em RAM) — parcial (prazo); carga 267 s |
| `ornith:9b` | 75/81 (93%) | 4.2 s | 3.9 s | 2.9 s | 81 casos; carga 46 s |
| `qwen3.5:0.8b` | 21/81 (26%) | 1.2 s | 1.1 s | 0.8 s | 81 casos; carga 16 s |
| `qwen3.5:2b` | 57/81 (70%) | 1.5 s | 1.4 s | 0.8 s | 81 casos; carga 32 s |
| `qwen3.5:35b` | 21/22 (95%) | 40.0 s | 39.5 s | 37.6 s | 27 casos (em RAM) — parcial (prazo); carga 182 s |
| `qwen3.5:9b` | 71/81 (88%) | 6.7 s | 6.5 s | 4.5 s | 81 casos; carga 57 s |
| `qwen3.8:27b` | 5/6 (83%) | 84.7 s | 77.4 s | 55.7 s | 27 casos (em RAM) — parcial (prazo); carga 239 s |

**Leitura**

- **O `qwen3.5:4b` continua sendo o melhor para voz**: 95% de acertos a 2,8 s com o prompt antigo (e 1,2 s com o prompt novo, #63).
- **Menor perde demais**: 2B 70%, 0,8B 26%; o `granite4.1:3b` fica em 77% e só é mais rápido que o 4B por poucos décimos.
- **Maior não ajuda**: 9B 88% a 6,7 s (repete a medição de 30/09: 48/56 contra 56/56); `ornith:9b` é o melhor deles (93% a 4,2 s) e ainda assim perde do 4B em acerto e leva 1,5× mais tempo; `granite4.1:8b` 90% a 12,0 s.
- **Usar a RAM como VRAM não serve para voz neste PC**: os modelos que não cabem na placa levam, em média, de **40 a 85 s por resposta** (pior pedido: 111 s) e de 3 a 4,5 minutos para carregar do HD (`nemotron-3.5-lightning:30b`, MoE com 3B ativos: 58 s por resposta; `qwen3.5:35b`: 40 s, 21/22 certos; `qwen3.8:27b`: 85 s, 5/6; as amostras são pequenas, 6 a 22 casos, por causa do prazo). A qualidade do 35B é boa, mas 40 s está a mais de 15× do teto de 2,5 s da meta. `qwen3.5:27b` e `granite4.1:30b` foram baixados e **não foram testados** (falta de tempo; ficariam na mesma ordem de grandeza).
- **Decisão sugerida (T-023): ficar no `qwen3.5:4b`.** O ganho de velocidade veio do prompt, não do modelo. Para a RAM valer a pena seria preciso uma placa com VRAM para o modelo inteiro, por exemplo no PC dedicado à IA (T-012 e `servicos-em-outros-servidores.md`).

**Modelos baixados nesta análise e apagados no fim** (D:, ~109 GB liberados): `qwen3.5:0.8b`, `qwen3.5:2b`, `granite4.1:3b`, `granite4.1:8b`, `granite4.1:30b`, `ornith:9b`, `nemotron-3.5-lightning:30b`, `qwen3.5:35b`, `qwen3.8:27b`, `qwen3.5:27b`. Ficam `qwen3.5:4b` (produção), `qwen3.5:9b` (já existia) e `embeddinggemma` (memória). Não puxados porque exigem um Ollama mais novo (não atualizei sem a sua licença): `tev1:4b` e `nimble:9b`.

## O que fazer de manhã

1. **Reiniciar o Vision** (já está na `main` atualizada: prompt novo e contexto de 16 mil tokens) e usar normalmente.
2. **Ouvir o PR #64** (bipe "entendi" mais cedo e `voz.silencio_fim_ms: 600`): se cortar a sua pausa no meio da frase, volte o número para 800 no `config.yaml`. Gostou: mesclar o PR.
3. Decidir a T-023 (modelo) olhando a tabela acima. Minha leitura: **ficar no `qwen3.5:4b`**.

## O que ficou aberto (tarefas em `TAREFAS.md`)

T-021 voz · T-022 resposta pronta · T-023 decidir modelo · T-024 mais ganhos de prompt · T-025 modelo no SSD · T-026 pendências da avaliação ·
T-027 refatorações · T-028 servidores/nuvem (estudo feito) · T-029 tirar segredos do `os.environ`.

## Documentos

- [`LICOES.md`](LICOES.md): 26 lições (a principal: **medir tempo e acerto juntos** — o 1º desenho do prompt cortou o tempo e derrubou os acertos de 93% para 81%).
- [`servicos-em-outros-servidores.md`](servicos-em-outros-servidores.md): estudo hipotético de separar o Vision em servidores e o que poderia ir para a nuvem (nada implementado).

## Nesta noite

O benchmark de modelos rodou de 5h45 até ~7h25, em paralelo com as últimas revisões e os merges, com prazo por modelo: a comparação dos modelos
grandes é **parcial** (a tabela mostra quantos casos cada um chegou a rodar). Lição 23: o que roda sozinho começa primeiro.
