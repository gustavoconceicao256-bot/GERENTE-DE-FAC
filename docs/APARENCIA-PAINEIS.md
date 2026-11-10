# Aparência dos painéis — GERENTE-DE-FAC

## Acesso

No Discord, abra `/config` e clique em **Aparência dos painéis**. Escolha um dos painéis disponíveis:

- **WL**
- **Farme**
- **Ticket**

A hierarquia não aparece nesse seletor porque sua publicação é uma lista de cargos/membros, não um painel de ações.

## O que pode ser personalizado

Em cada painel, o editor permite somente:

1. **Cor dos botões** — Azul, Cinza, Verde ou Vermelho.
2. **Descrição** — texto explicativo exibido no painel.
3. **Banner grande** — URL pública HTTPS da imagem horizontal. Deixar o campo vazio remove o banner. A miniatura pequena não faz parte deste editor.

No painel de Tickets, a cor pode ser escolhida separadamente para os botões Suporte, Denúncia e Outros. Na WL e no Farm existe um botão principal, então há uma única escolha de cor.

O Discord não permite cor hexadecimal/RGB arbitrária em botões comuns; por isso a configuração usa os quatro estilos nativos da plataforma.

## Aplicar as alterações

As alterações são salvas no SQLite. Depois de editar descrição, banner ou cores, clique em **Atualizar painel**. O bot atualiza a publicação existente quando conhece a mensagem; para WL/Farm, a primeira atualização também procura a publicação antiga do próprio bot pelo `custom_id` antes de criar outra mensagem.

Para Tickets, a publicação existente é atualizada pelo ID salvo no módulo de tickets. Se o painel ainda não foi publicado, o botão publicará a primeira versão depois que a configuração de canais, categorias e equipes estiver completa.

## Chaves persistidas

- `wl_panel_description`, `wl_panel_banner_url`, `wl_panel_button_style`
- `farm_panel_description`, `farm_panel_banner_url`, `farm_panel_button_style`
- `ticket_panel_description`, `ticket_panel_banner_url`
- `ticket_support_button_style`, `ticket_denuncia_button_style`, `ticket_outros_button_style`

Os estilos de botão aceitos são `primary`, `secondary`, `success` e `danger`.
