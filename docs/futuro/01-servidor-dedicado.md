# 01. Servidor dedicado para a IA

## Objetivo

Hoje o Vision divide o PC com tudo o mais: quando a GPU precisa ficar livre, o modo de liberar a GPU tira o modelo da VRAM e pausa a escuta. A ideia é ter **um computador só para a IA**, ligado o tempo todo, e o PC principal (e depois o celular) usando o Vision **pela rede de casa**.

```
                 rede de casa (Wi-Fi / cabo)
┌──────────────────────┐        ┌─────────────────────────────┐
│ Servidor Vision      │◄──────►│ PC principal: janela Vision │
│ Linux, 24 h          │        │ (só interface; a bandeja    │
│ • Ollama + modelo    │        │  mostra o estado do servidor)│
│ • núcleo (agente,    │◄──────►│ Celular: página do Vision   │
│   memória, agenda)   │        └─────────────────────────────┘
│ • STT / TTS          │◄──────► satélites de voz (plano 02)
│ • Home Assistant     │◄──────► dispositivos da casa (plano 03)
└──────────────────────┘
```

## O que vai para o servidor e o que fica no PC

| Peça | Servidor | PC principal |
|---|---|---|
| Ollama + modelo | ✅ | — |
| Agente, memória (`memoria.db`), perfil, conversas | ✅ | — |
| MCP do Google Agenda e login do Google | ✅ | — |
| Parakeet (fala → texto) e Piper (texto → fala) | ✅ | — |
| Microfone e alto-falante do PC | — | ✅ Opcional: o PC vira um "satélite" com o microfone dele (ver plano 02) |
| Janela (orbe, painéis) | — | ✅ Conecta no servidor |
| Bandeja e atalhos | — | ✅ Versão leve: estado, abrir a janela, falar agora |

## Hardware

- **Modelo de 4B na GPU:** uma placa NVIDIA com **8 GB ou mais** de VRAM. Pode ser usada: RTX 3060 12 GB (melhor custo), 3060 Ti, 4060 8 GB. O Qwen3.5 4B usa ~3–4 GB; sobra espaço para um modelo maior depois.
- **Sem GPU:** um mini PC com CPU moderna roda o 4B quantizado, mas a resposta fica lenta (vários segundos). Serve para testar, não para o dia a dia.
- **Resto:** 16 GB de RAM, SSD de 256 GB, rede por cabo. Fonte e ventilação para ficar ligado 24 h.
- **Consumo:** vale medir. Uma máquina ligada o tempo todo aparece na conta de luz. Com satélites (plano 02), suspender não serve: eles não acordam o PC. O caminho é uma máquina de baixo consumo parado (GPU em repouso, sem suspender).

## Sistema

- **Ubuntu Server LTS**, sem interface gráfica, e driver NVIDIA + CUDA para o Ollama.
- **Docker Compose** com serviços separados: Ollama, núcleo do Vision, Home Assistant (plano 03) e, se usarmos, os serviços Wyoming (plano 02). Um serviço que cair não derruba os outros e volta sozinho (`restart: unless-stopped`).
- **Dados:**
  - `data/` fica num volume com **backup cifrado** e fora do git (memória, perfil, conversas, tokens);
  - o `.env` e o `google-oauth.json` ficam só no servidor, nunca no git.

## Segurança

Hoje o servidor aceita **só `127.0.0.1`**, o que é seguro porque nada de fora alcança. Na rede de casa isso muda, e é o ponto mais importante deste plano:

1. **Só a rede de casa**, nunca a internet. Nada de abrir porta no roteador. Acesso de fora, se um dia fizer falta, só por VPN (Tailscale ou WireGuard).
2. **HTTPS/WSS:** com o tráfego saindo do próprio PC, o token passaria em texto puro pelo Wi-Fi. Um certificado próprio da rede local, ou o túnel do Tailscale, resolve.
3. **Um token por aparelho** (PC, celular, cada satélite), em vez do token único de hoje. Assim dá para revogar um aparelho perdido sem trocar todos. Os tokens ficam guardados no servidor com nome e data.
4. **Origin:** a checagem de hoje (só o próprio servidor) continua. A lista passa a ser configurável, com os endereços da interface.
5. **Escrita continua pedindo confirmação.** No servidor, isso vale ainda mais: um satélite na sala não pode confirmar sozinho uma ação que você não ouviu (ver plano 02, confirmação por satélite).
6. **Portas:** o `ufw` sozinho **não protege** portas publicadas pelo Docker, que escreve as próprias regras no iptables e passa por cima dele.
   - Ollama e Wyoming ficam **sem `ports:`**, só na rede interna do Docker: só o núcleo e o HA os alcançam. O Ollama não tem autenticação.
   - O que precisa ser visto na rede de casa (a API do Vision, o HA) é publicado de forma explícita, com a origem limitada nas regras `DOCKER-USER` (ou com `ufw-docker`).
   - Teste obrigatório: `nmap` de outra máquina da rede, conferindo que só essas portas respondem.
7. **Escuta fora de `127.0.0.1` só no modo servidor**, e só com TLS e token por aparelho já prontos (plano 04, itens 1 a 3). Antes disso, o núcleo continua preso ao `127.0.0.1`.

## Janela conectando a um servidor remoto

A janela já fala com o núcleo por WebSocket com token. Para o servidor remoto, falta:

- **Configuração** `servidor.endereco`, por exemplo `https://vision.home.arpa:8765` (o domínio reservado para redes de casa), no lugar do `127.0.0.1` fixo. Endereço `http://` só é aceito para `127.0.0.1`; para qualquer outro, a janela recusa.
- **Primeiro pareamento:** o servidor mostra um código (ou QR) e a janela do PC recebe o token dela, sem copiar arquivo à mão. O código expira em poucos minutos, vale uma vez e tem limite de tentativas. O pareamento só abre por um comando no próprio servidor, ou numa sessão já autenticada, e o código aparece só ali: um aparelho qualquer da rede não consegue pedir um.
- **O token nunca vai na URL de consulta** (`?token=`), que fica em log e histórico: vai no cabeçalho `Authorization` ou no fragmento (`#t=`), como hoje.
- **Sem servidor na rede**, a janela mostra "sem conexão" e tenta de novo, o que já acontece hoje.
- **A bandeja do PC** vira cliente: mostra o estado do servidor e abre a janela.

## Ordem sugerida

1. Preparar o código (plano 04): endereço configurável, token por aparelho, núcleo sem depender do Windows (bandeja e atalhos opcionais).
2. Rodar o núcleo num Linux de teste (WSL ou máquina virtual) para achar o que é só do Windows.
3. Montar o servidor: Ubuntu, driver, Docker, Ollama e modelo.
4. Migrar `data/` (memória, perfil, conversas) e o login do Google.
5. Apontar a janela do PC para o servidor, com HTTPS e pareamento.
6. Desligar o núcleo local do PC; o PC fica só com a janela.

## Riscos e dúvidas

- **Latência:** resposta pela rede soma poucos milissegundos, nada perto do tempo do modelo.
- **Login do Google em modo teste** (7 dias): continua existindo. O fluxo de aparelho do Google (código na tela) não aceita o escopo da Agenda. Num servidor sem tela, o jeito é gerar o link de consentimento no servidor, abrir em outro aparelho e colar de volta o endereço de retorno; ou fazer o login no PC e levar o token.
- **Voz no PC principal:** se o PC deixar de rodar o núcleo, o "Hey Vision" do PC depende do plano 02 (o PC como satélite).
