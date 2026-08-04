param(
    [ValidateSet("Sistema", "Usuario")]
    [string]$Modo = "Usuario"
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResultPath = Join-Path $Base "CONFIGURACAO_QZ_RESULTADO.txt"

function Add-Result([string]$Text) {
    Add-Content -Path $ResultPath -Encoding UTF8 -Value $Text
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
        $tray = Join-Path $dir "qz-tray.exe"
        if ((Test-Path $console) -or (Test-Path $tray)) {
            return [PSCustomObject]@{
                Dir = $dir
                Console = $console
                Tray = $tray
            }
        }
    }

    throw "QZ Tray 2.2 ou superior nao foi encontrado. Instale o QZ Tray e execute novamente."
}

function Upsert-Property([string]$Path, [string]$Name, [string]$Value) {
    $existing = if (Test-Path $Path) { Get-Content -Raw -Path $Path } else { "" }
    $escaped = [regex]::Escape($Name)
    $existing = [regex]::Replace($existing, "(?im)^\s*$escaped\s*=.*(?:\r?\n)?", "")
    $newContent = $existing.TrimEnd() + [Environment]::NewLine + "$Name=$Value" + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $newContent, (New-Object Text.UTF8Encoding($false)))
}

function Stop-Qz {
    Get-Process -Name "qz-tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process -Name "qz-tray-console" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 1200
}

function Test-QzPort {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(1200, $false)
        if ($ok -and $client.Connected) {
            $client.EndConnect($async)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

try {
    $RootSource = Join-Path $Base "QZ_CERTIFICADO_WEB\qz-root-ca.crt"
    $CertSource = Join-Path $Base "QZ_CERTIFICADO_WEB\digital-certificate.txt"
    if (!(Test-Path $RootSource) -or !(Test-Path $CertSource)) {
        throw "Os certificados publicos nao foram encontrados na pasta QZ_CERTIFICADO_WEB."
    }

    $Qz = Find-QzInstall
    $QzDir = $Qz.Dir
    $Console = $Qz.Console
    $Tray = $Qz.Tray

    if ($Modo -eq "Sistema") {
        @(
            "CONFIGURACAO QZ TRAY - EXPEDICAO WEB 8.1",
            "Data: " + (Get-Date -Format "dd/MM/yyyy HH:mm:ss"),
            "Etapa 1/2: confianca do computador",
            "QZ Tray encontrado em: $QzDir"
        ) | Set-Content -Path $ResultPath -Encoding UTF8

        Stop-Qz

        # Remove uma configuracao antiga da versao 7.7 que podia impedir o QZ
        # de iniciar quando o caminho do usuario continha espacos.
        [Environment]::SetEnvironmentVariable("QZ_OPTS", $null, "Machine")
        Add-Result "QZ_OPTS antigo removido do computador."

        $PublicBase = if ($env:PUBLIC) { $env:PUBLIC } else { $env:ProgramData }
        $CertDir = Join-Path $PublicBase "NoromakQZWeb"
        New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
        Copy-Item -Force $RootSource (Join-Path $CertDir "qz-root-ca.crt")
        Copy-Item -Force $CertSource (Join-Path $CertDir "digital-certificate.txt")
        Add-Result "Certificados publicos copiados para: $CertDir"

        # O QZ documenta authcert.override para confiar em uma CA propria.
        # Usamos nome relativo para evitar problemas com espacos no caminho.
        $Override = Join-Path $QzDir "override.crt"
        Copy-Item -Force $RootSource $Override
        $Properties = Join-Path $QzDir "qz-tray.properties"
        Upsert-Property $Properties "authcert.override" "override.crt"
        Add-Result "CA propria instalada e authcert.override=override.crt configurado."

        try {
            Import-Certificate -FilePath $RootSource -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
            Add-Result "Raiz adicionada ao repositorio LocalMachine\\Root do Windows."
        } catch {
            Add-Result ("Aviso ao importar em LocalMachine\\Root: " + $_.Exception.Message)
        }

        # Chrome passou a controlar acesso a servicos locais. Esta politica
        # libera somente o dominio da expedicao para acessar o QZ local.
        try {
            $chromeKey = "HKLM:\SOFTWARE\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls"
            New-Item -Path $chromeKey -Force | Out-Null
            New-ItemProperty -Path $chromeKey -Name "1" -Value "https://noromakweb.com.br" -PropertyType String -Force | Out-Null
            Add-Result "Politica do Chrome para acesso local configurada."
        } catch {
            Add-Result ("Aviso ao configurar politica do Chrome: " + $_.Exception.Message)
        }

        Add-Result "Etapa de sistema concluida."
        exit 0
    }

    Add-Result ""
    Add-Result "Etapa 2/2: autorizacao do usuario atual"
    Add-Result ("Usuario do Windows: " + [Environment]::UserName)

    [Environment]::SetEnvironmentVariable("QZ_OPTS", $null, "User")
    Remove-Item Env:\QZ_OPTS -ErrorAction SilentlyContinue
    Add-Result "QZ_OPTS antigo removido do usuario."

    try {
        Import-Certificate -FilePath $RootSource -CertStoreLocation "Cert:\CurrentUser\Root" | Out-Null
        Add-Result "Raiz adicionada ao repositorio CurrentUser\\Root."
    } catch {
        Add-Result ("Aviso ao importar em CurrentUser\\Root: " + $_.Exception.Message)
    }

    $UserCertDir = Join-Path $env:LOCALAPPDATA "NoromakQZWeb"
    New-Item -ItemType Directory -Force -Path $UserCertDir | Out-Null
    $UserRoot = Join-Path $UserCertDir "qz-root-ca.crt"
    $UserCert = Join-Path $UserCertDir "digital-certificate.txt"
    Copy-Item -Force $RootSource $UserRoot
    Copy-Item -Force $CertSource $UserCert
    Add-Result "Certificados copiados para o perfil do usuario: $UserCertDir"

    $QzUserDir = Join-Path $env:APPDATA "qz"
    New-Item -ItemType Directory -Force -Path $QzUserDir | Out-Null
    $BlockedPath = Join-Path $QzUserDir "blocked.dat"
    $CommonName = "Sistema de Conferencia Noromak Web"

    # Remove somente uma decisao de bloqueio referente a este sistema.
    if (Test-Path $BlockedPath) {
        $blockedLines = @(Get-Content $BlockedPath -ErrorAction SilentlyContinue)
        $filtered = @($blockedLines | Where-Object { $_ -notmatch [regex]::Escape($CommonName) })
        if ($filtered.Count -ne $blockedLines.Count) {
            $backupBlocked = "$BlockedPath.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
            Copy-Item -Force $BlockedPath $backupBlocked
            $filtered | Set-Content -Path $BlockedPath -Encoding UTF8
            Add-Result "Bloqueio antigo deste certificado removido; backup criado."
        }
    }

    Stop-Qz

    if (!(Test-Path $Console)) {
        throw "qz-tray-console.exe nao foi encontrado em $QzDir. Atualize o QZ Tray para a versao 2.2 ou superior."
    }

    $versionOutput = (& $Console --version 2>&1 | Out-String).Trim()
    Add-Result ("Versao do QZ Tray: " + $versionOutput)

    $allowOutput = (& $Console --allow $UserCert 2>&1 | Out-String).Trim()
    $allowExit = $LASTEXITCODE
    Add-Result "Saida do comando --allow:"
    Add-Result $allowOutput
    Add-Result ("Codigo de saida do --allow: " + $allowExit)
    if ($allowExit -ne 0) {
        throw "O QZ Tray recusou o comando --allow (codigo $allowExit)."
    }

    $AllowedPath = Join-Path $QzUserDir "allowed.dat"
    if (!(Test-Path $AllowedPath)) {
        throw "O QZ Tray nao criou allowed.dat em $QzUserDir."
    }
    Add-Result "Autorizacao confirmada em: $AllowedPath"

    Stop-Qz
    if (Test-Path $Tray) {
        Start-Process -FilePath $Tray -WorkingDirectory $QzDir
    } else {
        Start-Process -FilePath $Console -WorkingDirectory $QzDir
    }

    $ready = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-QzPort 8181) -or (Test-QzPort 8182)) {
            $ready = $true
            break
        }
    }

    if (!$ready) {
        throw "O QZ Tray foi iniciado, mas as portas locais 8181/8182 nao responderam. Verifique antivirus ou instalacao do QZ."
    }

    Add-Result "QZ Tray iniciado e respondendo nas portas locais."
    Add-Result ""
    Add-Result "CONFIGURACAO CONCLUIDA COM SUCESSO."
    Add-Result "Feche todas as abas da Expedicao, abra novamente e teste a impressao."
    exit 0
}
catch {
    if (!(Test-Path $ResultPath)) {
        "CONFIGURACAO QZ TRAY - EXPEDICAO WEB 8.1" | Set-Content -Path $ResultPath -Encoding UTF8
    }
    Add-Result ""
    Add-Result ("ERRO: " + $_.Exception.Message)
    exit 1
}
