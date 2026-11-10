# Relatório de validação — Aparência dos painéis

## Implementado

- Botão **Aparência dos painéis** no painel `/config`.
- Seleção de painel entre WL, Farme e Tickets. Hierarquia deliberadamente excluída.
- Editor limitado a cores de botões, descrição e banner grande.
- Cores individuais para Suporte, Denúncia e Outros no painel de Tickets.
- Persistência das opções no SQLite.
- Atualização da publicação existente de Tickets pelo ID salvo.
- Armazenamento de IDs de publicação WL/Farme e tentativa de localizar mensagens antigas do próprio bot pelo `custom_id` antes de criar uma nova publicação.
- Remoção do atalho antigo de aparência da etapa final da WL e centralização do fluxo visual no menu novo.
- O fluxo inicial de Farm segue para configuração de canais/equipe; a aparência fica no menu central.

## Verificações executadas

- `python -m py_compile bot.py` — aprovado.
- `python -m compileall -q .` — aprovado.
- `python -m unittest discover -s tests -v` — **78 testes aprovados**.
- Integridade do ZIP validada com `unzip -t`.

## Limitações

O ambiente de validação não possui `discord.py` instalado e não está conectado a um servidor Discord. Portanto, não foi possível executar interações reais de componentes, abrir modais no cliente Discord ou validar permissões ao vivo. Os testes automatizados são estáticos/lógicos e não substituem um teste no servidor de homologação.

Os botões comuns do Discord aceitam somente os quatro estilos nativos (Azul, Cinza, Verde e Vermelho); não aceitam cor HEX/RGB arbitrária. O banner é configurado por URL pública HTTPS.
