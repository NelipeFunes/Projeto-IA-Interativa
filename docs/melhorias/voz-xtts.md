# Voz clonada com o XTTS: o que medimos (16/10/2026)

**Queixa:** a voz do Piper era "robótica"; o XTTS clonado, "lenta e travada". Em vez de decidir no ouvido, a noite
foi gasta em medir, com uma voz de referência em português (30 s de fala limpa), o que dava essa impressão.
Tudo roda no PC; as gravações e os áudios de teste ficam fora do repositório (os dados brutos também).

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

1. **Referência em inglês, fala em português = voz lenta:** nas primeiras amostras, 6 a 7 letras/s e uma pausa de 3,8 s
   no meio de uma frase; na rodada medida (referência sintética em inglês), 10,2 letras/s, 20% de pausa, pausa máxima de
   3,6 s e timbre 0,37 em relação à voz de referência. Com uma referência em português: ~11 letras/s e timbre 0,70.
2. **A lentidão restante são as pausas, não a fala.** Sem as pausas, o ritmo já era 14,7 letras/s (alvo: 16 a 17). Mas
   22% do tempo era pausa, mediana de 0,54 s, de uma a três por frase, quase tudo em vírgula, dois-pontos e ponto.
3. **Falava a palavra "ponto" no fim da frase.** Com uma referência em inglês, em 30 de 55 sorteios (no fim); com a
   referência em português, em 4 de 20 (8 de 20 em qualquer lugar da frase), com erro de palavras 0,107 e 3 falhas.
   Sem o ponto final: 0 de 20 e erro 0,076. Por isso `preparar_texto` tira o `.` do fim (mantém `?`, `!` e reticências).
   O ponto de abreviação fica ("Dra.": o XTTS só expande para "doutora" com ele; testado, 4 de 4 saíram "doutora").
4. **Quanto da referência o XTTS usa** (o padrão dele é 6 s): com a mesma gravação, 6 s e 30 s dão o mesmo timbre (0,672
   contra 0,667) e o ritmo muda pouco (14,5 contra 15,2 letras/s). Já uma gravação de 30 s dá timbre melhor que uma de
   13 s (0,68 contra 0,62, rodada 8). Mantive 30 s; o que importa é a gravação ser longa e limpa, não o parâmetro.
5. **O que NÃO funcionou:** proibir o modelo de gerar mais de N códigos de silêncio seguidos (22,9% → 20,5% de pausa
   no melhor caso): proibidos os códigos que identificamos, ele escolhe outros quase mudos e a pausa continua. Temperatura
   diferente da padrão, penalidade de repetição menor e TF32/fp16/bf16 também não ajudaram (RTF 1,47 no fp32, 1,46 no
   TF32, 1,29 no fp16 e 1,31 no bf16).
6. **O que funcionou:** cortar no áudio, em tempo real, qualquer pausa acima de 0,15 s (`CompressorDePausas`), deixando
   0,25 s entre as frases, e a velocidade 1,1. Tirar as vírgulas do texto (`xtts_tirar_virgulas`) derrubou a pausa de
   22,9% para 12,5% a 14,3% antes do compressor, mas **com o compressor rende só +0,3 a +0,5 letras/s** e traz risco
   ("Não, está tudo certo" sem a vírgula vira "Não está tudo certo"; por isso a vírgula da 1ª palavra fica). Ficou desligado.

## Resultado (XTTS, mesma referência de 30 s, 20 áudios por linha, código final)

| Configuração | letras/s | pausa % | erro | timbre | RTF | paradas |
|---|---|---|---|---|---|---|
| alvo (a voz de referência) | 16,1 | ~0 | | 1,0 | | |
| XTTS puro (só tirando o ponto final) | 11,4 | 22,1 | 0,060 | 0,70 | 1,45 | 0 |
| + pausa máxima 0,15 s | 13,4 | 10,0 | 0,071 | 0,70 | 1,24 | 0,07 s (8 de 20) |
| + velocidade 1,1 (**padrão do config**) | 14,7 | 10,7 | 0,076 | 0,66 | 1,13 | 0,10 s (9 de 20) |
| sem vírgulas + velocidade 1,1 | 15,2 | 9,2 | 0,081 | 0,67 | 1,15 | 0,11 s (10 de 20) |

(a pausa residual de ~10% inclui os 0,25 s entre frases, que são desejados; "paradas" é a média por áudio e quantos
tiveram alguma, de 0,05 s para cima)

**Custo:** o corte de pausa encurta o áudio, mas não o cálculo, então o RTF cai de 1,45 para ~1,15 e sobra pouca folga:
metade dos áudios tem uma parada de ~0,1 s. Mais velocidade (1,2) pioraria isso; menos (1,0) devolve folga (RTF 1,24) e
custa ~1 letra/s. Os números variam um pouco de uma rodada para outra (a 1ª medida dos mesmos ajustes teve menos
paradas), então leia a ordem de grandeza.

## A placa fica quase parada durante a geração

Durante a geração a placa fica em **26%** de uso (`rodadas/velocidade.json`). Isso **indica** que quem limita é a CPU
(cada passo do XTTS lança centenas de operações pequenas; um i7 de 2018, no Windows), e combina com TF32 e fp16 não
ajudarem. Para ganhar velocidade de verdade, o caminho provável é outro processo (sem a disputa do GIL com o microfone e
o resto do núcleo), CUDA graphs ou outro motor; nenhum dos três foi medido para o XTTS aqui.

## Desafiante: Qwen3-TTS 0.6B (Apache-2.0), agora motor opcional

Medido **como roda no Vision** (`VozQwen`, processo real, gravação nivelada, configuração padrão do `config.yaml`), com a
gravação que ele pede (13 s e a transcrição dela; o XTTS usa a de 30 s, e com 13 s ele fica em timbre 0,62), 20 áudios por
linha. O XTTS é a linha do padrão dele, para comparar:

| Motor e configuração | letras/s | pausa % | erro | timbre | RTF | paradas |
|---|---|---|---|---|---|---|
| Qwen3-TTS, padrão (vírgulas mantidas) | **15,2** | 6,7 | 0,095 | **0,76** | **1,96** | **0 de 20** |
| Qwen3-TTS, sem vírgulas | 15,1 | 4,8 | 0,096 | 0,75 | 2,02 | 0 de 20 |
| XTTS, padrão (velocidade 1,1) | 14,7 | 10,7 | 0,076 | 0,66 | 1,13 | 9 de 20 |

Ele ganha em ritmo (sem acelerar), pausas, timbre, RTF e paradas (provavelmente pelos CUDA graphs; não foi medido sem
eles) e perde um pouco no erro de palavras. As paradas são sem o modelo de linguagem gerando ao mesmo tempo (veja a
seção seguinte).
O 1º som sai em ~0,42 s, igual ao do XTTS. **Mas** exige `transformers` 5.15.1, e o XTTS exige `transformers` menor que 5:
os dois não convivem no mesmo ambiente (as versões 5.18 e 5.20 não carregam o modelo). Por isso entrou como motor
**opcional, num processo separado** (`voz.motor: qwen3`, PR seguinte ao do XTTS). Qual soa melhor é decisão de ouvido.

### Como ficou (`vision/voice/tts_qwen.py` + `qwen_worker.py`, ambiente `servidor-voz/`)

- O Vision sobe um processo com o Python do ambiente `servidor-voz/` (`uv sync --project servidor-voz`, versões fixas e
  lock; criado do zero a partir do lock funcionou; documentado com `--locked`) e fala com ele **só por pipes**
  (stdin/stdout): sem rede, sem porta. O processo acaba sozinho se o Vision morrer.
- O processo recebe só o básico do Windows (nada de senha do Orbit nem chave da Tavily, como já era para os servidores MCP),
  sem rede (`HF_HUB_OFFLINE`) e sem procurar programa na pasta atual (o pacote `sox` roda `cmd /c sox` ao importar). O
  stdout é só o protocolo (o descritor 1 aponta para o stderr no processo, então `print`, código nativo e processos
  filhos não estragam o fluxo); o Vision recusa quadro de tipo desconhecido, tamanho acima de 4 MB ou áudio fora de float32.
- Mesma interface do `VozXTTS`; o texto, o ganho e o `CompressorDePausas` rodam no Vision, iguais aos do XTTS.
- **Modo jogo:** o processo é encerrado e a placa volta inteira (liberou ~4 GB; o XTTS só consegue mover o modelo para
  a RAM). Voltar leva 12 a 13 s (14 a 22 s na partida do Vision, com o modelo ainda fora do cache do Windows). **Subir o
  processo nunca segura a fala**: depois de uma queda ou do jogo, o que for falado nesse tempo sai pela reserva (Piper) e
  a voz volta sozinha quando o processo fica pronto.
- **Volume:** o Qwen3-TTS fala no volume da gravação que clona (gravação com RMS 0,02 dava voz baixa). O processo nivela a
  gravação para RMS 0,08 ao carregar (o mesmo do Piper); a fala saiu com RMS médio 0,075. A cópia nivelada fica ao lado
  da gravação, em `data/voz/` (a mesma proteção: fora do git), e é sobrescrita a cada partida.
- Falhas: erro num pedido volta como `ErroDaVoz` e o laço fala o resto com o Piper; processo que cai reinicia no pedido
  seguinte, com 30 s de espera para não reiniciar a cada frase; cancelar no meio da frase leva ~0,3 s.
- Pede a gravação (3 a 15 s; 13 s funcionou bem) e, ao lado, a **transcrição exata** dela em `data/voz/<nome>.txt`.
- Sobe em 14 a 22 s. Com o Ollama gerando ao mesmo tempo ele cai para RTF 0,58, como o XTTS (a disputa pela placa não é
  de um motor só).

**Limite:** numa frase com valor em dinheiro ("R$ 1.234,56") o reconhecedor ouviu "R$ 134,56" no Qwen3 (no XTTS saiu
certo): números de 4 e 5 dígitos em dígito. Corrigido para valores em reais no PR #69 (por extenso); o resto é a T-036.

## Com o modelo de linguagem escrevendo ao mesmo tempo

No Vision a voz começa na 1ª frase enquanto o modelo ainda escreve o resto, e os dois dividem a placa. **Pior caso, com o
Ollama gerando sem parar:** o XTTS cai de RTF 1,19 para 0,58 e o Qwen3-TTS de 2,01 para 0,58 (18 s de silêncio em 6 frases
no XTTS). Mas esse teste exagera: no uso real o modelo termina o resto da resposta em 1 a 4 s. Medido no fluxo de verdade
(o Ollama escrevendo em streaming, cada frase pronta indo para a voz de produção, sem alto-falante; 2 execuções por linha,
com a configuração de então):

| Resposta pedida | Política | 1º som | Paradas |
|---|---|---|---|
| 1 a 8 frases | falar já na 1ª frase (como hoje) | 0,50 a 0,76 s | 0 em 7 de 8 execuções; uma de 0,1 s |
| 1 a 8 frases | esperar o modelo terminar | 0,63 a 1,67 s | 0 |
| 15 e 25 frases (saíram 12 a 22) | falar já na 1ª frase | 0,62 a 0,73 s | 0,07 a 0,19 s em 3 de 4 execuções |
| 15 e 25 frases (saíram 13 a 50) | esperar o modelo terminar | 2,17 a 3,81 s | 0 (uma de 0,1 s) |

Conclusão: **manter a fala por frase em streaming**; esperar o fim só atrasa. O risco que sobra é uma resposta longa em
que a 1ª frase seja muito curta (pouca folga de áudio antes de o modelo disputar a placa).

## O que entrou no código

- `vision/voice/pausas.py`: `preparar_texto` (ponto final fora, menos o de abreviação; vírgulas fora só se pedido, menos a
  da 1ª palavra e a de número) e `CompressorDePausas` (em tempo real, com estado entre os pedaços; o silêncio do fim da
  frase é o maior entre `fim_s` e `pausa_max_s`).
- `vision/voice/tts.py`: `VozXTTS` aceita `referencia` (clona uma gravação em `data/voz/<nome>.wav`, com ou sem a extensão
  no nome), `idioma`, `pausa_max_s`, `tirar_virgulas`, `ganho` e `velocidade_base`; `carregar_voz` lê as chaves `voz.*` novas.
  O XTTS saía mais alto que o Piper (RMS 0,15 contra 0,08, picos de 0,97): agora passa por um ganho fixo, e o limiar do
  compressor acompanha o ganho (com um limiar fixo, ganho alto fazia o chiado contar como fala).
- Voz clonada nunca troca para o Piper numa resposta curta (`loop.py`): seria outra voz.
- `config.py`: `voz.motor` e `voz.referencia` podem ficar em `data/config-local.yaml` (validados: só o nome do arquivo).

## Limites

- A medida de timbre é do próprio XTTS e mede semelhança, não naturalidade. **Se a voz soa humana é decisão de ouvido.**
- A voz de referência é o teto: o clone não passa dela. Quanto mais longa e limpa, melhor (30 s, no idioma da fala).
- 4 sorteios por ajuste: diferenças pequenas de erro de palavras (0,06 contra 0,08) estão no ruído.
- `xtts_tirar_virgulas` e `xtts_velocidade` ficam como chaves porque dependem do gosto de ouvido e do texto.
- O que `para_fala` faz com reticências ("..." vira "..") antes do XTTS não foi medido.
