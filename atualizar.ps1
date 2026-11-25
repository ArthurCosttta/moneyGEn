$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('Primeiro encerre a versao antiga pelo botao Encerrar. Depois selecione a pasta antiga que contem INICIAR.bat e engine.py. Seus dados, sessao e videos serao preservados.','Atualizar Flow Local') | Out-Null
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Pasta antiga do Flow Local (onde estao INICIAR.bat e engine.py)'
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
    $destination = $picker.SelectedPath
    if (-not (Test-Path -LiteralPath (Join-Path $destination 'engine.py')) -or -not (Test-Path -LiteralPath (Join-Path $destination 'INICIAR.bat'))) {
        throw 'A pasta selecionada nao parece ser a instalacao antiga do Flow Local.'
    }
    $activePort = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($activePort) { throw 'O aplicativo antigo ainda esta aberto. Clique Encerrar na interface e execute este atualizador novamente.' }
    $sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
    $targetRoot = [IO.Path]::GetFullPath($destination).TrimEnd('\')
    if ($sourceRoot -ne $targetRoot) {
        $backupRoot = Join-Path $targetRoot ('backup-codigo-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        $files = Get-ChildItem -LiteralPath $sourceRoot -Recurse -File
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($sourceRoot.Length + 1)
            $parts = $relative -split '[\\/]'
            if (@($parts | Where-Object { $_ -in @('dados','.runtime','saida','__pycache__') -or $_ -like 'backup-codigo-*' }).Count -gt 0) { continue }
            $target = Join-Path $targetRoot $relative
            if (Test-Path -LiteralPath $target) {
                $backup = Join-Path $backupRoot $relative
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
                Copy-Item -LiteralPath $target -Destination $backup -Force
            }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        }
    }
    Write-Host 'Atualizacao aplicada. Sessao, dados e videos preservados.'
    Start-Process -FilePath (Join-Path $targetRoot 'INICIAR.bat') -WorkingDirectory $targetRoot
    exit 0
} catch {
    Write-Host ('ERRO: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
