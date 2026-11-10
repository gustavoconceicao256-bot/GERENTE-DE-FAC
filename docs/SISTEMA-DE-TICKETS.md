# Sistema de tickets integrado ao GERENTE-DE-FAC

O sistema de tickets foi integrado **nativamente ao bot Python existente**, usando o mesmo processo, aplicação Discord, token e banco SQLite. O Open Ticket original foi mantido em `vendor/open-ticket-reference/` apenas como referência de código e licença; ele não é iniciado em paralelo.

## O que está incluído

- Um painel público com três opções: **Suporte**, **Denúncia** e **Outros assuntos**.
- Abertura por modal com assunto, pessoa/ID envolvido (opcional) e descrição.
- Criação de canal privado dentro da categoria escolhida para aquele tipo.
- Acesso do autor, equipe configurada e bot; `@everyone` fica sem acesso.
- Botão para a equipe assumir ou liberar o atendimento.
- Encerramento pelo autor ou equipe, com motivo opcional.
- Reabertura pela equipe.
- Transcript HTML com mensagens e links dos anexos.
- Cópia administrativa anexada no canal de logs e cópia privada enviada por DM ao autor.
- Transcript guardado no SQLite; `/ticket-transcript NUMERO` permite ao autor ou à equipe responsável baixar sua cópia privada.
- Limite padrão de 2 tickets abertos por usuário, configurável no SQLite para um valor entre 1 e 10 (`ticket_user_limit`).
- Views persistentes: os botões do painel e dos tickets continuam reconhecidos após reiniciar o bot.
- Todas as telas e mensagens de interface usam Discord Components V2.

## Configuração pelo Discord

1. Inicie o GERENTE-DE-FAC normalmente e use `/config`.
2. Clique em **Tickets**.
3. Em **Canal do painel**, selecione o canal onde os membros vão abrir chamados e clique em **Salvar canal**.
4. Em **Canal de logs**, selecione um canal privado da liderança e clique em **Salvar canal**.
5. Configure **Suporte**: selecione a categoria que receberá tickets de suporte e o cargo da equipe de suporte; clique em **Salvar configuração**.
6. Repita a mesma configuração em **Denúncia** e **Outros**.
7. Volte à tela principal de Tickets e clique em **Publicar painel**.

O botão **Publicar Tickets** do painel principal e o comando `/painel-tickets` também publicam/atualizam o painel depois de concluída a configuração.

## Permissões do bot

Conceda ao bot, no servidor e nos canais relevantes:

- Ver canais;
- Enviar mensagens;
- Ler histórico de mensagens;
- Gerenciar canais (necessário para criar, renomear e ajustar permissões dos tickets);
- Anexar arquivos;
- Incorporar links.

O bot também precisa ter acesso à categoria configurada para cada tipo. O canal de logs deve permitir que o bot envie arquivos. **Configure manualmente esse canal para ficar visível apenas à liderança/equipe autorizada**; o assistente avisa sobre isso, mas não altera permissões preexistentes do canal de logs.

## Como funciona o acesso aos transcripts

- O autor recebe uma cópia HTML por mensagem privada.
- A equipe responsável pelo tipo do ticket e administradores consegue usar `/ticket-transcript NUMERO`.
- O canal de logs recebe a cópia administrativa em anexo. Mantenha esse canal privado.
- O arquivo fica guardado na tabela `ticket_transcripts` do SQLite. Use armazenamento persistente no Render para não perder o banco em reinicializações/redeploys.

Se a DM do autor estiver fechada, ele pode usar `/ticket-transcript NUMERO` dentro do servidor para baixar a própria cópia. O comando responde de forma privada (ephemeral).

## Banco de dados e migração

A inicialização cria as tabelas `tickets` e `ticket_transcripts` de forma idempotente. Nenhuma tabela existente de WL, hierarquia ou Farm é removida. Faça backup do arquivo SQLite antes de atualizar o projeto.

## Limites e pontos que ainda exigem validação no servidor

- O transcript inclui até 800 mensagens e links de anexos; não baixa os arquivos anexados para dentro do HTML.
- O canal de logs é configurado pela liderança; a privacidade dele precisa ser conferida manualmente no Discord.
- Não foi possível executar uma sessão real no Discord neste ambiente, pois não havia acesso à rede nem as bibliotecas Discord instaladas. A lógica local foi validada com testes automatizados e compilação Python; permissões, modais e publicação precisam de um teste final no servidor de teste.
- Esta integração não é um port de todos os 350+ parâmetros/plugins do Open Ticket. Ela incorpora os fluxos essenciais pedidos para o GERENTE-DE-FAC, com arquitetura Python/Components V2 e sem iniciar um segundo bot Node.js.

## Referência Open Ticket

O código-fonte original enviado pelo usuário foi copiado sem alterações para `vendor/open-ticket-reference/open-ticket-main/`, incluindo o `LICENSE.md` GPL-3.0. Para uma instalação independente do Open Ticket, consulte o README e a documentação presentes nessa pasta. Ele não deve ser executado em paralelo com o GERENTE-DE-FAC usando o mesmo token.
