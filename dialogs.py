"""Native pickers on the SAME PC as the local server. No credentials or shell input."""
import json
import os
import subprocess


def choose(kind, initial=""):
    if kind not in ("folder", "json"):
        raise ValueError("Tipo de seletor inválido.")
    if os.name != "nt":
        raise ValueError("O seletor nativo é para Windows. Cole o caminho ou arraste os arquivos na interface.")
    # Paths are passed only through the environment, never interpolated as PowerShell code.
    script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
try {
  if ($env:FLOW_PICKER_KIND -eq 'folder') {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Selecione a pasta local'
    $dialog.ShowNewFolderButton = $true
    if ([IO.Directory]::Exists($env:FLOW_PICKER_INITIAL)) { $dialog.SelectedPath = $env:FLOW_PICKER_INITIAL }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
      ConvertTo-Json -Compress -InputObject @($dialog.SelectedPath)
    } else { '[]' }
  } else {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Filter = 'Lotes JSON (*.json)|*.json'
    $dialog.Multiselect = $true
    if ([IO.Directory]::Exists($env:FLOW_PICKER_INITIAL)) { $dialog.InitialDirectory = $env:FLOW_PICKER_INITIAL }
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
      ConvertTo-Json -Compress -InputObject @($dialog.FileNames)
    } else { '[]' }
  }
} finally { $owner.Close(); $owner.Dispose() }
'''
    env = os.environ.copy()
    env.update(FLOW_PICKER_KIND=kind, FLOW_PICKER_INITIAL=initial if isinstance(initial, str) else "")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                            env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180, shell=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError("Não foi possível abrir o seletor do Windows. Cole o caminho na interface ou arraste os arquivos.")
    value = json.loads(result.stdout.lstrip("\ufeff").strip())
    return value if isinstance(value, list) else [value]
