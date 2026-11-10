# Apelido automático após aprovação da WL

Quando uma WL é aprovada, o GERENTE-DE-FAC aplica o cargo definido em `/config` → **Whitelist**, salva o membro como ativo no banco de dados e sincroniza o apelido com a tag definida na hierarquia.

## Formato

`[TAG] Nome RP | ID`

Exemplo: `[M] gustavo | 2908`. O nome mantém a grafia informada na WL. O apelido respeita o limite de 32 caracteres do Discord: o nome pode ser encurtado, mas a tag e o ID são preservados.

## Configuração obrigatória

1. Em `/config` → **Whitelist**, escolha o cargo que será aplicado após a aprovação.
2. Em `/config` → **Hierarquia**, inclua esse mesmo cargo na Etapa 2.
3. Na Etapa 3, informe a tag que deverá aparecer no início do apelido.
4. Conclua a configuração e publique a hierarquia.

Se o membro tiver mais de um cargo configurado na hierarquia, será usada a tag do cargo com maior prioridade. Alterações posteriores de cargos também atualizam o apelido. Se a tag/cargo ainda não estiver configurado, o bot usa a tag padrão `M`.

## Permissões

O bot precisa da permissão **Gerenciar apelidos** e sua posição na hierarquia de cargos deve estar acima dos cargos dos membros. A intenção **Server Members Intent** deve estar habilitada no Discord Developer Portal.

## Validação

Execute `python -m unittest discover -s tests -v` para validar o formato, o limite de 32 caracteres, a seleção da tag do cargo concedido na aprovação e a ordem de gravação/sincronização.
