# Quadro de tarefas

Todas as tarefas do projeto num só lugar: **Pendentes**, **Em andamento** e **Concluídas**.

Como usar:
- Cada tarefa tem um código (`T-001`…) que não muda. Para mudar de coluna, mova o bloco inteiro.
- Marcas: `[ ]` pendente, `[~]` em andamento, `[x]` concluída. Prioridade: 🔴 alta, 🟡 média, 🟢 baixa.
- Concluída leva a data e o PR (quando houver). Mudança neste arquivo também passa por branch e PR.
- Este repositório é **público**: só tarefas técnicas aqui. Nada de dado pessoal, rotina ou credencial.

Próximo código livre: **T-017**

---

## Pendentes

- [ ] **T-001** 🟡 **Trocar tudo que é "Vision" para "balinha"** (pedido de 06/10) — **decidido, ainda não começou: só fazer quando o Felipe mandar.**
  - **Decisão (06/10):** é a troca total. O nome do assistente, a frase de ativação ("Hey Vision" vira "Hey balinha") e tudo que se chame Vision. Não é só o nome na janela.
  - **O que entra:**
    - Nome do assistente: `assistente.nome` (`config.yaml`, tela de Ajustes), prompt, janela, bandeja, saudação, textos fixos da UI e mensagens de erro.
    - Ativação por voz: `voz.palavra_ativacao`, as grafias e a calibração em `comandos.achar_ativacao`, e **um novo modelo openWakeWord** (T-006 e T-015). O `hey_vision.onnx` não serve para "Hey balinha".
    - Nome do projeto: comando `vision` (`pyproject.toml`, `cli.py`), pacote Python `vision`, `Vision.exe` e o lançador, pasta do projeto, README, nome do repositório no GitHub e links.
    - Textos de treino e scripts: `scripts/ativacao/` (`preparar_treino.py` com as grafias e frases negativas, `gravar_minha_voz.py`) e os testes.
    - **Identificadores internos que quebram em silêncio** (achados da revisão; o resumo é `git grep -i vision` em `src/`, `scripts/`, `tests/`, `ui/`, `evals/`, `README.md` e `config.yaml`, cerca de 1050 linhas):
      - Instância única e inicialização: o mutex `Local\VisionNucleo` (`nucleo.py`) e o `ID_APP` (`interface.py`), o atalho `Vision.lnk` e o script gráfico `visionw` (`inicializacao.py`, `pyproject.toml`). Renomear um lado só pode deixar **dois núcleos** rodando; o atalho antigo precisa ser removido, senão o Windows continua abrindo o executável antigo.
      - Variáveis de ambiente `VISION_*` (`VISION_HOME`, `VISION_TOKEN`, `VISION_URL`, `VISION_SEM_AJUSTES` etc.): quem já as define perde a configuração sem aviso. Aceitar os dois nomes por um tempo.
      - Nomes que outros sistemas veem: os programas Wyoming `vision-parakeet` e `vision-piper` (`voice/wyoming.py`; quem montou o pipeline do Assist precisa reconfigurar), o ícone da bandeja e `prog="vision"` no `cli.py`.
      - **Dados no disco:** a chave `"vision"` no histórico de conversa (`brain/agent.py`, `voice/loop.py`) é gravada em `data/`. Renomear sem migração deixa as conversas antigas ilegíveis.
      - Palavras do reconhecimento de fala: `NOME` e `GRAFIAS` (`voice/comandos.py`), `COMPLEMENTO_DO_SIM` (`brain/confirmacao.py`) e `ANTES_DO_VERBO` (`tools/casa.py`). Sem trocar, "balinha, sim" não confirma uma ação e "balinha, liga a luz" não é entendido pelo atalho da casa.
      - Arquivos e pasta: `src/vision/` (o pacote), `recursos/vision.ico` e `vision.png`, `scripts/lancador/Vision.cs`, `vision.cmd` na raiz (o README manda pôr a pasta no PATH), `ui/package.json` (`vision-ui`), `uv.lock`, a lista de processos em `tools/pc.py` e os prefixos de `evals/`.
      - Artefatos da ativação: refazer o verificador (`hey_vision_verificador.pkl`) e a calibração (`data/ativacao.json`), e mudar o padrão `hey_vision` em `diagnostico_voz.py` e `gravar_minha_voz.py`.
  - **Cuidados:**
    - Fazer em etapas, cada uma com PR (por exemplo: nome e textos → ativação e modelo → comando e pacote → renomear repositório e pasta). Renomear o pacote e a pasta quebra caminhos, atalhos, `data/` e o MSIX/atalho de inicialização.
    - "balinha" tem acento fácil de o Parakeet escrever de outro jeito ("balinha", "baliña", "ba linha"): calibrar a ativação por texto depois de trocar.
    - Manter `vision` como apelido do comando por um tempo, para não quebrar o que já usa.
    - Treinar o novo modelo para "Hey balinha", com vozes pt-BR e negativas parecidas ("balão", "balanço", "linha"), antes de gravar a própria voz.
- [ ] **T-002** 🔴 **Reiniciar o Vision** para carregar as ferramentas da TV (PRs #46–#48) e testar por voz: "abre o Spotify na TV", "toca blank space no YouTube da TV".
- [ ] **T-003** 🔴 **Parear de novo as teclas da TV** (o pareamento venceu em 06/10). Só dá com a TV ligada: Ajustes → Conexões → TV Samsung, ou `vision tv-parear`.
- [ ] **T-004** 🟡 **Descobrir por que o pareamento da TV vence** (suspeita: a TV reinicia ou renova a sessão). Olhar o log e anotar quando as teclas deixam de reagir.
- [ ] **T-005** 🟡 **Testar na TV de verdade** o que só passou em simulação: desligar, canal, fonte/HDMI, setas, abrir Netflix e navegador, tocar link de mídia (UPnP), timer "desliga a TV em N minutos".
- [ ] **T-006** 🟡 **Treinar o modelo de ativação no Google Colab** e pôr o `.onnx` em `modelos/openwakeword/` (passo a passo no README, no item **"Hey Vision" pelo modelo**). **Esperar a T-001:** como a frase vai virar "Hey balinha", treinar um modelo `hey_vision` agora seria trabalho jogado fora (~2h30 de Colab).
- [ ] **T-007** 🟡 **Gravar a própria voz para o verificador** (`scripts/ativacao/gravar_minha_voz.py`) depois do T-001 e do T-006 (a voz gravada é para a frase nova), e reiniciar o Vision. As gravações ficam em `data/` e não saem do PC.
- [ ] **T-008** 🟢 **Expor `voz.limiar_ativacao` na tela de Ajustes** (hoje só no `config.yaml`).
- [ ] **T-009** 🟢 **Login do Orbit pela tela de Conexões** (se o Orbit mandar código por e-mail, a tela pede).
- [ ] **T-010** 🟢 **Calibrar o microfone** com o headset ligado (`vision teste voz`).
- [ ] **T-011** 🟢 **Ligar a TV pela rede:** impossível (o Wi-Fi dela desliga junto). Alternativa: um controle infravermelho (Broadlink RM4 Mini, `python-broadlink`) como segundo backend. O cliente da TV já é separado das ferramentas para isso.
- [ ] **T-012** 🟢 **Servidor dedicado para o modelo** (PC com GPU menor, rodando só o Ollama; `modelo.host` aponta para ele). Antes: rever o modo jogo e o "modelo sempre carregado", que supõem o Ollama no mesmo PC, e travar a porta 11434 por firewall só para o IP do PC principal.
- [ ] **T-013** 🟢 **Pausar e retomar música na TV** por voz (tecla `pausa` já existe; depende do T-003).

## Em andamento

- [~] **T-014** 🟡 **PR #37: pacote independente** (instalação sem a pasta do projeto). Aberto; não mesclar sem revisão.
- [~] **T-015** 🟡 **Ativação pelo som:** código e README prontos (PR #45); falta o treino no Colab e as gravações (T-006 e T-007), que agora esperam a T-001 (a frase vai virar "Hey balinha").

## Concluídas

- [x] **T-016** **Controle da TV Samsung (série J) pela rede** (05/10): teclas por PIN, volume e mudo pelo UPnP, YouTube pelo DIAL com OK no perfil, busca de vídeo, timer com ação, cartão em Conexões. PRs [#46](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/46) e [#47](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/47).
- [x] Abrir Spotify e navegador da TV pelo ID do app, "abre o X na TV" por voz (06/10), PR [#48](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/48).
- [x] "Hey Vision" por modelo e por transcrição ao mesmo tempo (`voz.ativacao: ambos`), verificador com a própria voz e scripts de treino (03/10), PR [#45](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/45).
- [x] Voz XTTS e busca na web com memória por assunto e cache da agenda (PRs #22 e #23).
- [x] Histórico anterior: veja os PRs mesclados no GitHub.
