# Planos para o futuro do Vision

Ideias registradas em 30/09/2026 para não se perderem. **Nada disto está implementado.** Cada arquivo descreve o objetivo, as opções, a recomendação e a ordem dos passos. Quando um plano virar trabalho, ele ganha um branch e um PR, como tudo no projeto.

| Arquivo | Assunto | Resumo |
|---|---|---|
| [01-servidor-dedicado.md](01-servidor-dedicado.md) | Servidor só para a IA | Tirar o cérebro do PC principal e pôr num PC Linux ligado 24 h. A janela do Vision passa a se conectar a ele pela rede de casa. |
| [02-satelites-de-voz.md](02-satelites-de-voz.md) | Microfones pela casa | Pequenos aparelhos com ESP32-S3, microfone e alto-falante, um por cômodo, falando com o Vision. |
| [03-casa-inteligente.md](03-casa-inteligente.md) | Luzes, dispositivos, acessos | O Vision controlando a casa: primeiro as luzes que hoje passam por um assistente comercial, depois o resto. |
| [04-preparar-o-codigo.md](04-preparar-o-codigo.md) | Deixar o programa pré-pronto | O que mudar no código já agora, para que os três planos acima encaixem sem reescrever nada. |

## Onde estamos hoje (para comparar)

- **Tudo roda no PC principal:** Windows 11, com uma GPU NVIDIA de 8 GB.
- **Cérebro:** Qwen3.5 4B no Ollama (GPU), com ferramentas próprias em português e confirmação obrigatória para qualquer escrita.
- **Voz:**
  - fala → texto com Parakeet (CPU);
  - texto → fala com Piper (CPU);
  - "Hey Vision" achado por transcrição;
  - conversa aberta e fechada por voz.
- **Núcleo em segundo plano** (`visionw`): voz, bandeja, janela e servidor local em `127.0.0.1:8765` com token.
- **A janela** já é um processo separado que fala com o núcleo por WebSocket. É isso que torna o plano 01 viável sem reescrever a tela.

## Regras que valem para todos os planos

- **Nada da casa real no repositório.** O repositório é público: a lista de dispositivos, cômodos, marcas e endereços de rede fica só em `data/` (fora do git). Os planos falam de forma genérica.
- **Escrita sempre com confirmação**, e texto de fora (agenda, memória, resposta de ferramenta) nunca dispara ação.
- **Nada escuta fora de `127.0.0.1` sem TLS e token por aparelho.**

## Correções em relação ao rascunho de outra sessão

O rascunho que originou estes planos foi escrito sem conhecer este projeto. Vale corrigir o que ele supunha:

- **Sistema:** o PC de hoje roda Windows 11, não Ubuntu. O Ubuntu só entra no servidor dedicado (plano 01).
- **Modelo:** é o Qwen3.5 4B, já validado com ferramentas (56 de 56 nos testes do projeto). Ele supera o 9B nesses mesmos testes.
- **Home Assistant:** o rascunho usava o pipeline do Home Assistant (Whisper + Piper + Ollama) como cérebro. Aqui o cérebro já existe e é melhor para o nosso caso: confirmação de escrita, memória, agenda e um prompt pensado para modelo pequeno. **Recomendação:** o Home Assistant entra como **hub de dispositivos** (plano 03) e, se ajudar, como ponte para os satélites (plano 02). Mas quem pensa continua sendo o Vision.
