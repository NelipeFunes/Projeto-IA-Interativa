# Conectar o Google Agenda ao Jarvis (~10 min, uma vez)

O Jarvis usa o servidor MCP `@cocal/google-calendar-mcp` (já instalado em `node/`). Ele precisa de um
"cliente OAuth" seu no Google Cloud. É grátis e não precisa de cartão.

## Passo a passo

1. Abra <https://console.cloud.google.com/> logado na sua conta Google.
2. **Criar projeto:** seletor de projeto (topo) → *Novo projeto* → nome `jarvis` → *Criar*. Confira que ele ficou selecionado.
3. **Ativar a API:** menu ☰ → *APIs e serviços* → *Biblioteca* → procure **Google Calendar API** → *Ativar*.
4. **Tela de consentimento** (*APIs e serviços* → *Tela de consentimento OAuth*, ou "Google Auth Platform"):
   - Tipo de usuário: **Externo** → *Criar*.
   - Nome do app: `Jarvis`; e-mail de suporte e de contato: o seu. Salve.
   - Em **Público-alvo / Usuários de teste**: *Adicionar usuários* → o seu e-mail.
   - Deixe o status em **Teste** (não publique). Consequência: o login vence a cada 7 dias.
5. **Criar o cliente OAuth:** *Credenciais* (ou *Clientes*) → *Criar credenciais* → *ID do cliente OAuth* →
   tipo de aplicativo **App para computador (Desktop app)** → nome `jarvis-pc` → *Criar*.
   - Tem que ser **Desktop app**. "Aplicativo da Web" não funciona com esse servidor.
6. Clique em **Baixar JSON** e salve como:
   `C:\Users\User\jarvis\data\google-oauth.json`
7. No terminal, na pasta do projeto:

   ```bash
   uv run jarvis google-login
   ```

   O navegador abre. Escolha a conta e aceite. Como o app está em teste, aparece o aviso "O Google não
   verificou este app" → *Continuar*. Esse aviso é esperado para um app pessoal.
8. Teste: `uv run jarvis teste agenda` e depois `uv run jarvis chat` → "qual minha agenda de hoje?".

## A cada 7 dias

Rode `uv run jarvis google-login` de novo. O Jarvis avisa no 6º dia ao iniciar. Se esquecer, ele diz
"a agenda está sem login" quando você perguntar da agenda.

Para acabar com isso um dia: tela de consentimento → *Publicar app* → "Em produção". Para uso pessoal
não precisa de verificação do Google (continua o aviso de app não verificado, mas o token não vence mais).

## Onde ficam as coisas

- Credenciais do app: `data\google-oauth.json` (fora do git).
- Token de acesso: `C:\Users\User\.config\google-calendar-mcp\tokens.json` (gerado pelo login).
- Data do último login: `data\google-login.txt`.
