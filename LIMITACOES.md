# Limitações explícitas

- **BLOCKED — upload live:** não foi anexada uma imagem na conta real. Fazer isso altera o estado externo; permaneceu bloqueado sem uma confirmação específica na interface.
- **BLOCKED — geração live paga:** nenhum vídeo foi solicitado ao Google e nenhum crédito foi gasto. O fluxo só deve ser marcado VERIFIED depois de execução observada com consentimento e MP4 validado.
- **VERIFIED somente leitura — conta:** a instância original ativa informou `arthurcostacopy@gmail.com`, igual à conta configurada. Isso não comprova disponibilidade futura de modelos nem saldo no instante de uma geração.
- **MOCK_ONLY — integração E2E:** o provider fake prova a orquestração local, não seletores ou mudanças futuras do Google Flow.
- **VERIFIED local — login GPT:** o estado não lê mais `~/.codex/auth.json`. Ele só fica conectado depois que um Chrome dedicado abre `chatgpt.com`, o perfil é reaberto e o compositor autenticado é observado. O roteiro segue `miuuyy/codex-chatgpt-web` no commit `fc701f6fbcff9fb65f679445389aca0c60410271`.
- **BLOCKED — imagem real no ChatGPT Web:** o transporte estreito por navegador, anexos e captura do resultado foram implementados, mas uma imagem real não foi solicitada nesta validação porque isso consumiria franquia. O próprio repositório de referência não declara geração de imagem como turn browser suportado; por isso esta parte permanece explicitamente não verificada até o usuário confirmar e executar na interface.
- O contrato privado de upload é deliberadamente fixado a `gflow-cli==0.103.1`; outra assinatura bloqueia a operação em vez de tentar um fallback inseguro.
- Os primeiros 12 players validados são ativados na tela para limitar memória; o ZIP inclui todos os artefatos selecionados.
- O bootstrap precisa de internet na primeira instalação. A execução do atualizador sobre a pasta original deve ocorrer apenas com a versão antiga encerrada.
