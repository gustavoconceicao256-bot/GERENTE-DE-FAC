# Guia de configuração do FAC-GERAL

## Painel `/config`

O comando abre um painel privado para liderança ou administradores. Os módulos disponíveis no painel são **WL**, **Hierarquia**, **Farm**, **Selecionar cargos** e **Logs**. As alterações são salvas no SQLite.

### WL

No botão **WL**, configure título e descrição, além de foto/miniatura e banner usando URLs públicas HTTPS. Depois do formulário, escolha nos menus do Discord o canal onde o painel será publicado e o canal que recebe as solicitações. O recrutador escolhido continua sendo o único que pode decidir a solicitação, com prazo de 5 minutos.

### Hierarquia

Configure título e descrição. Em seguida, selecione o canal de publicação no menu de canais e selecione os cargos que devem aparecer na hierarquia usando o seletor nativo de cargos. Selecione vários cargos por página (máximo 25 por menu) e navegue pelas páginas sem perder a seleção. Informe a quantidade exata de cargos antes, depois salve uma vez. A prioridade acompanha a posição nativa do Discord: nº 1 é o cargo mais baixo e o último número é o mais alto. O bot gera uma sigla do nome de cada cargo. Ao publicar, cada cargo recebe sua própria mensagem/embed, em ordem do maior para o menor, e cada membro aparece apenas no cargo mais alto configurado que possui.

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


## Seleção de hierarquia

O seletor de hierarquia é paginado em grupos de até 25 cargos, pois esse é o limite por menu do Discord. Use **Próxima** e **Anterior** para percorrer todos os cargos do servidor; as seleções ficam guardadas entre páginas. A publicação é exibida do cargo de menor posição para o de maior posição no Discord. O botão **Logs** foi removido do painel `/config`.

### Assistente sequencial da hierarquia

Informe a quantidade de cargos (1–50), escolha um cargo para cada posição começando pelo mais baixo e confirme cada escolha. Ao terminar, revise e salve a ordem. Para publicar, escolha um canal no seletor do botão `Publicar hierarquia`. Um membro aparece somente sob o cargo de maior prioridade configurada que possui.
