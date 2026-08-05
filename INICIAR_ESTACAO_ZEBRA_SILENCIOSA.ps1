$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogPath = Join-Path $Base "qz-headless.log"
$RootCert = Join-Path $Base "qz-root-ca.crt"
$SiteCert = Join-Path $Base "digital-certificate.txt"
$StationUrl = "https://noromakweb.com.br/expedicao/estacao-impressao?auto=1&headless=1"

function Log([string]$Text) {
    Add-Content -Path $LogPath -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Text)
}

function Find-QzInstall {
    $paths = @(
        (Join-Path $env:ProgramFiles "QZ Tray"),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "QZ Tray" }),
        (Join-Path $env:LOCALAPPDATA "Programs\QZ Tray"),
        (Join-Path $env:LOCALAPPDATA "QZ Tray")
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($dir in $paths) {
        $console = Join-Path $dir "qz-tray-console.exe"
        if (Test-Path $console) { return $console }
    }
    throw "qz-tray-console.exe não encontrado."
}

function Test-Port([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if ($ok -and $client.Connected) { $client.EndConnect($async); $client.Close(); return $true }
        $client.Close()
    } catch {}
    return $false
}

function Find-Browser {
    $paths = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe" }),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe" })
    ) | Where-Object { $_ -and (Test-Path $_) }
    return $paths | Select-Object -First 1
}

try {
    Log "Iniciando QZ Tray em modo headless."
    if (!(Test-Path $RootCert) -or !(Test-Path $SiteCert)) { throw "Certificados locais não encontrados em $Base" }
    $Console = Find-QzInstall

    Get-Process -Name "qz-tray","qz-tray-console" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # O certificado é autorizado novamente a cada login. A operação é idempotente.
    $allow = (& $Console --whitelist $SiteCert 2>&1 | Out-String).Trim()
    Log ("Whitelist: " + $allow)
    if ($LASTEXITCODE -ne 0) { throw "QZ Tray recusou a whitelist (código $LASTEXITCODE)." }

    # A raiz própria é usada pelo QZ para validar o certificado servido pelo site.
    $env:QZ_OPTS = '-DtrustedRootCert="' + $RootCert + '" -Dtray.headless=true -Dtray.notifications=false'
    $process = Start-Process -FilePath $Console -ArgumentList @("--headless", "--steal") -WindowStyle Hidden -PassThru
    Log ("QZ headless iniciado. PID=" + $process.Id)

    $ready = $false
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-Port 8181) -or (Test-Port 8182)) { $ready = $true; break }
    }
    if (!$ready) { throw "QZ Tray headless iniciou, mas as portas 8181/8182 não responderam." }
    Log "QZ Tray headless pronto."

    $Browser = Find-Browser
    if (!$Browser) { throw "Chrome ou Edge não encontrado." }
    Start-Process -FilePath $Browser -ArgumentList ('--app="' + $StationUrl + '" --disable-session-crashed-bubble --no-first-run')
    Log "Estação Zebra aberta."
    exit 0
} catch {
    Log ("ERRO: " + $_.Exception.Message)
    exit 1
}
