# Relatório de validação — Tickets no GERENTE-DE-FAC

## Escopo validado

- Inclusão do módulo **Tickets** no painel `/config`.
- Fluxo guiado para canal do painel, canal de logs, categoria e cargo de atendimento para Suporte, Denúncia e Outros.
- Publicação/atualização do painel sem criar uma nova mensagem a cada publicação no mesmo canal.
- Criação de tickets privados e controle de limite por usuário.
- Ações de assumir/liberar, fechar e reabrir.
- Geração e armazenamento de transcript HTML, com verificação de acesso por autor/equipe.
- Persistência dos botões do painel e dos tickets após reiniciar o processo.
- Criação idempotente das tabelas SQLite sem remover tabelas existentes.

## Verificações executadas

| Verificação | Resultado |
|---|---|
| `python -m py_compile bot.py` | Aprovado |
| `python -m compileall -q .` | Aprovado |
| `python -m unittest discover -s tests -q` | **44 testes aprovados** |
| `zipfile.testzip()` no pacote final | Executado na preparação do ZIP |
| Conferência de `.env`, banco local e caches | Arquivos secretos/bancos/caches não incluídos |

## Limitação do ambiente

O ambiente de preparação não tinha `discord.py` instalado e não conseguiu acessar a rede para instalar as dependências. Portanto, não foi possível iniciar o bot nem executar cliques reais contra a API do Discord. Os testes aprovados cobrem compilação Python, regras locais, estrutura/idempotência do SQLite e verificações estáticas do painel/Components V2. Antes de produção, publique primeiro em um servidor de teste e valide os fluxos de seleção, abertura, permissões e transcript.

## Decisão de arquitetura

O Open Ticket enviado é um aplicativo independente em Node.js/discord.js. Para preservar o GERENTE-DE-FAC como **um único bot, uma única aplicação e um único token**, o processo Node.js original não é iniciado pelo painel Python. O módulo de tickets foi implementado nativamente no GERENTE-DE-FAC com os fluxos essenciais de suporte, denúncia e outros assuntos. O código original e sua licença GPL-3.0 estão preservados em `vendor/open-ticket-reference/` para consulta.

Esta integração não afirma portar todos os parâmetros ou plugins do Open Ticket original.
