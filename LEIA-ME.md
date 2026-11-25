# Flow Local 3.0 para Windows

Aplicativo local para preparar e acompanhar lotes no Google Flow por meio de `gflow-cli==0.103.1`. Não é um produto oficial do Google. A interface tem somente três destinos: **Criar**, **Conferir** e **Vídeos**.

## Atualizar sem perder sessão, dados ou vídeos

1. Na instalação antiga, termine qualquer operação e clique **Encerrar**.
2. Extraia este ZIP em uma pasta nova.
3. Abra `ATUALIZAR_EXISTENTE.bat` e selecione a pasta antiga que contém `INICIAR.bat` e `engine.py`.
4. O atualizador guarda backup do código substituído e não copia, remove nem sobrescreve `dados`, `.runtime` ou `saida`.

O perfil Google dedicado continua em `dados/flow`. Nunca envie essa pasta a terceiros. Também é possível copiar manualmente o código novo sobre a instalação antiga com o aplicativo encerrado, preservando as três pastas acima.

## Iniciar

Abra `INICIAR.bat`. Na primeira instalação, o bootstrap instala em `.runtime` o Python 3.12, `gflow-cli==0.103.1` e dependências fixadas. O servidor escuta exclusivamente em `127.0.0.1:8765`, usa token local e abre `#/criar` no navegador.

`SOMENTE_TESTE.bat` inicia com trava de gastos no backend. Mesmo uma chamada direta à API é recusada nesse modo.

## Fluxo

1. Em **Criar**, cole prompts. Uma linha contendo exatamente `---` separa takes. Adicione imagens ou importe até 20 JSONs. A prévia/rascunho não envia nada ao Google.
2. Em **Conferir**, escolha modelo, formato, duração, resolução e pasta absoluta. Clique **Gerar agora**. O preflight e o plano são verificados automaticamente antes de abrir qualquer confirmação de gasto.
3. No modal, marque as três confirmações. A primeira autorização inclui exatamente um take. O backend persiste a intenção antes do envio e não repete automaticamente um efeito incerto.
4. Em **Vídeos**, assista ao MP4 validado. Somente depois faça uma nova aprovação explícita dos itens restantes. Falhas ou recusas anteriores não entram automaticamente.

O custo pode ser desconhecido. Nessa situação a interface exige aceite específico. Alterar o lote invalida o plano. Arquivo de saída existente bloqueia a execução; nada é sobrescrito.

## Upload corrigido

O upload de referência usa `FlowApiClient.upload_reference_in_place` no compositor do transporte `ui_automation`. As assinaturas da versão instalada são conferidas antes da chamada. Se essa rota não estiver disponível, o lote é bloqueado com `UPLOAD_ROUTE_UNAVAILABLE`; não existe fallback silencioso para o upload REST legado.

Todas as referências são confirmadas antes da primeira geração. A conta é revalidada antes dos uploads e novamente imediatamente antes de cada envio pago.

## ChatGPT Web e criação de imagens

**Entrar no GPT** abre um Chrome normal em um perfil separado, salvo em `dados/gpt-web`. Entre na conta e espere o campo de mensagem aparecer; a conexão é detectada automaticamente na mesma janela. O programa só mostra **Sair do GPT** quando o compositor autenticado for realmente encontrado. Essa sessão não usa o Codex nem `~/.codex/auth.json`.

Em **Criar**, o botão **Criar imagem** abre o prompt, permite até quatro referências e exige a confirmação explícita de possível consumo da franquia. A execução ocorre no ChatGPT Web visível; ao concluir, a imagem é copiada para a Biblioteca e anexada ao prompt. Como a validação de entrega não gastou franquia, a geração real permanece `BLOCKED` até uma execução confirmada pelo usuário.

## JSON canônico

O schema está em `schemas/lote.schema.json` e o exemplo em `exemplos/exemplo_lote.json`. O parser exige UTF-8 estrito, rejeita chaves repetidas, `NaN`/`Infinity`, campos desconhecidos, nomes de saída inseguros e imagens adulteradas. Limites: 1.000 itens por lote, 20 MiB e 40 MP por imagem.

## Segurança e privacidade

- Nenhum campo recebe senha Google; autenticação acontece no Chrome/Google.
- Host, Origin, token, MIME, tamanho, magic bytes e caminhos são validados.
- Prompts viram argumentos/dados, nunca texto de shell.
- O worker tem concorrência 1, fila persistente SQLite e recuperação conservadora.
- MP4 só aparece como válido depois de `ffprobe` e decodificação integral por `ffmpeg`.
- Traces autenticados, cookies, `dados`, `.runtime` e vídeos do usuário não fazem parte deste ZIP.

Automação não oficial pode violar termos do Google e gerar restrições. O usuário assume esse risco ao confirmar a ação paga.

## Evidências e limitações

Consulte `RELATORIO_DE_TESTES.md`, `LIMITACOES.md` e `ACEITE_NO_WINDOWS.md`. `VERIFIED` significa que o comportamento foi executado e observado; `MOCK_ONLY` usa o provider fake explícito; `BLOCKED` não foi executado.
