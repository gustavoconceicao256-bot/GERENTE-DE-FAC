# Guia de configuração do FAC-GERAL

## Painel `/config`

O comando abre um painel privado para liderança ou administradores. Os módulos disponíveis no painel são **WL**, **Hierarquia**, **Farm**, **Selecionar cargos** e **Logs**. As alterações são salvas no SQLite.

### WL

No botão **WL**, configure título e descrição, além de foto/miniatura e banner usando URLs públicas HTTPS. Depois do formulário, escolha nos menus do Discord o canal onde o painel será publicado e o canal que recebe as solicitações. O recrutador escolhido continua sendo o único que pode decidir a solicitação, com prazo de 5 minutos.

### Hierarquia

Configure título e descrição. Em seguida, selecione o canal de publicação no menu de canais e selecione os cargos que devem aparecer na hierarquia usando o seletor nativo de cargos. Ao salvar, o bot gera uma sigla a partir do nome de cada cargo e usa a posição do cargo no Discord como prioridade; cargos mais altos aparecem primeiro e a sigla é usada para calcular o nickname.

### Farm

Configure título e texto de instruções, foto/miniatura, banner e se o comprovante exige aprovação. Depois escolha o canal onde o painel será publicado e o canal onde a liderança receberá os comprovantes para análise. Se a aprovação for obrigatória, a liderança pode aprovar ou recusar; a recusa exige motivo.

### Selecionar cargos

Use os menus do Discord para selecionar o cargo padrão de membro, o cargo autorizado a recrutar WL e o cargo de liderança. Não é necessário digitar ou copiar IDs de cargos.

### Logs

Os canais de logs administrativos, públicos e de farm permanecem disponíveis no botão **Logs**. Esses campos ainda aceitam IDs numéricos de canais.

## Publicar os painéis

Depois de configurar os módulos, use os botões **Publicar WL**, **Publicar hierarquia** ou **Publicar Farm** no painel `/config`. O bot precisa ter permissão para ver e enviar mensagens nos canais escolhidos. Para foto e banner, use links públicos HTTPS que apontem diretamente para imagens; deixe os campos vazios para remover as imagens.

## Render

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- `DISCORD_TOKEN`: token do bot, mantido somente nas variáveis secretas do Render.
- `DISCORD_GUILD_ID`: ID do servidor principal.
- Ative as intents Server Members e Message Content no Developer Portal.
- O SQLite é um arquivo local. Em ambiente com disco efêmero, configure disco persistente para não perder configurações e comprovantes entre recriações.
