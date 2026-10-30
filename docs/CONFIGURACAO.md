# Guia de configuração do FAC-GERAL

## Painel `/config`

O comando abre um painel privado para liderança ou administradores. Os módulos disponíveis no painel são **WL**, **Hierarquia**, **Farm**, **Selecionar cargos** e **Logs**. As alterações são salvas no SQLite.

### WL

No botão **WL**, configure título e descrição, além de foto/miniatura e banner usando URLs públicas HTTPS. Depois do formulário, escolha nos menus do Discord o canal onde o painel será publicado e o canal que recebe as solicitações. O recrutador escolhido continua sendo o único que pode decidir a solicitação, com prazo de 5 minutos.

### Hierarquia

Configure título e descrição. Em seguida, selecione o canal de publicação no menu de canais e selecione os cargos que devem aparecer na hierarquia usando o seletor nativo de cargos. Selecione vários cargos por página (máximo 25 por menu) e navegue pelas páginas sem perder a seleção. Informe a quantidade exata de cargos antes, depois salve uma vez. A prioridade acompanha a posição nativa do Discord: nº 1 é o cargo mais baixo e o último número é o mais alto. O bot gera uma sigla do nome de cada cargo. Ao publicar, cada cargo recebe sua própria mensagem/embed, em ordem do maior para o menor, e cada membro aparece apenas no cargo mais alto configurado que possui.

### Farm

Configure título e texto de instruções, foto/miniatura, banner e se o comprovante exige aprovação. Depois escolha o canal onde o painel será publicado, o canal onde a liderança receberá os comprovantes para análise, o cargo autorizado a decidir e o canal que receberá os registros finais.

O jogador clica em **REGISTRAR FARM**. O bot identifica o Discord ID e consulta o cadastro ativo em `members`, criado pela WL aprovada. Em seguida, o jogador seleciona um item ativo cadastrado, informa a quantidade e envia a imagem de prova como anexo no mesmo canal do painel. O bot monta o registro com nome, ID FiveM, item, quantidade, pontos, Discord e imagem. Com aprovação obrigatória, só contabiliza os pontos após aprovação; com aprovação automática, contabiliza ao receber a prova. A imagem e os dados ficam salvos em `farm_proofs` e os registros contabilizados em `farm_entries`.

A liderança configura os itens com `/farm-item` (nome, pontos por unidade e meta) e pode desativá-los com `/farm-item-desativar`. O comando legado `/farm` não contabiliza registros sem prova: ele orienta o uso do painel.

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

### Cargos de WL e Farm

Abra `/config` e entre em **WL** para selecionar separadamente: cargo temporário antes da aprovação, cargo adicionado após aprovação, cargo autorizado a aprovar/recusar e cargo cujos membros aparecem na seleção de recrutadores. O cargo temporário é aplicado a novos membros e removido quando a WL é aprovada; em caso de recusa, permanece.

Em **Farm**, selecione o cargo autorizado a aprovar ou recusar comprovantes. Administradores continuam podendo realizar essas ações. As configurações ficam salvas no banco SQLite.

## Seleção de cargos e canais por ID

Nos painéis de configuração, use os menus do Discord quando o cargo/canal estiver disponível. Se o Discord não listar o item desejado, use **Informar ID manualmente**, cole o ID numérico e o bot validará se ele pertence ao servidor e se é do tipo correto.

## Configuração da hierarquia

A configuração não solicita mais uma quantidade de cargos. Selecione os cargos nas páginas, use **Remover cargo selecionado** para corrigir uma escolha, **Adicionar cargo por ID** quando o cargo não aparecer no menu, **Reiniciar configuração** para limpar a hierarquia e **Salvar hierarquia** para confirmar. A ordem de seleção define a hierarquia: primeiro cargo = base; último cargo = topo.

A publicação cria uma mensagem limpa por cargo, com o nome do cargo, quantidade de membros, menções dos membros (ou aviso de que ninguém possui o cargo) e horário da última atualização.
