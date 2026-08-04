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
    $candidates = @(
        "$env:ProgramFiles\QZ Tray",
        "${env:ProgramFiles(x86)}\QZ Tray",
        "$env:LOCALAPPDATA\Programs\QZ Tray",
        "$env:LOCALAPPDATA\QZ Tray"
    ) | Where-Object { $_ -and (Test-Path (Join-Path $_ "qz-tray-console.exe")) }

    if ($candidates.Count -eq 0) {
        throw "QZ Tray 2.2 ou superior nao foi encontrado neste computador."
    }
    return $candidates[0]
}

function Upsert-Property([string]$Path, [string]$Name, [string]$Value) {
    $existing = if (Test-Path $Path) { Get-Content -Raw $Path } else { "" }
    $escaped = [regex]::Escape($Name)
    $existing = [regex]::Replace($existing, "(?im)^\s*$escaped\s*=.*(?:\r?\n)?", "")
    $newContent = $existing.TrimEnd() + [Environment]::NewLine + "$Name=$Value" + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $newContent, (New-Object Text.UTF8Encoding($false)))
}

try {
    $RootSource = Join-Path $Base "QZ_CERTIFICADO_WEB\qz-root-ca.crt"
    $CertSource = Join-Path $Base "QZ_CERTIFICADO_WEB\digital-certificate.txt"
    if (!(Test-Path $RootSource) -or !(Test-Path $CertSource)) {
        throw "Os certificados publicos nao foram encontrados na pasta QZ_CERTIFICADO_WEB."
    }

    $QzDir = Find-QzInstall
    $Console = Join-Path $QzDir "qz-tray-console.exe"
    $Tray = Join-Path $QzDir "qz-tray.exe"

    if ($Modo -eq "Sistema") {
        @(
            "CONFIGURACAO QZ TRAY - EXPEDICAO WEB 7.7",
            "Data: " + (Get-Date -Format "dd/MM/yyyy HH:mm:ss"),
            "Etapa 1/2: confianca do computador",
            "QZ Tray encontrado em: $QzDir"
        ) | Set-Content -Path $ResultPath -Encoding UTF8

        $PublicBase = if ($env:PUBLIC) { $env:PUBLIC } else { $env:ProgramData }
        $CertDir = Join-Path $PublicBase "NoromakQZWeb"
        New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
        $RootDest = Join-Path $CertDir "qz-root-ca.crt"
        $CertDest = Join-Path $CertDir "digital-certificate.txt"
        Copy-Item -Force $RootSource $RootDest
        Copy-Item -Force $CertSource $CertDest
        Add-Result "Certificados publicos copiados para: $CertDir"

        $Override = Join-Path $QzDir "override.crt"
        Copy-Item -Force $RootSource $Override
        Add-Result "Raiz personalizada instalada em: $Override"

        $Properties = Join-Path $QzDir "qz-tray.properties"
        Upsert-Property $Properties "authcert.override" ($Override -replace '\\','/')
        Add-Result "authcert.override configurado no QZ Tray."

        $QzOpts = "-DtrustedRootCert=$RootDest"
        [Environment]::SetEnvironmentVariable("QZ_OPTS", $QzOpts, "Machine")
        Add-Result "QZ_OPTS configurado para todos os usuarios do computador."

        try {
            Import-Certificate -FilePath $RootSource -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
            Add-Result "Raiz adicionada ao repositorio de confianca do Windows."
        } catch {
            Add-Result "Aviso: nao foi possivel importar a raiz no Windows, mas o override interno do QZ foi configurado."
        }

        Add-Result "Etapa de sistema concluida."
        exit 0
    }

    Add-Result ""
    Add-Result "Etapa 2/2: autorizacao do usuario atual"
    Add-Result ("Usuario do Windows: " + [Environment]::UserName)

    $UserCertDir = Join-Path $env:LOCALAPPDATA "NoromakQZWeb"
    New-Item -ItemType Directory -Force -Path $UserCertDir | Out-Null
    $UserRoot = Join-Path $UserCertDir "qz-root-ca.crt"
    $UserCert = Join-Path $UserCertDir "digital-certificate.txt"
    Copy-Item -Force $RootSource $UserRoot
    Copy-Item -Force $CertSource $UserCert
    Add-Result "Certificados copiados para o perfil do usuario: $UserCertDir"

    $QzOptsUser = "-DtrustedRootCert=$UserRoot"
    [Environment]::SetEnvironmentVariable("QZ_OPTS", $QzOptsUser, "User")
    $env:QZ_OPTS = $QzOptsUser
    Add-Result "QZ_OPTS configurado para o usuario atual."

    $CertObject = New-Object -TypeName System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $UserCert
    $Thumbprint = $CertObject.Thumbprint.ToLowerInvariant()
    $CommonName = "Sistema de Conferencia Noromak Web"
    $QzUserDir = Join-Path $env:APPDATA "qz"
    New-Item -ItemType Directory -Force -Path $QzUserDir | Out-Null
    $BlockedPath = Join-Path $QzUserDir "blocked.dat"

    # Remove somente uma eventual decisao antiga de bloqueio deste certificado.
    if (Test-Path $BlockedPath) {
        $blockedLines = Get-Content $BlockedPath -ErrorAction SilentlyContinue
        $filtered = @($blockedLines | Where-Object {
            ($_ -notmatch [regex]::Escape($CommonName)) -and
            ($_ -notmatch [regex]::Escape($Thumbprint))
        })
        if ($filtered.Count -ne $blockedLines.Count) {
            $filtered | Set-Content -Path $BlockedPath -Encoding UTF8
            Add-Result "Bloqueio antigo deste certificado removido do perfil do usuario."
        }
    }

    Get-Process -Name "qz-tray" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 900

    $Allow = Start-Process -FilePath $Console -ArgumentList @("--allow", $UserCert) -WorkingDirectory $QzDir -Wait -PassThru -NoNewWindow
    if ($Allow.ExitCode -ne 0) {
        throw "O QZ Tray recusou o comando --allow para o usuario atual (codigo $($Allow.ExitCode))."
    }
    Add-Result "Certificado autorizado na lista allowed.dat do usuario atual."

    $AllowedPath = Join-Path $QzUserDir "allowed.dat"
    if (!(Test-Path $AllowedPath)) {
        throw "O QZ Tray nao criou allowed.dat em $QzUserDir."
    }
    Add-Result "Lista de autorizacoes confirmada em: $AllowedPath"

    if (Test-Path $Tray) {
        Start-Process -FilePath $Tray -ArgumentList "--steal" -WorkingDirectory $QzDir
        Start-Sleep -Seconds 2
        Add-Result "QZ Tray reiniciado no usuario atual."
    } else {
        Add-Result "Aviso: abra o QZ Tray manualmente pelo menu Iniciar."
    }

    Add-Result ""
    Add-Result "CONFIGURACAO CONCLUIDA COM SUCESSO."
    Add-Result "A pagina Web agora pode conectar e imprimir sem pedir Allow para este usuario do Windows."
    exit 0
}
catch {
    if (!(Test-Path $ResultPath)) {
        "CONFIGURACAO QZ TRAY - EXPEDICAO WEB 7.7" | Set-Content -Path $ResultPath -Encoding UTF8
    }
    Add-Result ""
    Add-Result ("ERRO: " + $_.Exception.Message)
    exit 1
}
