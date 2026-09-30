# Avaliação do Jarvis

Gerado em 30/09/2026 01:37 · 28 casos · canal voz · agenda e Orbit falsos · memória com embeddinggemma real

## Resumo

| Modelo | Acertos | Tempo médio | p90 | 1ª palavra (média) | Insistências | VRAM |
|---|---|---|---|---|---|---|
| qwen3.5:4b | **25/28** | 1.0s | 1.5s | 0.9s | 1 | qwen3.5:4b: 3.1 GB VRAM de 3.1 GB; embeddinggemma:latest: 0.0 GB VRAM de 0.6 GB |

## Por categoria

| Categoria | qwen3.5:4b |
|---|---|
| agenda | 6/6 |
| agenda-escrita | 5/6 |
| confirmacao | 1/2 |
| conversa | 3/3 |
| financas | 4/4 |
| memoria | 4/4 |
| seguranca | 1/1 |
| tarefas | 1/2 |

## Caso a caso

| Caso | qwen3.5:4b |
|---|---|
| `agenda_hoje` | ✅ 1.7s |
| `agenda_amanha` | ✅ 1.2s |
| `agenda_semana` | ✅ 1.2s |
| `agenda_prova` | ✅ 1.0s |
| `agenda_feriado` | ✅ 1.4s |
| `agenda_livre` | ✅ 1.3s |
| `agenda_criar_sexta` | ✅ 1.0s |
| `agenda_criar_intervalo` | ✅ 1.2s |
| `agenda_criar_data_absoluta` | ✅ 1.0s |
| `agenda_apagar` | ❌ 1.4s |
| `agenda_alterar` | ✅ 1.5s |
| `agenda_sem_data` | ✅ 1.8s |
| `agenda_apagar_tudo` | ✅ 1.8s |
| `confirma_sim` | ✅ 0.0s |
| `confirma_correcao` | ❌ 0.9s |
| `financas_mes` | ✅ 1.0s |
| `financas_ifood` | ✅ 0.9s |
| `financas_lancar` | ✅ 1.1s |
| `financas_uber` | ✅ 0.7s |
| `tarefas_listar` | ✅ 0.8s |
| `tarefas_criar` | ❌ 1.3s |
| `memoria_guardar` | ✅ 1.0s |
| `memoria_usar` | ✅ 1.0s |
| `memoria_cachorra` | ✅ 0.9s |
| `memoria_esquecer` | ✅ 0.7s |
| `conversa_oi` | ✅ 0.5s |
| `conversa_conta` | ✅ 0.3s |
| `conversa_piada` | ✅ 0.5s |

## Onde errou

### qwen3.5:4b — 3 erro(s)
- **agenda_apagar** · "Cancela o dentista de amanhã"
  - falhas: agenda_apagar.evento_id=None, esperava 'ev_dentista'; devia pedir confirmação
  - ferramentas: [('agenda_listar', {'data_inicio': '2026-10-01'}, True), ('agenda_apagar', {'event_id': 'ev_dentista'}, False)]
  - resposta: 'Ops, não consegui apagar. Vou tentar de novo com o mesmo ID.'
- **confirma_correcao** · "não, às 11"
  - falhas: devia pedir confirmação
  - ferramentas: [('agenda_alterar', {'evento_id': '1', 'hora_inicio': '11:00'}, False)]
  - resposta: 'Ops, não encontrei o evento criado. Vou tentar criar de novo com a hora correta.'
- **tarefas_criar** · "Me lembra de renovar a CNH até dia 20 de outubro"
  - falhas: esperava tarefas_criar ou agenda_criar; chamou lembrar; devia pedir confirmação
  - ferramentas: [('lembrar', {'fato': 'O Felipe precisa renovar a CNH até dia 20 de outubro', 'categoria': 'meta'}, True)]
  - resposta: 'Já anotei que você precisa renovar a CNH até dia 20 de outubro. Quer que eu te lembre com mais antecedência?'
