# Guia de configuração do FAC Control

## Antes de começar

Use Python 3.10+ e `discord.py>=2.7.1,<3.0.0` para garantir as correções de LayoutView e modais Components V2.

1. Configure `DISCORD_TOKEN` e `DISCORD_GUILD_ID` no ambiente.
2. Ative `Server Members Intent` e `Message Content Intent` no Discord Developer Portal.
3. Conceda ao bot acesso aos canais e as permissões para enviar mensagens, anexar arquivos, ler histórico, gerenciar mensagens, cargos e apelidos.
4. Inicie o bot e execute `/config` no servidor.

As telas, mensagens, seletores, botões e confirmações usam Discord Components V2. Os formulários abrem modais com `Label` e `TextInput`.

## Painel `/config`

O painel é privado. O acesso é permitido a administradores, liderança configurada e desenvolvedores autorizados.

### WL — configuração em etapas

Configure os cargos de:
- membro aprovado;
- recrutador;
- pessoa autorizada a aprovar ou recusar;
- membro temporário enquanto aguarda a WL (opcional).

Depois configure:
- canal onde o painel público será publicado;
- canal onde a liderança/recrutadores recebem as solicitações.

Os seletores próprios são paginados em blocos de até 25 opções. O cargo temporário é opcional. A solicitação expira após cinco minutos sem decisão. O recrutador selecionado fica registrado na solicitação.

### Hierarquia — quatro etapas

1. **Quantidade:** informe de 1 a 100 cargos, sem ultrapassar a quantidade de cargos selecionáveis do servidor.
2. **Cargos:** selecione os cargos disponíveis. A ordem é calculada pela posição real no Discord, do maior para o menor. Se houver mais de 25 cargos, use os controles de página.
3. **Tags:** o bot carrega as tags existentes e pede somente as pendentes. Cada formulário mostra até cinco cargos; após salvar, o assistente avança automaticamente ao próximo bloco incompleto. O progresso é exibido na tela. A etapa de publicação permanece bloqueada enquanto houver tag pendente.
4. **Canal:** selecione o canal de publicação. Use **Anterior** e **Próxima** quando houver mais de 25 canais. Confirme a publicação quando todos os campos estiverem corretos.

A publicação apresenta os cargos do maior para o menor. Para cada membro, o bot considera somente o cargo configurado de maior prioridade ao gerar o apelido `[TAG] NOME RP - ID`.

O assistente reaproveita a hierarquia já salva quando os cargos ainda existem no servidor. O botão de apagar configuração solicita confirmação e remove somente as mensagens de hierarquia enviadas pelo bot no canal configurado.

### Farm

Configure:
- canal público onde ficará o painel de registro;
- canal privado da liderança;
- cargo autorizado a aprovar, pagar ou recusar;
- canal opcional de histórico final.

Cadastre itens com `/farm-item`, defina preço com `/farm-preco` e desative opções com `/farm-item-desativar`.

O membro deve ter cadastro ativo pela WL. No painel, seleciona o item, informa a quantidade e envia a imagem de prova no canal público. A liderança analisa a prova no canal privado. Quando aplicável, a confirmação de pagamento solicita o envio da imagem do comprovante, que é salva no banco e encaminhada ao membro.

### Outros painéis e recursos

Os comandos `/painel`, `/painel-membros` e `/painel-lideranca` publicam as telas correspondentes. `/perfil`, `/membros`, `/farm-resumo`, `/farm-ranking`, `/logs` e `/conquistas` apresentam consultas em Components V2.

Os canais de logs administrativos, públicos, ADV, PD, blacklist e Farm são definidos pelas variáveis de ambiente e/ou configurações salvas. O comando `/logs` consulta o histórico registrado no SQLite.

## Paginação

O Discord limita menus de seleção próprios a 25 opções. Os seletores de recrutadores, itens de Farm, cargos de hierarquia, remoção de cargos, destinos de publicação e campos de configuração dividem os resultados em páginas. Use os botões **Anterior** e **Próxima** para navegar; a seleção e o progresso permanecem salvos entre as páginas.

Seletores nativos de cargo/canal do Discord permitem pesquisa pelo próprio cliente e não precisam ser convertidos em listas manuais.

## Apelidos e permissões

O bot monta o apelido com a tag do cargo de maior prioridade, o nome RP e o ID. O cargo do bot deve ficar acima dos cargos que ele gerencia. Para alterar apelidos, habilite `Manage Nicknames`; para atribuir ou remover cargos, habilite `Manage Roles`.

Para publicar ou substituir a hierarquia, conceda `View Channel`, `Send Messages`, `Read Message History` e `Manage Messages` no canal escolhido.

## SQLite e hospedagem

`DATABASE_PATH` define o arquivo SQLite. Em ambiente de hospedagem com disco efêmero, configure um disco persistente. O arquivo contém configurações, membros, registros de Farm, comprovantes e logs.

No Render:
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Mantenha `DISCORD_TOKEN` somente como variável secreta.
- Configure um disco persistente para o banco.

## Diagnóstico

Execute:

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
```

O bot registra erros de interação no log e responde com uma mensagem V2 genérica. O `discord.py` gerencia os limites de requisição do Discord; em falhas de login com `Retry-After`/HTTP 429, o processo usa espera automática com backoff.
