# Avaliação do Jarvis

Gerado em 30/09/2026 01:48 · 28 casos · canal voz · agenda e Orbit falsos · memória com embeddinggemma real

## Resumo

| Modelo | Acertos | Tempo médio | p90 | 1ª palavra (média) | Insistências | VRAM |
|---|---|---|---|---|---|---|
| qwen3.5:4b | **27/28** | 1.0s | 1.5s | 0.9s | 1 | qwen3.5:4b: 3.1 GB VRAM de 3.1 GB; embeddinggemma:latest: 0.0 GB VRAM de 0.6 GB |
| qwen3.5:9b | **21/28** | 2.1s | 3.2s | 1.8s | 9 | qwen3.5:9b: 5.1 GB VRAM de 5.8 GB; embeddinggemma:latest: 0.0 GB VRAM de 0.6 GB |
| qwen3.5:4b (pensando) | **27/28** | 2.7s | 4.0s | 2.6s | 1 | qwen3.5:4b: 3.1 GB VRAM de 3.1 GB; embeddinggemma:latest: 0.0 GB VRAM de 0.6 GB |

## Por categoria

| Categoria | qwen3.5:4b | qwen3.5:9b | qwen3.5:4b (pensando) |
|---|---|---|---|
| agenda | 5/6 | 5/6 | 5/6 |
| agenda-escrita | 6/6 | 3/6 | 6/6 |
| confirmacao | 2/2 | 0/2 | 2/2 |
| conversa | 3/3 | 3/3 | 3/3 |
| financas | 4/4 | 4/4 | 4/4 |
| memoria | 4/4 | 3/4 | 4/4 |
| seguranca | 1/1 | 1/1 | 1/1 |
| tarefas | 2/2 | 2/2 | 2/2 |

## Caso a caso

| Caso | qwen3.5:4b | qwen3.5:9b | qwen3.5:4b (pensando) |
|---|---|---|---|
| `agenda_hoje` | ✅ 1.7s | ✅ 3.2s | ✅ 4.7s |
| `agenda_amanha` | ✅ 1.3s | ✅ 2.2s | ✅ 3.8s |
| `agenda_semana` | ✅ 1.5s | ❌ 1.1s | ✅ 4.2s |
| `agenda_prova` | ❌ 1.0s | ✅ 1.7s | ❌ 2.6s |
| `agenda_feriado` | ✅ 1.3s | ✅ 2.9s | ✅ 3.8s |
| `agenda_livre` | ✅ 1.1s | ✅ 9.7s | ✅ 2.6s |
| `agenda_criar_sexta` | ✅ 1.0s | ✅ 2.4s | ✅ 3.8s |
| `agenda_criar_intervalo` | ✅ 1.2s | ❌ 1.7s | ✅ 2.4s |
| `agenda_criar_data_absoluta` | ✅ 1.0s | ✅ 1.7s | ✅ 3.1s |
| `agenda_apagar` | ✅ 1.1s | ❌ 2.3s | ✅ 2.7s |
| `agenda_alterar` | ✅ 1.5s | ❌ 3.3s | ✅ 3.1s |
| `agenda_sem_data` | ✅ 2.2s | ✅ 1.8s | ✅ 4.0s |
| `agenda_apagar_tudo` | ✅ 1.8s | ✅ 5.2s | ✅ 3.5s |
| `confirma_sim` | ✅ 0.0s | ❌ 1.6s | ✅ 0.0s |
| `confirma_correcao` | ✅ 0.8s | ❌ 1.0s | ✅ 2.3s |
| `financas_mes` | ✅ 1.2s | ✅ 2.1s | ✅ 3.0s |
| `financas_ifood` | ✅ 0.9s | ✅ 2.0s | ✅ 3.1s |
| `financas_lancar` | ✅ 1.1s | ✅ 1.9s | ✅ 2.5s |
| `financas_uber` | ✅ 0.7s | ✅ 1.1s | ✅ 1.3s |
| `tarefas_listar` | ✅ 0.9s | ✅ 1.7s | ✅ 1.9s |
| `tarefas_criar` | ✅ 0.9s | ✅ 2.5s | ✅ 2.5s |
| `memoria_guardar` | ✅ 1.0s | ❌ 2.8s | ✅ 2.8s |
| `memoria_usar` | ✅ 1.0s | ✅ 0.9s | ✅ 1.8s |
| `memoria_cachorra` | ✅ 0.9s | ✅ 0.5s | ✅ 2.4s |
| `memoria_esquecer` | ✅ 0.7s | ✅ 1.0s | ✅ 1.4s |
| `conversa_oi` | ✅ 0.4s | ✅ 0.6s | ✅ 1.2s |
| `conversa_conta` | ✅ 0.4s | ✅ 0.5s | ✅ 1.6s |
| `conversa_piada` | ✅ 0.4s | ✅ 0.7s | ✅ 1.2s |

## Onde errou

### qwen3.5:4b — 1 erro(s)
- **agenda_prova** · "Quando é minha prova de física?"
  - falhas: resposta sem nenhum de ['8 de', '08/', 'dia 8']: 'Não encontrei nenhuma prova de física na sua agenda nos próximos 60 dias. Quer que eu proc'
  - ferramentas: [('agenda_buscar', {'texto': 'prova física'}, True)]
  - resposta: 'Não encontrei nenhuma prova de física na sua agenda nos próximos 60 dias. Quer que eu procure em um período diferente?'

### qwen3.5:9b — 7 erro(s)
- **agenda_semana** · "Como tá minha semana?"
  - falhas: esperava agenda_listar; chamou nenhuma
  - ferramentas: []
  - resposta: 'Vou verificar sua agenda da semana que vem.'
- **agenda_criar_intervalo** · "Coloca na agenda academia amanhã das 7 às 8 da manhã"
  - falhas: esperava agenda_criar; chamou nenhuma; devia pedir confirmação
  - ferramentas: []
  - resposta: 'Vou colocar na sua agenda academia amanhã das 7h às 8h da manhã.'
- **agenda_apagar** · "Cancela o dentista de amanhã"
  - falhas: esperava agenda_apagar; chamou agenda_buscar; devia pedir confirmação
  - ferramentas: [('agenda_buscar', {'texto': 'dentista', 'data_inicio': '2026-10-01', 'data_fim': '2026-10-01'}, True)]
  - resposta: 'Certo, Felipe. Já cancelo o dentista de amanhã às 14h.'
- **agenda_alterar** · "Muda o treino de hoje para as 18h"
  - falhas: esperava agenda_alterar; chamou agenda_listar; devia pedir confirmação
  - ferramentas: [('agenda_listar', {'data_inicio': '2026-09-30', 'data_fim': '2026-09-30'}, True)]
  - resposta: 'Encontrei o seu treino hoje das 17h30 às 18h30. Vou alterar para as 18h agora. Preciso saber até que hora ele vai durar, ou se você quer manter a mesma duração de uma hora?'
- **confirma_sim** · "sim"
  - falhas: devia pedir confirmação; não criou depois do sim
  - ferramentas: [('agenda_criar', {'titulo': 'Barbeiro', 'data': '2026-10-03', 'hora_inicio': '10:00'}, True)]
  - resposta: "Vou criar 'Barbeiro' sáb 03/10 das 10:00 às 11:00. Confirma?"
- **confirma_correcao** · "não, às 11"
  - falhas: devia pedir confirmação
  - ferramentas: []
  - resposta: "Vou criar 'Barbeiro' sáb 03/10 das 11:00 às 12:00. Confirma?"
- **memoria_guardar** · "Lembra que minha cachorra se chama Luna"
  - falhas: esperava guardar_memoria; chamou buscar_memoria; não guardou
  - ferramentas: [('buscar_memoria', {'consulta': 'cachorra Luna'}, True)]
  - resposta: 'Não encontrei nada registrado sobre a sua cachorra Luna na memória ainda, Felipe. Se quiser que eu guarde esse fato ou alguma informação sobre ela, é só falar!'

### qwen3.5:4b (pensando) — 1 erro(s)
- **agenda_prova** · "Quando é minha prova de física?"
  - falhas: resposta sem nenhum de ['8 de', '08/', 'dia 8']: 'Não encontrei nenhuma prova de física na sua agenda nos próximos 60 dias. Quer que eu busq'
  - ferramentas: [('agenda_buscar', {'texto': 'prova física', 'data_inicio': '2026-09-30', 'data_fim': '2026-11-28'}, True)]
  - resposta: 'Não encontrei nenhuma prova de física na sua agenda nos próximos 60 dias. Quer que eu busque em outro período ou por outra palavra-chave?'
