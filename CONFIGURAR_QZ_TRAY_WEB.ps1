$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResultPath = Join-Path $Base "CONFIGURACAO_QZ_RESULTADO.txt"
$Lines = New-Object System.Collections.Generic.List[string]
$Lines.Add("CONFIGURACAO QZ TRAY - EXPEDICAO WEB 7.2")
$Lines.Add("Data: " + (Get-Date -Format "dd/MM/yyyy HH:mm:ss"))

try {
    $RootSource = Join-Path $Base "QZ_CERTIFICADO_WEB\qz-root-ca.crt"
    $CertSource = Join-Path $Base "QZ_CERTIFICADO_WEB\digital-certificate.txt"
    if (!(Test-Path $RootSource) -or !(Test-Path $CertSource)) {
        throw "Os certificados publicos nao foram encontrados na pasta QZ_CERTIFICADO_WEB."
    }

    $Candidates = @(
        "$env:ProgramFiles\QZ Tray",
        "${env:ProgramFiles(x86)}\QZ Tray",
        "$env:LOCALAPPDATA\Programs\QZ Tray",
        "$env:LOCALAPPDATA\QZ Tray"
    ) | Where-Object { $_ -and (Test-Path (Join-Path $_ "qz-tray-console.exe")) }

    if ($Candidates.Count -eq 0) { throw "QZ Tray 2.2 ou superior nao foi encontrado." }
    $QzDir = $Candidates[0]
    $Console = Join-Path $QzDir "qz-tray-console.exe"
    $Tray = Join-Path $QzDir "qz-tray.exe"
    $Lines.Add("QZ Tray encontrado em: $QzDir")

    $PublicBase = if ($env:PUBLIC) { $env:PUBLIC } else { $env:ProgramData }
    $CertDir = Join-Path $PublicBase "NoromakQZWeb"
    New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
    $RootDest = Join-Path $CertDir "qz-root-ca.crt"
    $CertDest = Join-Path $CertDir "digital-certificate.txt"
    Copy-Item -Force $RootSource $RootDest
    Copy-Item -Force $CertSource $CertDest
    $Lines.Add("Certificados copiados para: $CertDir")

    $Override = Join-Path $QzDir "override.crt"
    Copy-Item -Force $RootSource $Override
    $Lines.Add("Raiz instalada no QZ Tray: $Override")

    $Properties = Join-Path $QzDir "qz-tray.properties"
    $PropertyLine = "authcert.override=" + ($Override -replace '\\','/')
    $Existing = if (Test-Path $Properties) { Get-Content -Raw $Properties } else { "" }
    $Existing = [regex]::Replace($Existing, '(?im)^\s*authcert\.override\s*=.*(?:\r?\n)?', '')
    $NewContent = $Existing.TrimEnd() + [Environment]::NewLine + $PropertyLine + [Environment]::NewLine
    [IO.File]::WriteAllText($Properties, $NewContent, (New-Object Text.UTF8Encoding($false)))
    $Lines.Add("authcert.override configurado.")

    $QzOpts = "-DtrustedRootCert=$RootDest"
    [Environment]::SetEnvironmentVariable("QZ_OPTS", $QzOpts, "User")
    $env:QZ_OPTS = $QzOpts
    $Lines.Add("QZ_OPTS configurado para o usuario do Windows.")

    Get-Process -Name "qz-tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 1000

    $Allow = Start-Process -FilePath $Console -ArgumentList @("--allow", $CertDest) -WorkingDirectory $QzDir -Wait -PassThru -NoNewWindow
    if ($Allow.ExitCode -ne 0) { throw "O QZ Tray recusou o comando --allow (codigo $($Allow.ExitCode))." }
    $Lines.Add("Certificado Web incluido na lista de sites permitidos.")

    if (Test-Path $Tray) {
        Start-Process -FilePath $Tray -ArgumentList "--steal" -WorkingDirectory $QzDir
        $Lines.Add("QZ Tray reiniciado.")
    }
    $Lines.Add("")
    $Lines.Add("CONFIGURACAO CONCLUIDA COM SUCESSO.")
    $Lines | Set-Content -Encoding UTF8 $ResultPath
    exit 0
}
catch {
    $Lines.Add("")
    $Lines.Add("ERRO: " + $_.Exception.Message)
    $Lines | Set-Content -Encoding UTF8 $ResultPath
    exit 1
}
