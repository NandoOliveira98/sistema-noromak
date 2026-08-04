$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Configurar Noromak Print Agent"

function Pausar {
    Write-Host ""
    Read-Host "Pressione ENTER para fechar"
}

try {
    $origem = Split-Path -Parent $MyInvocation.MyCommand.Path
    $exeOrigem = Join-Path $origem "NoromakPrintAgent.exe"
    if (-not (Test-Path $exeOrigem)) {
        throw "NoromakPrintAgent.exe não foi encontrado na mesma pasta do configurador. Extraia o ZIP inteiro antes de executar."
    }

    Write-Host "==============================================" -ForegroundColor DarkBlue
    Write-Host " NOROMAK PRINT AGENT - CONFIGURACAO" -ForegroundColor Blue
    Write-Host " Impressao direta na Zebra, sem QZ Tray" -ForegroundColor Blue
    Write-Host "==============================================" -ForegroundColor DarkBlue
    Write-Host ""

    $serverDefault = "https://noromakweb.com.br/expedicao"
    $server = Read-Host "Endereco do sistema [$serverDefault]"
    if ([string]::IsNullOrWhiteSpace($server)) { $server = $serverDefault }
    $server = $server.Trim().TrimEnd('/')

    Write-Host ""
    Write-Host "No painel Admin, abra a aba 'Agente Zebra' e copie o token." -ForegroundColor DarkYellow
    $token = Read-Host "Cole o token do agente"
    $token = $token.Trim()
    if ([string]::IsNullOrWhiteSpace($token)) { throw "O token não pode ficar vazio." }

    Write-Host ""
    Write-Host "Impressoras instaladas neste Windows:" -ForegroundColor DarkBlue
    $impressoras = @()
    try {
        $impressoras = @(Get-CimInstance Win32_Printer | Select-Object -ExpandProperty Name | Sort-Object)
    } catch {
        try { $impressoras = @(Get-Printer | Select-Object -ExpandProperty Name | Sort-Object) } catch { }
    }
    for ($i = 0; $i -lt $impressoras.Count; $i++) {
        Write-Host ("  {0}. {1}" -f ($i + 1), $impressoras[$i])
    }
    $sugerida = $impressoras | Where-Object { $_ -match 'ZDesigner|Zebra|ZD220' } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($sugerida)) { $sugerida = "ZDesigner ZD220-203dpi ZPL" }
    Write-Host ""
    $printer = Read-Host "Nome exato da impressora Zebra [$sugerida]"
    if ([string]::IsNullOrWhiteSpace($printer)) { $printer = $sugerida }
    $printer = $printer.Trim()

    Write-Host ""
    Write-Host "Validando token no servidor..." -ForegroundColor DarkBlue
    $headers = @{ Authorization = "Bearer $token" }
    $ping = Invoke-RestMethod -Uri "$server/api/print/agent/ping" -Headers $headers -Method Get -TimeoutSec 20
    if (-not $ping.ok) { throw "O servidor não confirmou o token." }
    Write-Host "Token aceito pelo servidor Web $($ping.version)." -ForegroundColor Green

    if ($impressoras.Count -gt 0 -and -not ($impressoras -contains $printer)) {
        Write-Host "ATENCAO: o nome informado não apareceu na lista de impressoras instaladas." -ForegroundColor Yellow
        $continuar = Read-Host "Deseja continuar mesmo assim? (S/N)"
        if ($continuar -notmatch '^[sS]') { throw "Configuração cancelada para corrigir o nome da impressora." }
    }

    $destino = Join-Path $env:LOCALAPPDATA "NoromakPrintAgent"
    New-Item -ItemType Directory -Path $destino -Force | Out-Null
    $exeDestino = Join-Path $destino "NoromakPrintAgent.exe"
    $configDestino = Join-Path $destino "config.json"

    Get-Process NoromakPrintAgent -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Copy-Item $exeOrigem $exeDestino -Force

    $config = [ordered]@{
        server_url   = $server
        token        = $token
        printer_name = $printer
        mode         = "windows"
        tcp_address  = ""
        poll_seconds = 2
    }
    $config | ConvertTo-Json | Set-Content -Path $configDestino -Encoding UTF8

    $startup = [Environment]::GetFolderPath('Startup')
    $atalho = Join-Path $startup "Noromak Print Agent.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $shortcut = $ws.CreateShortcut($atalho)
    $shortcut.TargetPath = $exeDestino
    $shortcut.WorkingDirectory = $destino
    $shortcut.Description = "Agente de impressão Zebra da Noromak"
    $shortcut.Save()

    Start-Process -FilePath $exeDestino -WorkingDirectory $destino
    Start-Sleep -Seconds 2
    $processo = Get-Process NoromakPrintAgent -ErrorAction SilentlyContinue
    if (-not $processo) {
        $logPath = Join-Path $destino "agent.log"
        $detalhe = if (Test-Path $logPath) { (Get-Content $logPath -Tail 15) -join "`n" } else { "Log ainda não criado." }
        throw "O agente não permaneceu aberto. Detalhes:`n$detalhe"
    }

    $resultado = @"
CONFIGURACAO CONCLUIDA COM SUCESSO
Data: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
Servidor: $server
Impressora: $printer
Programa: $exeDestino
Configuracao: $configDestino
Inicializacao automatica: $atalho
Log: $(Join-Path $destino 'agent.log')
"@
    $resultado | Set-Content -Path (Join-Path $origem "CONFIGURACAO_AGENTE_RESULTADO.txt") -Encoding UTF8

    Write-Host ""
    Write-Host "CONFIGURACAO CONCLUIDA COM SUCESSO." -ForegroundColor Green
    Write-Host "O agente já está em execução e abrirá automaticamente com o Windows." -ForegroundColor Green
    Write-Host "Agora use o botão de teste de impressão no sistema Web." -ForegroundColor Green
    Pausar
}
catch {
    $mensagem = $_.Exception.Message
    Write-Host ""
    Write-Host "ERRO NA CONFIGURACAO:" -ForegroundColor Red
    Write-Host $mensagem -ForegroundColor Red
    try {
        $origem = Split-Path -Parent $MyInvocation.MyCommand.Path
        "ERRO`r`nData: $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')`r`n$mensagem" | Set-Content -Path (Join-Path $origem "CONFIGURACAO_AGENTE_RESULTADO.txt") -Encoding UTF8
    } catch { }
    Pausar
    exit 1
}
