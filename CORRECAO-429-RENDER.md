# Correção do HTTP 429 no Render

## Ação de segurança obrigatória antes do deploy

O repositório publicado continha uma credencial real em `.env.example`. Considere-a comprometida:

1. No Discord Developer Portal, abra a aplicação > Bot > Reset Token.
2. No Render > Environment, substitua `DISCORD_TOKEN` pelo token recém-gerado.
3. Nunca coloque o token em `.env.example`, README, código ou mensagens.
4. O token antigo deve ser considerado inválido mesmo que o arquivo público seja apagado. Se possível, remova também o segredo do histórico Git.

## O que o patch faz

- Não tenta login repetidamente a cada 30 segundos.
- Respeita `Retry-After` quando o Discord o informa.
- Se o servidor não fornecer prazo, usa espera progressiva: 10, 20, 40 minutos, até 6 horas.
- Erro de token inválido (401/LoginFailure) não entra em loop de tentativas.
- Remove credenciais/IDs reais de `.env.example` e exclui bytecode do ZIP.

## Render

- Para bot Discord, prefira **Background Worker**.
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Mantenha somente um serviço/instância executando esse token.
- Configure `DISCORD_TOKEN` em Environment, não em arquivo versionado.
- Depois do deploy, deixe o processo ativo sem clicar repetidamente em Restart/Deploy.

## Como interpretar o 429

Um 429 no `GET /users/@me` ocorre antes de os comandos e eventos do bot iniciarem. Não é causado pelo conteúdo dos comandos slash em si. O Discord recomenda respeitar `Retry-After` e diferencia limites por rota, globais e restrições temporárias por excesso de requisições inválidas. Se o bloqueio persistir após a rotação do token e uma pausa, aguarde o período indicado pela API e contate o suporte de desenvolvedores do Discord com os horários e cabeçalhos de resposta (sem compartilhar o token).

Documentação: https://docs.discord.com/developers/topics/rate-limits
