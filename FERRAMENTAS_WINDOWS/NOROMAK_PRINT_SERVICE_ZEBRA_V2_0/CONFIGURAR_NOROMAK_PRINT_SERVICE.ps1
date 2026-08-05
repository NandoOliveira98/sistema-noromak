$ErrorActionPreference = 'Stop'
$Base = Join-Path $env:LOCALAPPDATA 'NoromakPrintService'
$ExeOrigem = Join-Path $PSScriptRoot 'NoromakPrintService.exe'
$ExeDestino = Join-Path $Base 'NoromakPrintService.exe'
$ConfigPath = Join-Path $Base 'config.json'
$Resultado = Join-Path $PSScriptRoot 'CONFIGURACAO_RESULTADO.txt'
$StationUrl = 'https://noromakweb.com.br/expedicao/estacao-impressao'

function Registrar([string]$texto) {
    $linha = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $texto"
    $linha | Tee-Object -FilePath $Resultado -Append | Write-Host
}

Set-Content -Path $Resultado -Value "NOROMAK PRINT SERVICE 2.0 - CONFIGURACAO`r`n" -Encoding UTF8

try {
    if (-not (Test-Path $ExeOrigem)) { throw "NoromakPrintService.exe nao foi encontrado na pasta extraida." }
    New-Item -ItemType Directory -Path $Base -Force | Out-Null

    Registrar 'Encerrando versoes antigas do agente...'
    Get-Process -Name 'NoromakPrintService','NoromakPrintAgent' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700

    Registrar 'Localizando impressoras instaladas no Windows...'
    $printers = @(Get-CimInstance Win32_Printer | Sort-Object Name)
    if ($printers.Count -eq 0) { throw 'Nenhuma impressora foi encontrada no Windows.' }

    Write-Host ''
    Write-Host 'IMPRESSORAS INSTALADAS:' -ForegroundColor Cyan
    for ($i = 0; $i -lt $printers.Count; $i++) {
        $marca = if ($printers[$i].Default) { ' [PADRAO]' } else { '' }
        Write-Host ("{0}. {1}{2}" -f ($i + 1), $printers[$i].Name, $marca)
    }

    $sugerida = 1
    for ($i = 0; $i -lt $printers.Count; $i++) {
        if ($printers[$i].Name -match 'ZDesigner|Zebra|ZD220') { $sugerida = $i + 1; break }
    }
    $escolha = Read-Host "Digite o numero da Zebra [$sugerida]"
    if ([string]::IsNullOrWhiteSpace($escolha)) { $escolha = "$sugerida" }
    $numero = 0
    if (-not [int]::TryParse($escolha, [ref]$numero) -or $numero -lt 1 -or $numero -gt $printers.Count) {
        throw 'Numero de impressora invalido.'
    }
    $printerName = [string]$printers[$numero - 1].Name
    Registrar "Impressora selecionada: $printerName"

    Copy-Item -Path $ExeOrigem -Destination $ExeDestino -Force
    $config = [ordered]@{
        printer = $printerName
        port = 18181
        allowed_origins = @('https://noromakweb.com.br','https://www.noromakweb.com.br')
    }
    $jsonConfig = $config | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText($ConfigPath, $jsonConfig, (New-Object System.Text.UTF8Encoding($false)))
    Registrar "Programa instalado em: $ExeDestino"

    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'NoromakPrintService' -Value ('"' + $ExeDestino + '"') -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $runKey -Name 'NoromakPrintStation' -Value ('cmd.exe /c start "" "' + $StationUrl + '"') -PropertyType String -Force | Out-Null
    Remove-ItemProperty -Path $runKey -Name 'NoromakPrintAgent' -ErrorAction SilentlyContinue
    Registrar 'Inicializacao automatica configurada no usuario atual do Windows.'

    $desktop = [Environment]::GetFolderPath('Desktop')
    $internetShortcut = "[InternetShortcut]`r`nURL=$StationUrl`r`n"
    [System.IO.File]::WriteAllText((Join-Path $desktop 'Estacao Zebra Noromak.url'), $internetShortcut, (New-Object System.Text.UTF8Encoding($false)))

    Start-Process -FilePath $ExeDestino -WindowStyle Hidden
    Registrar 'Aguardando o programa local responder...'
    $health = $null
    for ($tentativa = 1; $tentativa -le 20; $tentativa++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:18181/health' -TimeoutSec 3
            if ($health.ok) { break }
        } catch {}
    }
    if (-not $health -or -not $health.ok) { throw 'O programa foi instalado, mas nao respondeu na porta local 18181.' }
    Registrar "Servico local conectado. Versao: $($health.version). Impressora encontrada: $($health.printer_found)."

    $teste = Read-Host 'Deseja imprimir uma etiqueta de teste agora? (S/N) [S]'
    if ([string]::IsNullOrWhiteSpace($teste)) { $teste = 'S' }
    if ($teste -match '^[sS]') {
        $headers = @{ 'X-Noromak-Client' = 'expedicao-web' }
        $resp = Invoke-RestMethod -Uri 'http://127.0.0.1:18181/test' -Method Post -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 20
        if (-not $resp.ok) { throw 'A impressao de teste nao foi confirmada.' }
        Registrar 'Etiqueta de teste enviada para a Zebra.'
    }

    Start-Process $StationUrl
    Registrar 'Estacao Web aberta. Entre uma vez com o usuario do estoque e deixe a pagina aberta.'
    Registrar 'CONFIGURACAO CONCLUIDA COM SUCESSO.'
    Write-Host ''
    Write-Host 'CONFIGURACAO CONCLUIDA COM SUCESSO.' -ForegroundColor Green
    Write-Host 'O QZ Tray nao e utilizado por esta versao.' -ForegroundColor Green
} catch {
    Registrar ("ERRO: " + $_.Exception.Message)
    Write-Host ''
    Write-Host ("ERRO: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
