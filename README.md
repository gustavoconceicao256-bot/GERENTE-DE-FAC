# FAC Control — Bot de gerenciamento para Discord

Bot em Python para administração de facção FiveM RP: whitelist, hierarquia, membros, farm, ranking, ações administrativas e logs.

## Interface Discord

Todas as mensagens e painéis do bot são enviados em **Discord Components V2** (`LayoutView` + `Container`, `TextDisplay`, `ActionRow`, seletores e galerias de mídia). Os formulários usam `Label` + `TextInput` em modais. O código não envia mensagens com embeds nem Views tradicionais.

A dependência `discord.py` precisa ser `2.7.1` ou superior para suportar Components V2.

## Recursos

- WL em etapas: seleção de recrutador, formulário, aprovação/recusa por cargo autorizado e prazo de cinco minutos.
- Hierarquia guiada: quantidade de cargos, seleção paginada, tags automáticas em blocos, seleção paginada do canal e publicação do maior para o menor.
- Reaproveitamento das tags/cargos já configurados e pulo automático dos campos concluídos.
- Após aprovação da WL, apelido automático no formato `[TAG] Nome RP | ID`, usando a tag configurada para o cargo de maior prioridade do membro; mudanças posteriores de cargo atualizam a tag.
- Farm com itens configuráveis, preço, quantidade, imagem de prova, aprovação e fluxo de confirmação de pagamento.
- Painéis principal, WL, farm, hierarquia, membros e liderança.
- Perfis, membros, ranking, metas, slots, advertências, PD, blacklist e registros administrativos.
- Logs em canais configuráveis e armazenamento em SQLite.
- Paginação para seletores próprios que ultrapassam o limite de 25 opções do Discord.
- Tratamento de erros nas interações e backoff de login para erros HTTP/429; o cliente HTTP do `discord.py` também aplica o controle de rate limit do Discord.

## Requisitos

- Python 3.10 ou superior.
- `discord.py` 2.7.1 ou superior.
- Aplicação e bot criados no [Discord Developer Portal](https://discord.com/developers/applications).
- Intents **Server Members Intent** e **Message Content Intent** ativadas no portal.

## Instalação

1. Crie um ambiente virtual:

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

2. Instale as dependências:
   ```bash
   python -m pip install -r requirements.txt
   ```

3. Copie `.env.example` para `.env` e configure o token, IDs e canais:
   ```bash
   cp .env.example .env
   ```
   No Windows PowerShell:
   ```powershell
   Copy-Item .env.example .env
   ```

4. Convide o bot com os escopos `bot` e `applications.commands`. Conceda as permissões necessárias: ver canais, enviar mensagens, anexar arquivos, ler histórico, gerenciar mensagens, gerenciar cargos e gerenciar apelidos.

5. Inicie:
   ```bash
   python bot.py
   ```

6. No servidor, use `/config` para configurar os módulos.

**Segurança:** nunca envie seu token no chat, GitHub ou ZIP público. Guarde-o apenas em `.env` ou nas variáveis secretas do provedor de hospedagem. Se ele já foi exposto, regenere-o no Developer Portal.

## Configuração inicial

### `/config`

O painel é privado e restrito à liderança, desenvolvedores autorizados e administradores.

- **WL:** configure cargos de membro, recrutador, aprovador e canal do painel/canal de análise em etapas.
- **Hierarquia:** siga o assistente de quatro etapas descrito abaixo.
- **Farm:** configure canal público, canal privado da liderança, cargo aprovador e canal de histórico.
- **Publicação:** publique os painéis depois de validar os campos obrigatórios.

### Assistente da hierarquia

1. Informe a quantidade de cargos (1 a 100; limitada à quantidade de cargos selecionáveis no servidor).
2. Selecione os cargos na ordem que deseja usar: **Cargo 1 é o topo** e o último é a base. Os menus exibem até 25 opções por página. Use **Apagar 1** para liberar uma posição específica e escolher outro cargo; repita quantas vezes precisar. **Mudar posição** troca a ordem de um cargo sem refazer a seleção.
3. Defina as tags em blocos de até cinco cargos. Ao salvar, o bloco atual é substituído na mesma mensagem pelo próximo bloco pendente. Os únicos botões são **Definir tag dos cargos**, **Voltar** (retorna aos cargos) e **Recomeçar tags** (limpa somente o bloco exibido).
4. Escolha o canal de publicação usando **Anterior** e **Próxima** quando houver mais de 25 canais. A publicação segue a ordem manual configurada.

No guia da hierarquia, **Editar hierarquia** preserva a configuração e permite editar cargos/posições, a tag de qualquer cargo ou o canal. **Começar do zero** pede confirmação e remove a configuração e as mensagens da hierarquia publicadas pelo bot antes de iniciar novamente. A prioridade salva determina o cargo usado no apelido do membro e a posição na publicação.

### Farm

1. Cadastre os itens com `/farm-item`.
2. Defina o preço por unidade com `/farm-preco`.
3. Desative itens com `/farm-item-desativar`.
4. Publique o painel de Farm pelo `/config`.
5. O membro seleciona um item, informa a quantidade e envia uma imagem de prova. A liderança aprova ou recusa no canal privado configurado.

O registro depende do cadastro ativo da WL. A imagem, os dados do item e a decisão são armazenados no SQLite.

## Sistema de Tickets

O painel `/config` agora possui o módulo **Tickets**, com configuração guiada para **Suporte**, **Denúncia** e **Outros assuntos**. Configure o canal do painel, canal privado de logs, categoria e cargo de atendimento para cada tipo; depois clique em **Publicar painel**.

Inclui canais privados, modais, assumir/liberar, fechar/reabrir, transcripts HTML, cópia administrativa nos logs e cópia privada para o autor. Consulte [docs/SISTEMA-DE-TICKETS.md](docs/SISTEMA-DE-TICKETS.md) para o passo a passo e permissões.

O código do Open Ticket original fornecido está preservado em `vendor/open-ticket-reference/` como referência; o módulo em execução é nativo em Python e não inicia um segundo bot Node.js.

## Comandos

- **Painéis:** `/config`, `/painel`, `/painel-wl`, `/painel-farm`, `/painel-hierarquia`, `/painel-membros`, `/painel-lideranca`.
- **Membros e hierarquia:** `/perfil`, `/membros`, `/promover`, `/rebaixar`, `/nickname`, `/slot`, `/slots`.
- **Farm:** `/farm`, `/farm-item`, `/farm-preco`, `/farm-item-desativar`, `/farm-comprovante`, `/farm-resumo`, `/farm-ranking`, `/meta-farm`, `/farm-meta-status`.
- **Administração:** `/acao`, `/adv`, `/pd`, `/blacklist-add`, `/blacklist-check`, `/logs`, `/conquistas`.

Os comandos que alteram cargos, apelidos, punições ou configurações exigem a permissão de liderança ou administrador, conforme a regra de cada comando.

## Banco de dados e hospedagem

O bot usa SQLite. Configure `DATABASE_PATH` para um caminho persistente. Em hospedagens como Render, associe um disco persistente ao arquivo do banco para não perder membros, configurações, comprovantes e logs após recriações.

No Render:
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`
- Defina `DISCORD_TOKEN` e os demais valores como variáveis de ambiente.
- Ative as intents privilegiadas no Developer Portal.

## Testes

Execute a auditoria estática e a verificação de sintaxe:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

Os testes automatizados verificam que as mensagens usam LayoutViews, os formulários usam o adaptador de Labels, os callbacks dos botões são adaptados e os containers respeitam o limite de componentes. A validação final de permissões, publicação e interações deve ser feita em um servidor de teste com o bot instalado.
