# Guia de configuração do FAC-GERAL

## Painel `/config`

O comando abre um painel privado para liderança ou administradores. Cada botão abre um formulário e grava as mudanças no banco SQLite. Use números de IDs de canal/cargo copiados com o Modo Desenvolvedor do Discord.

### Módulos

- **WL:** título, descrição, canal para publicar o painel de candidatura e canal para receber os pedidos de WL. O recrutador escolhido continua sendo o único que pode decidir a solicitação.
- **Hierarquia:** título, descrição, canal de publicação e lista de cargos exibidos. O formato para cada item é `ID_CARGO=SIGLA=PRIORIDADE; ID_CARGO=SIGLA=PRIORIDADE`. Prioridade maior aparece primeiro e define a sigla usada no nickname.
- **Farm:** título, texto de instruções, canal de publicação, canal para revisar comprovantes e aprovação automática ou obrigatória. Se obrigatória, a liderança aprova ou recusa; recusar exige motivo.
- **Painel principal, membros e liderança:** cada um tem seu próprio título, descrição e canal de publicação.
- **Cargos principais:** cargos de membro, recrutador, liderança, ADV1 e ADV2. O ADV3 fica nas regras de farm/ADV3.
- **Logs e punições:** canais separados para logs administrativos/públicos/farm e, opcionalmente, ADV/PD/blacklist. Se um destino específico de ADV/PD/blacklist for `0`, o bot usa o canal de logs administrativos.

Depois de preencher e salvar cada formulário, reabra `/config` para revisar o resumo. Use os botões **Publicar ...** para enviar os painéis aos canais definidos. A configuração de canal deve ser um ID numérico; o bot deve ter acesso ao canal de destino.

## Render

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- `DISCORD_TOKEN`: token do bot, mantido somente nas variáveis secretas do Render.
- `DISCORD_GUILD_ID`: ID do servidor principal.
- Ative as intents Server Members e Message Content no Developer Portal.
- O SQLite é um arquivo local. Em ambiente com disco efêmero, configure disco persistente para não perder configurações e comprovantes entre recriações.
