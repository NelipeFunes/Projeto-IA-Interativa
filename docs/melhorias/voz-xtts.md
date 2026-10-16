# Voz clonada com o XTTS: o que medimos (16/10/2026)

**Queixa:** a voz do Piper era "robótica"; o XTTS clonado, "lenta e travada". Em vez de decidir no ouvido, a noite
foi gasta em medir, com uma voz de referência em português (30 s de fala limpa), o que dava essa impressão.
Tudo roda no PC; as gravações e os áudios de teste ficam fora do repositório.

## Como se mede "lenta e travada"

| Métrica | O que é |
|---|---|
| letras/s | letras faladas por segundo de áudio útil (sem o silêncio das pontas) |
| pausa % | parte do áudio útil em silêncio de 150 ms ou mais **dentro** da fala |
| erro | erro de palavras do Parakeet (o do Vision) ouvindo o áudio; só mede se é entendido |
| timbre | semelhança (0 a 1) com a voz de referência, pelo codificador de voz do próprio XTTS |
| RTF | segundos de fala por segundo de cálculo; abaixo de 1 não acompanha a fala |
| paradas | simula o alto-falante tocando sem parar: quanto tempo o som acabou antes de o próximo pedaço chegar |

**O alvo** (a própria voz de referência): **16 letras/s**, ~0% de pausa dentro da frase, ~0,25 s entre frases.
Cada ajuste foi testado com 4 sorteios × 5 frases (o XTTS sorteia: a mesma frase deu 11,9 s e 15,7 s com a mesma
configuração, então um áudio só por ajuste não prova nada), com as mesmas sementes em todos.

## O que achamos, na ordem

1. **O XTTS usava só os primeiros 6 s da referência** (`gpt_cond_len=6`). Com a referência inteira (30 s) o timbre é 0,68;
   com só 13 s da mesma gravação cai para 0,62, e fatiar a referência em pedaços de 3 s também piora (0,65).
2. **Falava "ponto" no fim da frase curta:** 32 de 55 sorteios (58%). Sem o ponto final, 0 de 10. Por isso
   `preparar_texto` tira o `.` do fim (mantém `?`, `!` e reticências).
3. **Referência em inglês, fala em português = voz lenta:** nas primeiras amostras, 6 a 7 letras/s e uma pausa de 3,8 s
   no meio de uma frase; na rodada medida (referência sintética em inglês), 10,2 letras/s, 20% de pausa, pausa máxima de
   3,6 s e timbre 0,37 em relação à voz de referência. Com uma referência em português: ~11 letras/s e timbre 0,70.
4. **A lentidão restante são as pausas, não a fala.** Sem as pausas, o ritmo já era 14,7 letras/s (alvo: 16 a 17). Mas
   22% do tempo era pausa, mediana de 0,54 s, de uma a três por frase, quase tudo em vírgula, dois-pontos e ponto.
5. **O que NÃO funcionou:** proibir o modelo de gerar mais de N códigos de silêncio seguidos (22,9% → 20,5% de pausa
   no melhor caso). Proibidos os códigos que identificamos, ele escolhe outros quase mudos e a pausa continua. Temperatura
   menor ou maior, penalidade de repetição menor e TF32/fp16 também não ajudaram (o fp16 até piorou: RTF 1,46 → 1,29).
6. **O que funcionou:** (a) tirar vírgulas, ponto e vírgula e dois-pontos do texto (fora de números): pausa de 22,9% para
   12,5% a 14,3%; (b) cortar no áudio, em tempo real, qualquer pausa acima de 0,15 s (`CompressorDePausas`), deixando
   0,25 s entre as frases; (c) velocidade 1,1.

## Resultado (XTTS, mesma referência de 30 s, 20 áudios por linha)

| Configuração | letras/s | pausa % | erro | timbre | RTF | paradas |
|---|---|---|---|---|---|---|
| alvo (a voz de referência) | 16,1 | ~0 | | 1,0 | | |
| XTTS puro (só tirando o ponto final) | 11,4 | 22,1 | 0,060 | 0,70 | 1,42 | 0 |
| + pausa máxima 0,15 s, com vírgulas | 13,4 | 10,0 | 0,071 | 0,70 | 1,23 | 0,07 s (8 de 20) |
| + sem vírgulas | 14,4 | 6,4 | 0,072 | 0,69 | 1,31 | 0,01 s (1 de 20) |
| + velocidade 1,1 (**padrão do config**) | **16,0** | 6,5 | 0,096 | 0,68 | 1,19 | 0,02 s (3 de 20) |

(a pausa residual de ~6% inclui os 0,25 s entre frases, que são desejados)

**Custo:** o corte de pausa encurta o áudio, mas não o cálculo, então o RTF cai de 1,4 para ~1,2. Acompanha a fala, com
pouca folga (as paradas simuladas ficam em 0,02 s em média).

## O gargalo é a CPU, não a placa

Durante a geração a placa fica em **26%** de uso: cada passo do XTTS lança centenas de operações pequenas e quem
limita é a CPU (i7 de 2018, e no Windows o lançamento é mais lento). Por isso nada que acelera conta (TF32, fp16)
adianta. Para ganhar velocidade de verdade: gerar em outro processo (sem a disputa do GIL com o microfone e o resto do
núcleo), CUDA graphs, ou outro motor.

## Desafiante: Qwen3-TTS 0.6B (Apache-2.0), mesmas condições

Com a mesma referência (13 s e a transcrição dela), uma frase por vez, o mesmo preparo de texto e o mesmo compressor:

| Configuração | letras/s | pausa % | erro | timbre | RTF | paradas |
|---|---|---|---|---|---|---|
| com vírgulas, sem compressor | 14,2 | 10,0 | 0,087 | 0,75 | 2,02 | 0 |
| com vírgulas + pausa máxima 0,18 s | 15,3 | 2,5 | 0,097 | 0,74 | 1,81 | 0 |
| sem vírgulas + pausa máxima 0,18 s | **16,0** | 1,2 | 0,102 | 0,73 | 1,84 | 0 |

Ele empata com o XTTS no ritmo (16,0, sem precisar acelerar) e ganha em timbre, pausas, RTF e paradas (usa CUDA graphs);
perde um pouco no erro de palavras.
**Mas** exige `transformers` 5.15.1, e o XTTS exige `transformers` menor que 5: os dois não convivem no mesmo ambiente
(as versões 5.18 e 5.20 não carregam o modelo). Entrar no Vision pediria um processo separado (T-033). Qual soa melhor
é decisão de ouvido.

## Com o modelo de linguagem escrevendo ao mesmo tempo

No Vision a voz começa na 1ª frase enquanto o modelo ainda escreve o resto, e os dois dividem a placa. **Pior caso, com o
Ollama gerando sem parar:** o XTTS cai de RTF 1,19 para 0,58 e o Qwen3-TTS de 2,01 para 0,58 (18 s de silêncio em 6 frases
no XTTS). Mas esse teste exagera: no uso real o modelo termina o resto da resposta em 1 a 4 s. Medido no fluxo de verdade
(o Ollama escrevendo em streaming, cada frase pronta indo para a voz de produção, sem alto-falante):

| Resposta | Política | 1º som | Paradas |
|---|---|---|---|
| 1 a 8 frases | falar já na 1ª frase (como hoje) | 0,5 a 0,8 s | 0 a 0,1 s (1 em 8 execuções) |
| 1 a 8 frases | esperar o modelo terminar | 0,6 a 1,7 s | 0 |
| 15 a 25 frases (até 276 tokens) | falar já na 1ª frase | 0,6 a 0,7 s | ≤ 0,2 s (uma parada em 3 de 4 execuções) |
| 15 a 25 frases | esperar o modelo terminar | 2,3 a 3,5 s | 0 |

Conclusão: **manter a fala por frase em streaming**; esperar o fim só atrasa. O risco que sobra é uma resposta longa em
que a 1ª frase seja muito curta (pouca folga de áudio antes de o modelo disputar a placa).

## O que entrou no código

- `vision/voice/pausas.py`: `preparar_texto` e `CompressorDePausas` (em tempo real, com estado entre os pedaços).
- `vision/voice/tts.py`: `VozXTTS` aceita `referencia` (clona uma gravação em `data/voz/<nome>.wav`), `idioma`,
  `pausa_max_s`, `tirar_virgulas`, `ganho` e `velocidade_base`; `carregar_voz` lê as chaves `voz.*` novas. O XTTS saía mais
  alto que o Piper (RMS 0,15 contra 0,08, picos de 0,97): agora passa por um ganho fixo.
- Voz clonada nunca troca para o Piper numa resposta curta (`loop.py`): seria outra voz.
- `config.py`: `voz.motor` e `voz.referencia` podem ficar em `data/config-local.yaml` (validados: só o nome do arquivo).

## Limites

- A medida de timbre é do próprio XTTS e mede semelhança, não naturalidade. **Se a voz soa humana é decisão de ouvido.**
- A voz de referência é o teto: o clone não passa dela. Quanto mais longa e limpa, melhor (30 s, no idioma da fala).
- 4 sorteios por ajuste: diferenças pequenas de erro de palavras (0,06 contra 0,08) estão no ruído.
- Sem ouvir: o que a "voz sem vírgulas" perde de entonação em frases com listas só o ouvido diz; por isso
  `voz.xtts_tirar_virgulas` é uma chave e não uma regra.
