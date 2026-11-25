param([switch]$NoSpend)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$appRoot = $PSScriptRoot
Set-Location -LiteralPath $appRoot
$runtimeDir = Join-Path $appRoot '.runtime'
$dataDir = Join-Path $appRoot 'dados'
New-Item -ItemType Directory -Force -Path $runtimeDir,$dataDir | Out-Null
$logPath = Join-Path $dataDir 'instalacao.log'
Start-Transcript -Path $logPath -Append | Out-Null

function Run-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Falha ao executar $Executable (codigo $LASTEXITCODE)." }
}

function Find-Chrome {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe')
    )
    if (${env:ProgramFiles(x86)}) { $candidates += Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe' }
    foreach ($reg in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe','HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe')) {
        if (Test-Path $reg) { $candidates += (Get-Item $reg).GetValue('') }
    }
    foreach ($candidate in $candidates) { if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate } }
    return $null
}

try {
    # One local server per folder. Never kill an unrelated process on this port.
    $activePort = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($activePort) {
        $pidFile = Join-Path $dataDir 'servidor.pid'
        $owned = $false
        if (Test-Path -LiteralPath $pidFile) {
            $oldPid = [int](Get-Content -LiteralPath $pidFile -Raw)
            $owned = @($activePort | Where-Object { $_.OwningProcess -eq $oldPid }).Count -gt 0
        }
        if ($owned -and (Test-Path -LiteralPath (Join-Path $dataDir 'abrir.url'))) {
            if ($NoSpend) { throw 'O aplicativo ja esta aberto. Encerre a instancia atual antes de abrir SOMENTE_TESTE.bat.' }
            $pidTime = (Get-Item -LiteralPath $pidFile).LastWriteTime
            $codeModified = @(Get-ChildItem -LiteralPath (Join-Path $appRoot 'app.py'), (Join-Path $appRoot 'backend'), (Join-Path $appRoot 'migrations') -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $pidTime }).Count -gt 0
            if ($codeModified) {
                $urlMatch = Select-String -Path (Join-Path $dataDir 'abrir.url') -Pattern 'token=([^\s&#]+)' | Select-Object -First 1
                if ($urlMatch) {
                    $tok = $urlMatch.Matches[0].Groups[1].Value
                    try {
                        Invoke-WebRequest -UseBasicParsing -Method Post -Uri 'http://127.0.0.1:8765/api/shutdown' -Headers @{ 'X-Flow-Token' = $tok; 'Origin' = 'http://127.0.0.1:8765' } -ContentType 'application/json' -Body '{}' -TimeoutSec 4 | Out-Null
                        Start-Sleep -Seconds 2
                        $stillUp = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
                        if (-not $stillUp) { $owned = $false }
                    } catch {}
                }
            }
            if ($owned) {
                Start-Process -FilePath (Join-Path $dataDir 'abrir.url')
                Stop-Transcript | Out-Null
                exit 0
            }
        }
        if ($owned -or (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {
            throw 'A porta 8765 esta em uso. Feche a outra instancia pelo botao Encerrar e tente novamente.'
        }
    }
    $env:UV_TOOL_DIR = Join-Path $runtimeDir 'tools'
    $env:UV_TOOL_BIN_DIR = Join-Path $runtimeDir 'bin'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtimeDir 'python'
    $env:UV_CACHE_DIR = Join-Path $runtimeDir 'cache'
    $env:UV_INSTALL_DIR = Join-Path $runtimeDir 'uv'
    $env:UV_NO_MODIFY_PATH = '1'
    $env:UV_PYTHON_BIN_DIR = Join-Path $runtimeDir 'python-bin'
    $env:PYTHONUTF8 = '1'
    $env:GFLOW_CLI_TELEMETRY = '0'
    $env:GFLOW_CLI_HOME = Join-Path $dataDir 'flow'
    $env:GFLOW_CLI_UPDATE_CHECK = '0'
    $env:GFLOW_CLI_STORAGE_URI = ''
    $env:DO_NOT_TRACK = '1'
    $uvExe = Join-Path $env:UV_INSTALL_DIR 'uv.exe'
    if (-not (Test-Path -LiteralPath $uvExe)) {
        Write-Host 'Instalando uv pelo instalador oficial da Astral...'
        $installer = Invoke-RestMethod 'https://astral.sh/uv/0.13.9/install.ps1'
        & ([scriptblock]::Create($installer))
        if (-not (Test-Path -LiteralPath $uvExe)) { throw 'O instalador do uv nao criou uv.exe.' }
    }
    Write-Host 'Verificando Python 3.12...'
    Run-Checked -Executable $uvExe -Arguments @('python','install','3.12','--no-bin','--no-registry')
    # uv manages a private Python; find must use exactly the same directory.
    $pythonLines = & $uvExe python find --managed-python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 nao encontrado apos a instalacao.' }
    $pythonExe = [string]($pythonLines | Select-Object -Last 1)
    $pythonExe = $pythonExe.Trim()
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python indisponivel: $pythonExe" }
    $gflowExe = Join-Path $env:UV_TOOL_BIN_DIR 'gflow.exe'
    $cliPython = Join-Path $env:UV_TOOL_DIR 'gflow-cli\Scripts\python.exe'
    $needsInstall = -not (Test-Path -LiteralPath $gflowExe)
    if (-not $needsInstall) {
        $version = & $gflowExe --version 2>&1 | Out-String
        $needsInstall = $LASTEXITCODE -ne 0 -or $version -notmatch 'version\s+0\.103\.1(?:\s|$)'
    }
    if ($needsInstall) {
        Write-Host 'Instalando gflow-cli oficial (0.103.1) e dependencias...'
        Run-Checked -Executable $uvExe -Arguments @('tool','install','--force','--python',$pythonExe,'gflow-cli==0.103.1')
    }
    $browserMarker = Join-Path $runtimeDir 'chromium-0.103.1.ok'
    if ($needsInstall -or -not (Test-Path -LiteralPath $browserMarker)) {
        Write-Host 'Preparando navegador do Playwright...'
        Run-Checked -Executable $cliPython -Arguments @('-m','playwright','install','chromium')
        Set-Content -LiteralPath $browserMarker -Value 'ok'
    }
    if (-not (Find-Chrome)) {
        Write-Host 'Instalando Google Chrome oficial para o login...'
        $chromeInstaller = Join-Path $runtimeDir 'ChromeSetup.exe'
        Invoke-WebRequest 'https://dl.google.com/chrome/install/latest/chrome_installer.exe' -OutFile $chromeInstaller -UseBasicParsing
        $signature = Get-AuthenticodeSignature -LiteralPath $chromeInstaller
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Google LLC') { throw 'Assinatura do instalador do Chrome nao foi validada.' }
        $installChrome = Start-Process -FilePath $chromeInstaller -ArgumentList '/silent','/install' -PassThru
        if (-not $installChrome.WaitForExit(600000)) { throw 'O instalador do Chrome nao terminou em 10 minutos.' }
        if (-not (Find-Chrome)) { throw 'Chrome nao foi instalado. Instale pelo site oficial e abra INICIAR.bat novamente.' }
        Remove-Item -LiteralPath $chromeInstaller -Force
    }
    Write-Host 'Abrindo Flow Local no navegador...'
    $arguments = @(('"' + (Join-Path $appRoot 'app.py') + '"'))
    if ($NoSpend) { $arguments += '--no-spend' }
    $serverProcess = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $appRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDir 'servidor.log') -RedirectStandardError (Join-Path $dataDir 'servidor-erros.log')
    Set-Content -LiteralPath (Join-Path $dataDir 'servidor.pid') -Value $serverProcess.Id
    Start-Sleep -Seconds 3
    if ($serverProcess.HasExited) { throw 'O servidor local encerrou. Consulte dados\servidor-erros.log.' }
    Write-Host 'Pronto. Use a interface que abriu no navegador.'
    Stop-Transcript | Out-Null
    exit 0
} catch {
    Write-Host ('ERRO: ' + $_.Exception.Message) -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
