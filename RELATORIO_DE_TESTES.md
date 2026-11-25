# Flow Local 3.0 — relatório de testes

Data: 24/11/2026. Ambiente: Windows, Python 3.12, Node 24.16.0, Playwright, Chrome/Chromium e ffmpeg.

| Evidência | Resultado | Status |
|---|---:|---|
| Suíte Python backend/HTTP/serviço | 59/59 | VERIFIED |
| Regressão Python v3 | 9/9 | VERIFIED local |
| Playwright E01–E28 | 28/28 | VERIFIED em fixture |
| Fluxos visuais 390/768/1440 | 3/3 | VERIFIED em fixture |
| Capturas obrigatórias | 27/27 inspecionadas | VERIFIED em fixture |
| workers / retries | 1 / 0 | VERIFIED |
| Provider dos E2E | FakeFlowAdapter identificado | MOCK_ONLY |
| Conta da instalação original | `arthurcostacopy@gmail.com` = esperada | VERIFIED somente leitura |
| Upload real no compositor | não executado | BLOCKED |
| Login sem sessão Codex | marcador exclusivo do ChatGPT Web + teste E28 | VERIFIED local/fixture |
| Geração de imagem real no ChatGPT Web | não executada; nenhuma franquia usada | BLOCKED |
| Geração paga real | não executada; nenhum crédito usado | BLOCKED |

O E2E usa UI, HTTP, serviço, SQLite, worker, filesystem, ffprobe/ffmpeg e publicação reais. Somente a fronteira do provider é substituída por um fake determinístico. Não há `page.route` para simular a API principal.

Foram comprovados em fixture: fila persistente, prompts exatos, importação/validação, plano e preflight, conta incorreta sem efeitos, `--no-spend`, aprovação de um teste, nova aprovação dos restantes, nenhuma repetição automática, validação integral do MP4, player autenticado, Range HTTP, ZIP, revisão após F5, três telas e ausência de overflow nos viewports medidos.

Durante a execução foram encontradas e corrigidas duas falhas: módulo `state.js` ausente da allowlist estática e coluna `id` ambígua na consulta de artefato. A inspeção visual também encontrou a barra de ação cobrindo campos em 390/768 px; ela deixou de ser sobreposta. Todas as suítes foram repetidas depois das correções.

O Playwright executa 31 casos (28 E2E e três execuções visuais por viewport), com `workers=1` e `retries=0`. Traces brutos ficam somente no diretório de trabalho e não entram na distribuição porque podem conter token local.
