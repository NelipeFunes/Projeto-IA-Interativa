# Lições aprendidas (para não repetir)

Cada item tem o que aconteceu, o que custou e a regra que sobrou. Atualize quando um erro novo ensinar algo.

## Desempenho e modelo

1. **Medir como o assistente roda de verdade, não como é fácil de testar.** A avaliação antiga registrava só 1/3 das
   ferramentas (agenda, memória, Orbit) e rodava todos os casos no mesmo minuto. O prompt de produção tem ~7.800 tokens
   (53 ferramentas) e a avaliação via ~2.500; o relógio parado deixava o cache do modelo sempre quente. Resultado:
   "1,0 s por resposta" no relatório, 2,2 a 3,6 s no uso real.
   *Regra:* a avaliação usa o conjunto completo de ferramentas e um relógio que avança entre as falas (`evals/ambiente.py`,
   `--avanco-min`). Quem muda o prompt roda `evals/run.py --suite todas` (e, na T-026, a conversa de 12 pedidos com 7 min de intervalo) antes e depois.
2. **Nada que varie por pedido (hora com minutos, memórias, estilo do canal) no prompt de sistema.** O Ollama reaproveita
   o início do prompt entre pedidos. *Medido:* mudar só o fim do prompt custa ~0,3 s de preparo; mudar o meio (a hora com
   minutos, no sistema) custa ~1,8 s, em TODO pedido. *Hipótese (não verificada no Ollama):* num modelo híbrido como o
   Qwen3.5 o reaproveitamento só recomeça de um ponto de controle ~1.000 tokens antes do fim do prompt anterior.
   *Regra:* o que varia vai numa mensagem de sistema curta logo antes da fala (`prompt.contexto_do_pedido`), nunca no
   sistema fixo; `tests/test_prompt_estavel.py` segura.
3. **O tamanho do prompt tem teto.** Cada ferramenta nova engorda todo pedido (3,6 mil tokens/s de preparo: 1.000 tokens
   = 0,28 s no pedido frio). `tests/test_orcamento_prompt.py` falha quando os esquemas passam de 25 mil caracteres ou o
   sistema de 6 mil, e exige a contagem exata de ferramentas. Para subir o teto, suba de propósito, no mesmo PR.
4. **Hipótese sobre o Ollama é hipótese até medir.** "As ferramentas vêm depois do system" foi dito por duas fontes e
   não ficou provado; o que se mediu foi o *ponto de divergência* do prompt. Documente o medido, não o suposto.
5. **Modelo maior não é automaticamente melhor para voz.** Em 30/09 o 9B acertou menos (48/56) e levou o dobro do tempo
   do 4B (56/56). Modelo novo só entra com a avaliação de produção.
6. **O modelo no HD (D:) leva 4 a 29 s para carregar.** `manter_carregado: "-1m"` existe por isso; o modo jogo descarrega.

## Código e processo

7. **Nunca grave arquivo novo direto na pasta principal.** Um `Write` caiu em `C:\Users\User\vision\evals\` em vez de
   no worktree. Antes de criar arquivo, confira o diretório; trabalho novo nasce em worktree.
8. **No Windows, `Path.write_text()` sem `encoding="utf-8"` grava em cp1252.** Um teste ficou ilegível e só foi percebido
   na coleta do pytest. *Regra:* sempre `encoding="utf-8"` ao escrever; scripts de edição em arquivo, não em heredoc com
   `\n` (a barra invertida vira quebra de linha real no meio do texto).
9. **Merge da `main` numa branch antiga traz arquivos para o diff que o scanner olha.** O GitGuardian reprovou o PR #37
   por "senhas" que eram valores de teste (`"s3nha"`) de um arquivo da `main`. *Regra:* em teste, monte valores com cara de
   senha em partes (`"-".join([...])`) ou refaça o PR em um commit só sobre a `main` limpa; nunca ignore o check.
10. **Ambiente "falso" de avaliação precisa ser provado isolado.** O primeiro `ambiente.py` ainda travava a tela, fechava o
    Chrome e apertava a tecla de mídia de verdade, e a avaliação rodava de madrugada na máquina dele. *Regra:*
    `tests/test_avaliacao.py` troca `ctypes.windll`, `subprocess`, `socket` etc. por funções que levantam e roda as
    ferramentas de ação; nada de ação real sem esse teste verde.
11. **Reviewer sênior encontra o que o autor não vê.** Todas as revisões do Opus nesta análise acharam algo real (o ambiente
    agindo no PC; o relógio voltando ao horário real; a memória no papel de "user"). Rode a revisão antes de medir, não depois.

## Segurança e robustez

12. **Processo filho não herda o ambiente inteiro.** O Node de terceiros do Google Agenda recebia ORBIT_PASSWORD e
    TAVILY_API_KEY. Agora só o básico + o `env:` do config daquele servidor (`$NOME` copia a variável do Vision).
    *Regra:* servidor novo declara o que precisa em `env:`; nada de `dict(os.environ)`.
13. **`ipaddress.is_private` não é "rede local".** Aceita 255.255.255.255, 240.x, 198.18.x e 192.0.2.x. Lista explícita de
    redes (`tv.REDES_LOCAIS`).
14. **Tarefa solta precisa de referência e de log.** `asyncio.ensure_future` sem guardar a tarefa pode ser coletada, e
    exceção de tarefa que ninguém aguarda some. Use `Nucleo._agendar` (ou guarde e adicione `done_callback` que loga).
15. **Todo log e todo histórico tem retenção.** `janela.log` e `mcp-*.log` cresciam sem limite; as conversas guardavam tudo
    para sempre. `arquivos.limitar_log` e `arquivos.podar_datados` (`conversa.guardar_dias`).
16. **Credencial do usuário nunca vai em teste, log ou doc público.** Os identificadores do backend da piscina ficam em
    `data/piscina/app.json`; o cliente só fala com a API da AWS e o Cognito da mesma região, sem seguir redirecionamento.

## Cache

17. **Cache só vale com validade, tamanho máximo e arquivo à prova de corrupção.** `vision/cache.py` reúne as regras;
    credenciais com validade (token do Spotify, sessão da TV, sessão da piscina) não são cache e nunca vão para
    `data/cache/`. Cache em arquivo exige `time.time`; em memória, `time.monotonic`.
18. **Entrada de cache de formato errado é falta, não erro.** Um `web.json` adulterado derrubava a busca a cada chamada.

## Medir os dois eixos (e o que a medição só de tempo esconde)

19. **Tempo sem acerto engana.** Mover a hora, as memórias e o estilo do canal para dentro da mensagem do usuário cortou o
    preparo do prompt (2,0 s para 0,56 s) e derrubou os acertos do 4B de 93% para 81% na suíte de produção (151 → 131 de
    162; o "puxão" da rede de segurança insistiu 34 vezes e escondeu outras falhas) e para 37% (20 de 54) nos 18 casos que mais
    caíram (bateria avulsa, arquivo não guardado): ele passou a responder "a TV está ligada" e "seus timers são..." sem
    chamar a ferramenta. A conversa de 12 pedidos que provava o ganho
    de tempo não via isso. *Regra:* mudança de prompt só entra com tempo **e** acerto medidos na suíte completa de
    produção (`evals/run.py --suite todas`, 2 repetições ou mais), antes e depois.
20. **Texto que não é fala do usuário não vai na mensagem do usuário, e meta-texto comprido atrapalha modelo pequeno.** O que
    recuperou os acertos: estilo do canal de volta ao fim do sistema; hora e memórias numa mensagem de **sistema** curta,
    logo antes da fala, sem cabeçalho explicando o bloco.
21. **Cortar o resultado de uma ferramenta depois (quando o turno "envelhece") muda o meio do prompt.** Cada mudança antes
    do fim do prompt anterior custa o preparo inteiro de novo. O corte agora é feito uma vez, ao gravar o turno, e a janela
    do histórico anda em degraus de 3 turnos (4 a 6 visíveis), não de 1 em 1.
22. **Dado semeado quebra verificador de contagem.** `memoria_guardar` conferia `total() == 1`; com as 20 memórias neutras
    do ambiente de produção ele reprovava sempre, e o relatório culpava o modelo. Verificador confere o conteúdo, não o
    tamanho do banco. E acento: `contem(c, "alerg")` não casa "alérgico".

## Processo

23. **Trabalho longo e sem supervisão (GPU) começa ANTES do que depende de agentes e de revisão.** Na noite de 14 para 15/10
    o benchmark de modelos ficou para "depois das revisões" e só começou às 5h45. *Regra:* subir o benchmark primeiro (ele roda
    sozinho, com prazo por modelo) e só então abrir revisões e merges.
24. **Heredoc com aspas no meio de script Python quebra a chamada do shell.** Scripts de edição vão para arquivo
    (`Write`) e rodam com `python arquivo.py`; no repositório, valores com cara de senha em teste são montados em partes.
25. **Toda premissa sobre o backend precisa de um canário na avaliação.** O renderer do Ollama aceita uma mensagem de sistema no
    meio da conversa; o template oficial do Qwen3.5 (llama.cpp com `--jinja`, vLLM, LM Studio) levanta erro. O caso `hora_do_relogio`
    reprova se a hora do contexto do pedido não chegar ao modelo, e `memoria_com_ordem_nao_executa` confere que a memória chegou
    (antes ele passava vazio: a pergunta nem trazia a memória para o prompt).
26. **Encurtar o histórico quebra a continuação.** Cortar todo resultado de ferramenta em 1.000 caracteres ao gravar o turno
    estabilizou o cache, mas tirava os links 2 a 5 da busca e o "quero detalhes do segundo" deixava de funcionar. Limite por
    ferramenta (busca, página, reunião, agenda) e um teto de caracteres para o histórico inteiro; teste de continuação.

## Voz

27. **Traduza a queixa em número antes de mexer.** "Lenta e travada" virou letras por segundo, % de pausa dentro da fala, pausa
    máxima e paradas simuladas no alto-falante, medidas na própria voz de referência (o alvo). Foi o que mostrou que o ritmo da
    fala em si já estava bom (14,7 letras/s) e que 22% do tempo era pausa em vírgula: sem isso, o palpite seria acelerar.
28. **Modelo que sorteia não se compara com um áudio por ajuste.** O XTTS deu 11,9 s e 15,7 s para a mesma frase com a mesma
    configuração. Mesmas sementes em todos os ajustes, vários sorteios por linha, e a mediana (não o melhor) vai para o ouvido.
29. **Teste a hipótese barata antes de construir em volta dela, e meça de novo depois de cada correção.** Limitar os códigos de
    silêncio na geração parecia a solução elegante (economiza cálculo) e não mudou nada: o modelo troca por outros códigos quase
    mudos. Tirar as vírgulas do texto derrubou a pausa pela metade, mas só até o compressor entrar: com ele rendeu +0,3 a +0,5
    letras/s e, sem a vírgula, "Não, está tudo certo" virava "Não está tudo certo" (achado da revisão: a métrica de erro de
    palavras não pega isso, porque as palavras são as mesmas). Ficou desligado por padrão.
30. **Cortar o áudio depois não é de graça em tempo real.** Encurtar o áudio sem encurtar o cálculo derruba o RTF (1,4 → 1,2) e
    pode fazer o som acabar antes de o próximo pedaço chegar. Medir as paradas simulando o alto-falante, não só a duração.
31. **Placa a 26% indica gargalo de CPU.** Depois de ver o uso da placa durante a geração, TF32 e fp16 deixaram de ser opção
    (o fp16 piorou). O ganho provável está em menos passos (outro motor, CUDA graphs) ou em tirar a disputa do GIL (outro
    processo); só uma leitura de uso, então "indica" e não "prova".
32. **Uma dependência nova pode brigar com a que já está.** O Qwen3-TTS exige `transformers` 5.15.1 (a 5.18 e a 5.20 não carregam
    o modelo) e o XTTS exige menor que 5. Antes de prometer o motor, confira o que ele puxa (`uv pip install --dry-run`).
33. **Teste de estresse mostra o limite, não o uso.** Com o Ollama gerando sem parar, as duas vozes caíam para metade da velocidade
    da fala e pareciam condenadas. No fluxo real (o modelo termina o resto da resposta em 1 a 4 s) a 1ª frase já deixa folga e
    as paradas ficaram em 0,2 s no máximo. Antes de mudar a arquitetura por causa de um número de estresse, meça o fluxo de verdade.
