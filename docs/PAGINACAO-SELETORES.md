# Paginação de seletores de cargos e canais

## O que mudou

Os menus de configuração de cargos e canais agora usam um seletor paginado próprio em vez de depender de um menu nativo que mostra somente a primeira página de opções.

O seletor compartilhado `PaginatedGuildObjectPickerView` atende a:

- cargos do servidor;
- canais de texto;
- categorias de canais (usadas na configuração dos tickets).

Cada página mostra no máximo 25 opções, respeitando o limite do Discord. Quando houver mais opções, aparecem os botões **◀ Anterior** e **Próxima página ▶**. O seletor também mostra a página atual, a quantidade total de opções e o item selecionado.

## Onde a paginação está disponível

- Configuração geral de cargos;
- configuração dos canais da whitelist;
- cargos temporários, cargo de cidadão e cargos autorizados da whitelist;
- configuração dos canais e do cargo de aprovação do Farm;
- seleção de cargos e ordem da hierarquia;
- seleção do canal de publicação da hierarquia;
- configuração dos canais, categorias e equipes de Suporte, Denúncia e Outros no módulo de Tickets.

Os fluxos de whitelist e Farm que já tinham paginação continuam com seus próprios controles. A seleção de recrutador também mantém suas setas para servidores com mais de 25 recrutadores.

## Comportamento

1. As setas avançam e voltam sem salvar uma opção diferente.
2. A escolha feita na página atual é mantida ao navegar.
3. Quando um campo já tem valor salvo, o seletor abre na página que contém esse cargo/canal e o marca como selecionado.
4. A configuração só é gravada ao clicar em **Confirmar seleção** ou no botão de salvar da tela correspondente.
5. O botão **Voltar** cancela a seleção sem alterar o valor salvo.
6. Nos campos de cargo que permitem desconfiguração, **Limpar seleção** remove apenas aquele valor.
7. Apenas a pessoa que iniciou a configuração pode interagir com o seletor aberto.

## Validação

O teste automatizado `tests/test_role_channel_pagination.py` verifica o tamanho máximo de 25 opções, a presença dos controles de navegação, os tipos de item suportados, a preservação da seleção e o uso do seletor compartilhado nas telas antigas e no módulo de Tickets.
