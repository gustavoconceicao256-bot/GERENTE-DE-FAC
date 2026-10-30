# FacControl FiveM RP — Bot para Discord

Bot Discord em Python para gerenciamento de facção de servidor FiveM RP, com fluxo de whitelist (WL), recrutadores autorizados, registros de farm e armazenamento de comprovantes em SQLite.

> **Status:** versão inicial organizada para publicação no GitHub. A sintaxe Python foi verificada; configure e teste no seu próprio servidor Discord antes de usar em produção. Nem todos os recursos planejados estão completos — veja [Limitações conhecidas](#limitações-conhecidas).

## Recursos incluídos

- Solicitação de WL por painel e seleção de recrutador autorizado.
- Somente o recrutador selecionado pode aprovar ou recusar a solicitação.
- Recusa exige motivo; solicitações expiram após 5 minutos.
- Resultado enviado por mensagem direta, com botão para o convite configurado.
- Cadastro de membro, aplicação do cargo configurado e tentativa de definir apelido no formato `[ M ] NOME - ID`.
- Registro de farm integrado ao cadastro aprovado da WL: o bot identifica o jogador pelo ID do Discord e recupera nome/ID FiveM do SQLite, sem perguntar esses dados novamente.
- Fluxo de farm pelo painel: seleção de item ativo configurado, quantidade numérica e imagem de prova anexada no mesmo canal.
- Registros e provas salvos no SQLite; envio para canal de aprovação (se habilitado) e publicação do registro final completo no canal de logs de farm.
- Comando para recuperar um comprovante salvo.
- Migrações aditivas para bancos existentes.
- Painel `/config` simplificado para WL, hierarquia, farm, seleção de cargos e logs.
- Cargos e canais configuráveis pelos menus do Discord, com alternativa de informar o ID manualmente quando necessário.
- WL e farm com título, descrição, canal de publicação escolhido em lista, foto/miniatura e banner por URL HTTPS.
- Hierarquia configurável sem informar previamente a quantidade de cargos: percorra as páginas, adicione cargos, remova seleções incorretas, reinicie a seleção e salve a ordem escolhida.
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

Use `/config` no servidor. É necessário ter o cargo de liderança configurado ou a permissão de administrador. O painel é privado e tem áreas para configurar WL, hierarquia, farm e cargos principais. Para WL, farm e hierarquia, escolha os canais nos menus do Discord. Foto e banner são configurados por links públicos HTTPS.

- **WL:** título, descrição, canal em que o painel público será publicado e canal que recebe as solicitações para decisão do recrutador.
- **Hierarquia:** título, descrição, quantidade de cargos e seleção de vários cargos por página. A lista é dividida em páginas de até 25 cargos e a ordem é determinada pela posição no Discord: nº 1 é o cargo mais baixo e o último é o mais alto. A publicação gera uma mensagem/embed separada para cada cargo, com o nome do cargo destacado no título.
- **Farm:** título, instruções, canal para publicar o painel, canal de revisão, canal de registros finais e escolha entre aprovação obrigatória ou automática. O jogador seleciona um item ativo, informa a quantidade e envia a imagem de prova no próprio canal; nome e ID são recuperados automaticamente do cadastro aprovado da WL.
- **Painel principal, membros e liderança:** título, descrição e canal de publicação separados para cada painel.
- **Cargos e regras:** cargo de membro, recrutador, liderança, ADV1/ADV2/ADV3, cooldown, limite diário, fuso e opção de PD automático na ADV3.
- **Logs:** destinos separados para logs administrativos, públicos, farm, ADV, PD e blacklist.
- **Publicação:** use os botões na parte inferior do `/config` para publicar WL, hierarquia, farm, painel principal, membros e liderança depois de salvar os campos.

Nos campos de canal/cargo, informe o ID numérico do Discord; use `0` quando a tela permitir deixar um destino sem configurar. O bot precisa ter permissão para ver o canal, enviar mensagens e anexar arquivos. As configurações são salvas na tabela `settings` do SQLite e reaplicadas durante a execução.

### Patentes e apelidos

No botão **Hierarquia**, informe a quantidade de patentes. Na tela seguinte, marque vários cargos ao mesmo tempo; use **Anterior** e **Próxima** para visitar todas as páginas. Clique em **Salvar hierarquia** uma única vez depois de escolher a quantidade exata. A ordem usa a posição dos cargos nas configurações do Discord: nº 1 é a patente mais baixa e o último número é a mais alta. Para mudar a ordem, ajuste a posição dos cargos no servidor Discord.

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
- O comprovante de farm é salvo como imagem. Item e quantidade são informados pelo jogador no fluxo do painel; o bot não tenta interpretar os dados escritos dentro da imagem. O comando `/farm-item` cria/atualiza os itens e `/farm-item-desativar` desativa opções. O comando antigo `/farm` apenas orienta a usar o painel para garantir que todo registro tenha prova.
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


### Painel de configuração atualizado

- O botão **Logs** foi removido do `/config`.
- A configuração em lote da hierarquia usa páginas de até 25 opções diferentes; as seleções são mantidas entre páginas.
- Os cargos são classificados pela posição nativa do Discord. A prioridade 1 é a mais baixa e o maior número é a mais alta. Para facilitar a leitura do canal, a publicação mostra o cargo mais alto primeiro e o cargo nº 1 por último.
- Os botões estão separados entre configuração e publicação para facilitar a navegação.

## Hierarquia por ordem manual

No `/config` → `Hierarquia`, informe a quantidade de cargos (1 a 50), marque os cargos desejados nos menus paginados e salve uma vez. A lista usa a ordem de posição dos cargos no Discord; nº 1 representa o menor cargo. Ao clicar em `Publicar hierarquia`, selecione o canal. O bot publica uma mensagem/embed individual por cargo, com o nome do cargo no título. Cada membro aparece apenas na seção do maior cargo configurado que possui; o mais alto é publicado primeiro e o cargo nº 1 por último.

## Cargos configuráveis de WL e Farm

No comando `/config`:

- **WL → Cargo temporário:** aplicado quando o membro entra no servidor (e como fallback quando envia a WL); permanece se a WL for recusada e é removido quando a WL é aprovada.
- **WL → Cargo após aprovação:** aplicado ao membro quando a WL é aprovada.
- **WL → Cargo autorizado a aprovar/recusar:** controla quem pode decidir uma solicitação. Administradores também podem decidir.
- **WL → Cargo de recrutadores:** define quais membros podem aparecer na lista de recrutadores para seleção.
- **Farm → Cargo autorizado a aprovar/recusar:** controla quem pode analisar comprovantes. Administradores também podem decidir.

As escolhas são salvas no SQLite e permanecem após reiniciar o bot. O cargo temporário é aplicado automaticamente a novos membros; para membros que já estavam no servidor, também é aplicado quando enviam a solicitação de WL.
