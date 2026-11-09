# Atualização — fluxo de hierarquia (07/11/2026)

## Correções e melhorias

1. **Etapa 3 — tags em blocos:** ao salvar o modal, o bot edita a mensagem efêmera que abriu o modal. O bloco concluído é substituído pelo próximo bloco pendente, sem criar outra mensagem abaixo.
2. **Botões da etapa 3:** ficaram somente `Definir tag dos cargos`, `Voltar` e `Recomeçar tags`. `Voltar` abre a etapa dos cargos. `Recomeçar tags` limpa apenas as tags do bloco exibido.
3. **Etapa 2 — apagar um cargo:** `Apagar 1` pede o número da posição e libera somente aquela vaga. A ação pode ser repetida quantas vezes forem necessárias; as demais escolhas são preservadas.
4. **Etapa 2 — reorganizar:** `Mudar posição` permite mover um cargo da posição atual para outra posição. A ordem configurada agora é preservada e não é automaticamente reordenada pela posição nativa dos cargos no Discord.
5. **Editar hierarquia:** o guia passou a oferecer `Editar hierarquia` e `Começar do zero`. O menu de edição permite alterar cargos/posições, a tag de qualquer cargo e o canal de publicação sem apagar toda a configuração.
6. **Começar do zero:** após a confirmação, a configuração antiga e as mensagens de hierarquia publicadas pelo bot são removidas; em seguida, o painel solicita a nova quantidade de cargos.
7. **Navegação por modal:** os modais de quantidade, remoção, reorganização e tags atualizam a mensagem original sempre que ela está disponível, evitando telas efêmeras duplicadas.

## Testes

- `python -m compileall -q .` — aprovado.
- `python -m unittest discover -s tests -v` — 36 testes aprovados.

A validação foi estática/local. Não foi possível testar os cliques e modais em um servidor Discord real neste ambiente; a homologação final deve ser feita em um servidor de testes.
