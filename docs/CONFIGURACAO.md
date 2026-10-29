# Guia rápido de configuração

## Primeira inicialização

1. Crie o bot no [Discord Developer Portal](https://discord.com/developers/applications) e copie o token para `DISCORD_TOKEN` em `.env` ou nas variáveis de ambiente do host.
2. Ative **Server Members Intent** e **Message Content Intent** em *Bot → Privileged Gateway Intents*.
3. Ative o modo Desenvolvedor no Discord para copiar IDs de servidor, canais e cargos.
4. Defina `DISCORD_GUILD_ID` no ambiente para sincronizar os comandos no seu servidor.
5. Convide o bot com permissões adequadas e coloque o cargo dele acima dos cargos que precisa gerenciar.
6. Instale dependências com `pip install -r requirements.txt` e inicie com `python bot.py`.
7. No Discord, execute `/config` como administrador para configurar as opções principais.

## Painel `/config`

O painel é privado e somente pode ser usado pela liderança configurada ou por administradores. Ele contém:

- **Geral:** nome da facção e link de convite.
- **Cargos:** membro, recrutador, liderança, ADV1 e ADV2.
- **Canais:** painel de WL, pedidos de WL e canais de log administrativo, público e de farm.
- **Farm / ADV3:** cargo ADV3, cooldown, limite diário, fuso horário e opção de PD automático na ADV3.
- **Hierarquia:** cargos, siglas de nickname e prioridade.

Informe IDs numéricos do Discord, não nomes nem menções. Use `0` para desativar um ID quando permitido. Depois de salvar, execute `/config` outra vez para conferir as alterações. Os valores são salvos na tabela `settings` do SQLite e reaplicados quando o bot reinicia.

## Patentes e apelidos

Na seção **Hierarquia**, informe uma ou mais entradas separadas por ponto e vírgula, no formato:

```text
ID_CARGO=SIGLA=PRIORIDADE; ID_CARGO=SIGLA=PRIORIDADE
```

Exemplo fictício:

```text
123456789012345678=M=10; 234567890123456789=DIR=20; 345678901234567890=CEO=30
```

Uma prioridade maior representa uma patente superior. A sigla da maior patente entre os cargos do membro é usada ao criar/definir o nickname. Ao salvar essa lista, ela substitui a lista hierárquica anterior.

## Render e armazenamento

Para um Background Worker, use:

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`

Se você já criou um **Web Service**, esta versão também abre `GET /` e `GET /health` na porta `PORT` informada pelo Render, apenas para o healthcheck. Não é preciso cadastrar manualmente `PORT`. Um Background Worker continua sendo a opção recomendada para um bot que não precisa servir páginas.

Confirme que as variáveis do `.env.example` estão configuradas no painel do host. O banco SQLite fica no caminho de `DATABASE_PATH` (padrão `fac_control.sqlite3`). Em hospedagens sem armazenamento persistente, o banco pode ser perdido em um novo deploy/recriação. Use um disco persistente ou banco adequado e faça backups.

### Erro `403 Forbidden (50001): Missing Access` ao sincronizar comandos

Esse erro geralmente indica que `DISCORD_GUILD_ID` não é o ID do servidor certo, que o bot não está nesse servidor ou que foi convidado sem o escopo `applications.commands`. Verifique o token e gere um convite com os escopos `bot` e `applications.commands`. Se precisar iniciar para diagnosticar, coloque `DISCORD_GUILD_ID=0` para usar a sincronização global; ela pode demorar a aparecer no Discord. A versão atual tenta esse fallback e mantém o processo ativo, mesmo que o Discord recuse a sincronização.

## Antes de colocar em produção

- Teste aprovação, recusa, expiração de WL e envio de DM com contas de teste.
- Teste envio e recuperação de comprovantes de imagem.
- Confirme permissões e hierarquia dos cargos.
- Não envie `.env`, tokens, banco de produção ou comprovantes ao GitHub.
