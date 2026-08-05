$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResultPath = Join-Path $Base "CONFIGURACAO_ESTACAO_RESULTADO.txt"
$Url = "https://noromakweb.com.br/expedicao/estacao-impressao?auto=1"

function Find-Browser {
    $candidates = @(
        @{ Name = "Google Chrome"; Path = (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe") },
        @{ Name = "Google Chrome"; Path = $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe" }) },
        @{ Name = "Google Chrome"; Path = (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe") },
        @{ Name = "Microsoft Edge"; Path = (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe") },
        @{ Name = "Microsoft Edge"; Path = $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe" }) }
    )
    foreach ($item in $candidates) {
        if ($item.Path -and (Test-Path $item.Path)) { return [PSCustomObject]$item }
    }
    throw "Chrome ou Microsoft Edge nao foi encontrado neste computador."
}

function New-Link([string]$Path, [string]$Target, [string]$Arguments, [int]$WindowStyle) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = Split-Path -Parent $Target
    $shortcut.Description = "Estacao Zebra Noromak - impressao automatica"
    $shortcut.WindowStyle = $WindowStyle
    $shortcut.Save()
}

try {
    @(
        "CONFIGURACAO DA ESTACAO ZEBRA AUTOMATICA 9.2",
        "Data: " + (Get-Date -Format "dd/MM/yyyy HH:mm:ss")
    ) | Set-Content -Path $ResultPath -Encoding UTF8

    $Browser = Find-Browser
    Add-Content $ResultPath "Navegador selecionado: $($Browser.Name) - $($Browser.Path)"

    $Args = '--app="' + $Url + '" --disable-session-crashed-bubble --no-first-run'
    $Startup = [Environment]::GetFolderPath("Startup")
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $StartupLink = Join-Path $Startup "Noromak Estacao Zebra Automatica.lnk"
    $DesktopLink = Join-Path $Desktop "Noromak Estacao Zebra.lnk"

    # A abertura no logon fica minimizada. A estacao usa busca longa no servidor,
    # portanto continua recebendo trabalhos mesmo em segundo plano.
    New-Link $StartupLink $Browser.Path $Args 7
    New-Link $DesktopLink $Browser.Path $Args 1

    Add-Content $ResultPath "Atalho de inicializacao criado em: $StartupLink"
    Add-Content $ResultPath "Atalho da area de trabalho criado em: $DesktopLink"

    # Abre agora em primeiro plano para o login inicial e a verificacao do estado.
    Start-Process -FilePath $Browser.Path -ArgumentList $Args
    Add-Content $ResultPath "Estacao aberta para a ativacao inicial."
    Add-Content $ResultPath "CONFIGURACAO CONCLUIDA COM SUCESSO."
    exit 0
}
catch {
    Add-Content $ResultPath ("ERRO: " + $_.Exception.Message)
    exit 1
}
