# Relatório de auditoria e validação — 02/11/2026

## Escopo

Revisão do projeto `GERENTE-DE-FAC` recebido no ZIP, preservando a arquitetura e os recursos existentes. O código principal permanece em `bot.py`; os testes automatizados estão em `tests/`.

## Correções realizadas nesta revisão

1. **Formatação de mensagens Components V2**
   - Corrigidas sequências de escape duplicadas que produziam `\\n` literal em vez de quebras de linha reais.
   - A correção alcança o texto de painéis, campos, mensagens de WL, resultados do Farm, nomes/tags e publicação da hierarquia.
   - Adicionada asserção de regressão para impedir que `\\n` literal volte a aparecer na renderização de painéis.

2. **Ciclo de vida / inicialização**
   - `setup_hook` estava decorado com `@bot.event`. No `discord.py`, `setup_hook` é um método de ciclo de vida e não um evento despachado dessa forma.
   - Removido o decorator e instalado o callback diretamente em `bot.setup_hook`, garantindo que o código de inicialização seja chamado pelo cliente.
   - Adicionado teste estático para impedir regressão.

## Verificações executadas

| Verificação | Resultado |
|---|---|
| `python -m unittest discover -s tests -v` | **29 testes passaram** |
| `python -m compileall -q .` | **Aprovado** |
| Auditoria estática de ausência de `discord.Embed` | **Aprovado** |
| Auditoria estática de ausência de Views legadas | **Aprovado** |
| Verificação dos envios/edições com LayoutView | **Aprovado pelos testes estáticos** |
| Testes de schema SQLite e inicialização idempotente | **Aprovado** |
| Testes de ordenação e prontidão da hierarquia | **Aprovado** |
| Testes do parser `Retry-After` | **Aprovado** |

## Validação não realizada neste ambiente

A instalação de dependências com `pip install -r requirements.txt` não foi concluída porque o ambiente de execução não conseguiu resolver/acessar o PyPI (falha de DNS). O pacote `discord.py` não estava instalado no ambiente. Por isso, não foi possível inicializar uma sessão real do cliente Discord nem validar a serialização final dos componentes contra a API.

Também dependem de homologação em servidor Discord real:
- permissões efetivas do bot e hierarquia de cargos;
- interações de botões, menus, modais e mensagens efêmeras;
- envio e recebimento de anexos/imagens do Farm;
- DMs, aprovação/recusa WL e aplicação de cargos;
- publicação e substituição de painéis;
- comportamento real da API em HTTP 429.

Esses itens não são declarados como testados. Os testes automatizados são locais e não substituem a homologação no Discord.

## Próximos passos para homologação

1. Use Python 3.12 (versão indicada em `.python-version`).
2. Instale as dependências com `python -m pip install -r requirements.txt`.
3. Copie `.env.example` para `.env` e informe um token válido e os IDs reais.
4. Ative **Server Members Intent** e **Message Content Intent** no Developer Portal.
5. Convide o bot com os escopos `bot` e `applications.commands`, concedendo apenas as permissões necessárias.
6. Execute `python bot.py` e valide os fluxos em um servidor de testes antes de usar em produção.

## Segurança

O ZIP não inclui `.env`, token de bot nem banco de dados local. Nunca compartilhe o token. Se ele já tiver sido publicado, revogue-o e gere outro no Discord Developer Portal.
