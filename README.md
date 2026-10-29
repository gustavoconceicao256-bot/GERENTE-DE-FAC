# FacControl FiveM RP — Bot para Discord

Bot Discord em Python para gerenciamento de facção de servidor FiveM RP, com fluxo de whitelist (WL), recrutadores autorizados, registros de farm e armazenamento de comprovantes em SQLite.

> **Status:** versão inicial organizada para publicação no GitHub. A sintaxe Python foi verificada; configure e teste no seu próprio servidor Discord antes de usar em produção. Nem todos os recursos planejados estão completos — veja [Limitações conhecidas](#limitações-conhecidas).

## Recursos incluídos

- Solicitação de WL por painel e seleção de recrutador autorizado.
- Somente o recrutador selecionado pode aprovar ou recusar a solicitação.
- Recusa exige motivo; solicitações expiram após 5 minutos.
- Resultado enviado por mensagem direta, com botão para o convite configurado.
- Cadastro de membro, aplicação do cargo configurado e tentativa de definir apelido no formato `[ M ] NOME - ID`.
- Painel de comprovantes de farm; imagens são armazenadas no banco SQLite como dados binários.
- Comando para recuperar um comprovante salvo.
- Migrações aditivas para bancos existentes.
- Painel `/config` com configurações separadas para WL, hierarquia, farm, painel principal, membros, liderança, cargos, logs e canais de ADV/PD/blacklist.
- Título, descrição e canal de publicação configuráveis para cada painel; destino informado pelo ID do canal.
- Painel de hierarquia com lista explícita de cargos exibidos e prioridade para ordenar patentes.
- Aprovação opcional dos comprovantes de farm; quando ativada, a liderança aprova ou recusa no canal definido, com motivo obrigatório para recusa.

## Requisitos

- Python 3.10 ou superior.
- Um aplicativo/bot criado no [Discord Developer Portal](https://discord.com/developers/applications).
- As intents **Server Members Intent** e **Message Content Intent** ativadas no portal.

## Instalação local

1. Baixe este repositório ou clone-o:

   ```bash
   git clone https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
   cd SEU-REPOSITORIO
   ```

2. Crie e ative um ambiente virtual:

   **Windows (PowerShell):**

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   **Linux/macOS:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Instale as dependências:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Copie `.env.example` para `.env` e preencha as variáveis. **Nunca publique o arquivo `.env` nem o token do bot.**

   Windows:

   ```powershell
   Copy-Item .env.example .env
   ```

   Linux/macOS:

   ```bash
   cp .env.example .env
   ```

5. No Discord Developer Portal, ative as intents privilegiadas necessárias e convide o bot com as permissões adequadas.
6. Inicie o bot:

   ```bash
   python bot.py
   ```

7. No Discord, use `/config` para abrir o painel de configuração. Depois, use `/painel-wl` e `/painel-farm` para publicar os respectivos painéis, desde que sua conta tenha as permissões exigidas pelo bot.

## Configuração

As variáveis iniciais estão documentadas em [`.env.example`](.env.example). Entre as principais:

| Variável | Finalidade |
|---|---|
| `DISCORD_TOKEN` | Token secreto do bot. Não compartilhe. |
| `DISCORD_GUILD_ID` | ID do servidor Discord, quando aplicável. |
| `DATABASE_PATH` | Caminho do banco SQLite. |
| `MEMBER_ROLE_ID` | ID do cargo aplicado ao membro aprovado. |
| `RECRUIT_ROLE_ID` | ID do cargo cujos usuários podem ser selecionados como recrutadores. |
| `APPROVAL_CHANNEL_ID` | Canal usado no fluxo de aprovação, conforme a configuração do código. |
| `SERVER_INVITE_URL` | Convite exibido no botão enviado ao candidato. |
| `ADMIN_LOG_CHANNEL_ID` / `PUBLIC_LOG_CHANNEL_ID` / `FARM_LOG_CHANNEL_ID` | Canais de log, quando utilizados pelos recursos correspondentes. |

### Painel `/config`

Use `/config` no servidor. É necessário ter o cargo de liderança configurado ou a permissão de administrador. O painel é privado e tem áreas para configurar cada módulo e publicar os painéis nos canais escolhidos.

- **WL:** título, descrição, canal em que o painel público será publicado e canal que recebe as solicitações para decisão do recrutador.
- **Hierarquia:** título, descrição, canal de publicação e quais cargos aparecem na lista. Cada cargo usa o formato `ID_CARGO=SIGLA=PRIORIDADE`, separado por ponto e vírgula.
- **Farm:** título, instruções, canal para publicar o painel, canal de revisão de comprovantes e escolha entre aprovação obrigatória ou automática.
- **Painel principal, membros e liderança:** título, descrição e canal de publicação separados para cada painel.
- **Cargos e regras:** cargo de membro, recrutador, liderança, ADV1/ADV2/ADV3, cooldown, limite diário, fuso e opção de PD automático na ADV3.
- **Logs:** destinos separados para logs administrativos, públicos, farm, ADV, PD e blacklist.
- **Publicação:** use os botões na parte inferior do `/config` para publicar WL, hierarquia, farm, painel principal, membros e liderança depois de salvar os campos.

Nos campos de canal/cargo, informe o ID numérico do Discord; use `0` quando a tela permitir deixar um destino sem configurar. O bot precisa ter permissão para ver o canal, enviar mensagens e anexar arquivos. As configurações são salvas na tabela `settings` do SQLite e reaplicadas durante a execução.

### Patentes e apelidos

No botão **Hierarquia**, informe as entradas no formato `ID_CARGO=SIGLA=PRIORIDADE`, separadas por ponto e vírgula. Exemplo ilustrativo: `123456789012345678=M=10; 234567890123456789=DIR=20`. Uma prioridade numérica maior representa uma patente mais alta e define qual sigla será usada no nickname.

> Os IDs devem ser números reais do seu servidor. O bot precisa ter permissões para ver canais, enviar mensagens, anexar arquivos, gerenciar apelidos e cargos; o cargo do bot deve ficar acima dos cargos que ele gerencia.

## Estrutura do repositório

```text
.
├── .github/
│   └── workflows/
│       └── python.yml
├── docs/
│   └── CONFIGURACAO.md
├── .env.example
├── .gitignore
├── README.md
├── bot.py
└── requirements.txt
```

## Limitações conhecidas

- A interface usa componentes tradicionais do `discord.py`; não é uma implementação integral de Discord Components V2.
- O comprovante de farm é salvo como imagem e pode exigir aprovação da liderança. O sistema não interpreta automaticamente a imagem para calcular itens, quantidades ou pontos de farm; o registro de pontos continua sendo separado pelo comando `/farm`.
- O comportamento real depende de IDs, permissões, intents e configurações válidas. Teste primeiro em um servidor de desenvolvimento.

## Resolver erro do Render: `403 Forbidden (50001): Missing Access`

Se o log mostrar a falha em `bot.tree.sync(guild=...)`, o Discord não está permitindo registrar os comandos no servidor indicado. O bot agora registra uma mensagem mais clara, tenta sincronizar os comandos globalmente como alternativa e evita encerrar o processo apenas por essa falha.

Confira estes pontos no Render e no Discord:

1. Em **Environment**, confirme que `DISCORD_TOKEN` é o token da mesma aplicação/bot que você convidou para o servidor. Não compartilhe esse token.
2. `DISCORD_GUILD_ID` deve ser o ID do servidor Discord, não o ID de um canal nem de um cargo. Ative o Modo Desenvolvedor e use **Copiar ID do servidor**.
3. Confirme que esse bot está dentro do servidor correto. Se necessário, gere novamente o convite no Developer Portal com os escopos `bot` e `applications.commands` e autorize-o no servidor.
4. Se ainda não souber o ID correto do servidor, defina temporariamente `DISCORD_GUILD_ID=0` no Render. O bot tentará sincronizar os comandos globalmente; a aparição desses comandos pode demorar mais do que a sincronização específica de servidor.
5. O aviso `PyNaCl is not installed, voice will NOT be supported` não é a causa desse erro: ele só importa para recursos de voz, que este bot não usa.

Para hospedar um bot Discord, o tipo mais natural no Render é **Background Worker**. Use `pip install -r requirements.txt` como Build Command e `python bot.py` como Start Command. Esta versão também detecta a variável `PORT` e oferece `GET /` e `GET /health`, então consegue manter o processo ativo caso você já tenha criado um **Web Service**. Essas rotas são apenas healthcheck; os comandos e recursos funcionam pelo Discord.

## Segurança

- Não envie tokens, arquivos `.env`, banco de produção ou dados privados para o GitHub.
- Se o token for publicado por engano, regenere-o imediatamente no Discord Developer Portal.
- O SQLite guarda configurações, dados e comprovantes localmente; faça backup seguro do arquivo de banco. Em hospedagens com disco efêmero, anexe um disco persistente ou use um banco persistente adequado para evitar perder esses dados após recriações/deploys.

## Publicar no GitHub

1. Crie um repositório vazio no GitHub.
2. Extraia os arquivos deste projeto para uma pasta local.
3. Revise `.env.example` e confirme que nenhum token ou dado real está incluído.
4. Execute `git init`, `git add .`, `git commit -m "Initial commit"` e conecte ao repositório remoto conforme as instruções exibidas pelo GitHub.
5. Envie a branch principal com `git push -u origin main`.

O workflow em `.github/workflows/python.yml` verifica a sintaxe do código em cada push e pull request. Ele não testa a conexão com o Discord, pois isso exigiria um token secreto e um servidor configurado.

## Licença

Nenhuma licença foi definida neste pacote. Escolha e adicione uma licença antes de permitir que outras pessoas reutilizem ou distribuam o código.
