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

- [ ] **T-001** 🟡 **Trocar o nome do assistente para "balinha"** (pedido de 06/10)
  - O nome vem de `assistente.nome` (`config.yaml`; também pela tela de Ajustes). Aparece no prompt, na janela, na bandeja e na saudação.
  - **Decidir antes:** a frase de ativação também muda? O modelo `hey_vision` foi treinado para "Hey Vision" (T-006). Trocar a frase exigiria treinar outro modelo e mudar `voz.palavra_ativacao` e as grafias em `comandos.achar_ativacao`.
  - Conferir também: textos fixos com "Vision" na UI, no README e nas mensagens de erro; o Whisper/Parakeet escrever "balinha" de formas diferentes na ativação por texto.
- [ ] **T-002** 🔴 **Reiniciar o Vision** para carregar as ferramentas da TV (PRs #46–#48) e testar por voz: "abre o Spotify na TV", "toca blank space no YouTube da TV".
- [ ] **T-003** 🔴 **Parear de novo as teclas da TV** (o pareamento venceu em 06/10). Só dá com a TV ligada: Ajustes → Conexões → TV Samsung, ou `vision tv-parear`.
- [ ] **T-004** 🟡 **Descobrir por que o pareamento da TV vence** (suspeita: a TV reinicia ou renova a sessão). Olhar o log e anotar quando as teclas deixam de reagir.
- [ ] **T-005** 🟡 **Testar na TV de verdade** o que só passou em simulação: desligar, canal, fonte/HDMI, setas, abrir Netflix e navegador, tocar link de mídia (UPnP), timer "desliga a TV em N minutos".
- [ ] **T-006** 🟡 **Treinar o modelo "Hey Vision" no Google Colab** e pôr `hey_vision.onnx` em `modelos/openwakeword/` (passo a passo no README, seção "Hey Vision").
- [ ] **T-007** 🟡 **Gravar a própria voz para o verificador** (`scripts/ativacao/gravar_minha_voz.py`) depois do T-006, e reiniciar o Vision. As gravações ficam em `data/` e não saem do PC.
- [ ] **T-008** 🟢 **Expor `voz.limiar_ativacao` na tela de Ajustes** (hoje só no `config.yaml`).
- [ ] **T-009** 🟢 **Login do Orbit pela tela de Conexões** (se o Orbit mandar código por e-mail, a tela pede).
- [ ] **T-010** 🟢 **Calibrar o microfone** com o headset ligado (`vision teste voz`).
- [ ] **T-011** 🟢 **Ligar a TV pela rede:** impossível (o Wi-Fi dela desliga junto). Alternativa: um controle infravermelho (Broadlink RM4 Mini, `python-broadlink`) como segundo backend. O cliente da TV já é separado das ferramentas para isso.
- [ ] **T-012** 🟢 **Servidor dedicado para o modelo** (PC com GPU menor, rodando só o Ollama; `modelo.host` aponta para ele). Antes: rever o modo jogo e o "modelo sempre carregado", que supõem o Ollama no mesmo PC, e travar a porta 11434 por firewall só para o IP do PC principal.
- [ ] **T-013** 🟢 **Pausar e retomar música na TV** por voz (tecla `pausa` já existe; depende do T-003).

## Em andamento

- [~] **T-014** 🟡 **PR #37: pacote independente** (instalação sem a pasta do projeto). Aberto; não mesclar sem revisão.
- [~] **T-015** 🟡 **Hey Vision pelo som:** código e README prontos (PR #45); falta o treino no Colab e as gravações (T-006 e T-007).

## Concluídas

- [x] **T-016** **Controle da TV Samsung (série J) pela rede** (05/10): teclas por PIN, volume e mudo pelo UPnP, YouTube pelo DIAL com OK no perfil, busca de vídeo, timer com ação, cartão em Conexões. PRs [#46](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/46) e [#47](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/47).
- [x] Abrir Spotify e navegador da TV pelo ID do app, "abre o X na TV" por voz (06/10), PR [#48](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/48).
- [x] "Hey Vision" por modelo e por transcrição ao mesmo tempo (`voz.ativacao: ambos`), verificador com a própria voz e scripts de treino (03/10), PR [#45](https://github.com/NelipeFunes/Projeto-IA-Interativa/pull/45).
- [x] Voz XTTS e busca na web com memória por assunto e cache da agenda (PRs #22 e #23).
- [x] Histórico anterior: veja os PRs mesclados no GitHub.
