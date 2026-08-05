param(
    [ValidateSet("Sistema", "Usuario")]
    [string]$Modo = "Usuario"
)

$ErrorActionPreference = "Stop"
$Base = Split-Path -Parent $MyInvocation.MyCommand.Path
$ResultPath = Join-Path $Base "CONFIGURACAO_QZ_RESULTADO.txt"
$CommonName = "Sistema de Conferencia Noromak Web"
$ExpectedSha256 = "0F567BC916FBD2B451B01FA593BD91DF0B0925C44A475CED427081D94A21EB46"

function Add-Result([string]$Text) { Add-Content -Path $ResultPath -Encoding UTF8 -Value $Text }

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
        if (Test-Path $console) { return [PSCustomObject]@{Dir=$dir;Console=$console;Tray=$tray} }
    }
    throw "QZ Tray 2.2 ou superior não foi encontrado."
}

function Stop-Qz {
    Get-Process -Name "qz-tray","qz-tray-console" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Upsert-Property([string]$Path,[string]$Name,[string]$Value) {
    $existing = if (Test-Path $Path) { Get-Content -Raw -Path $Path } else { "" }
    $escaped=[regex]::Escape($Name)
    $existing=[regex]::Replace($existing,"(?im)^\s*$escaped\s*=.*(?:\r?\n)?","")
    [IO.File]::WriteAllText($Path,$existing.TrimEnd()+[Environment]::NewLine+"$Name=$Value"+[Environment]::NewLine,(New-Object Text.UTF8Encoding($false)))
}

function Get-PemCertificate([string]$Path) {
    $pem=Get-Content -Raw -Path $Path
    $match=[regex]::Match($pem,"-----BEGIN CERTIFICATE-----\s*(?<b64>[A-Za-z0-9+/=\r\n]+?)\s*-----END CERTIFICATE-----")
    if (!$match.Success) { throw "Certificado PEM inválido: $Path" }
    $raw=[Convert]::FromBase64String(($match.Groups['b64'].Value -replace '\s',''))
    return New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,$raw)
}

function Get-Sha256([System.Security.Cryptography.X509Certificates.X509Certificate2]$Cert) {
    $sha=[System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Cert.RawData))).Replace('-','') } finally { $sha.Dispose() }
}

function Remove-MatchingBlockLines([string]$AllowedPath,[string]$BlockedPath) {
    if (!(Test-Path $AllowedPath) -or !(Test-Path $BlockedPath)) { return }
    $allowed=@(Get-Content $AllowedPath -ErrorAction SilentlyContinue | ForEach-Object {$_.Trim()} | Where-Object {$_})
    if (!$allowed.Count) { return }
    $blocked=@(Get-Content $BlockedPath -ErrorAction SilentlyContinue)
    $filtered=@($blocked | Where-Object { $allowed -notcontains $_.Trim() })
    if ($filtered.Count -ne $blocked.Count) {
        Copy-Item -Force $BlockedPath "$BlockedPath.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        $filtered | Set-Content -Path $BlockedPath -Encoding UTF8
        Add-Result "Bloqueio antigo do mesmo certificado removido."
    }
}

function New-Shortcut([string]$Path,[string]$Target,[string]$Arguments,[int]$WindowStyle=1) {
    $shell=New-Object -ComObject WScript.Shell
    $shortcut=$shell.CreateShortcut($Path)
    $shortcut.TargetPath=$Target
    $shortcut.Arguments=$Arguments
    $shortcut.WorkingDirectory=Split-Path -Parent $Target
    $shortcut.Description="Noromak Estação Zebra silenciosa"
    $shortcut.WindowStyle=$WindowStyle
    $shortcut.Save()
}

try {
    $RootSource=Join-Path $Base "QZ_CERTIFICADO_WEB\qz-root-ca.crt"
    $CertSource=Join-Path $Base "QZ_CERTIFICADO_WEB\digital-certificate.txt"
    $LauncherSource=Join-Path $Base "INICIAR_ESTACAO_ZEBRA_SILENCIOSA.ps1"
    if (!(Test-Path $RootSource) -or !(Test-Path $CertSource) -or !(Test-Path $LauncherSource)) { throw "Arquivos de configuração QZ incompletos." }

    $cert=Get-PemCertificate $CertSource
    $fingerprint=Get-Sha256 $cert
    if ($fingerprint -ne $ExpectedSha256) { throw "O certificado do pacote não corresponde à versão esperada. SHA256=$fingerprint" }

    $Qz=Find-QzInstall
    if ($Modo -eq "Sistema") {
        @("CONFIGURACAO QZ TRAY HEADLESS - EXPEDICAO WEB 9.3","Data: "+(Get-Date -Format "dd/MM/yyyy HH:mm:ss"),"Etapa 1/2: computador","QZ: $($Qz.Dir)","Certificado SHA256: $fingerprint") | Set-Content -Path $ResultPath -Encoding UTF8
        Stop-Qz

        $GlobalDir=Join-Path $env:ProgramData "NoromakQZHeadless"
        New-Item -ItemType Directory -Force -Path $GlobalDir | Out-Null
        Copy-Item -Force $RootSource (Join-Path $GlobalDir "qz-root-ca.crt")
        Copy-Item -Force $CertSource (Join-Path $GlobalDir "digital-certificate.txt")

        $Override=Join-Path $Qz.Dir "override.crt"
        Copy-Item -Force $RootSource $Override
        $Properties=Join-Path $Qz.Dir "qz-tray.properties"
        Upsert-Property $Properties "authcert.override" "override.crt"
        Upsert-Property $Properties "tray.headless" "true"
        Upsert-Property $Properties "tray.notifications" "false"
        Upsert-Property $Properties "websocket.steal" "true"
        Upsert-Property $Properties "tray.idle.printers" "true"
        Add-Result "QZ configurado para iniciar sem interface e confiar na raiz Noromak."

        Import-Certificate -FilePath $RootSource -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
        Add-Result "Raiz instalada em LocalMachine\\Root."

        try {
            $chromeKey="HKLM:\SOFTWARE\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls"
            New-Item -Path $chromeKey -Force | Out-Null
            New-ItemProperty -Path $chromeKey -Name "1" -Value "https://noromakweb.com.br" -PropertyType String -Force | Out-Null
            $edgeKey="HKLM:\SOFTWARE\Policies\Microsoft\Edge\LocalNetworkAccessAllowedForUrls"
            New-Item -Path $edgeKey -Force | Out-Null
            New-ItemProperty -Path $edgeKey -Name "1" -Value "https://noromakweb.com.br" -PropertyType String -Force | Out-Null
            Add-Result "Acesso local liberado para Chrome e Edge."
        } catch { Add-Result ("Aviso ao configurar política do navegador: "+$_.Exception.Message) }

        Add-Result "Etapa de computador concluída."
        exit 0
    }

    Add-Result ""
    Add-Result "Etapa 2/2: usuário atual $([Environment]::UserName)"
    Import-Certificate -FilePath $RootSource -CertStoreLocation "Cert:\CurrentUser\Root" | Out-Null
    Add-Result "Raiz instalada em CurrentUser\\Root."

    Stop-Qz
    $allowOutput=(& $Qz.Console --whitelist $CertSource 2>&1 | Out-String).Trim()
    $allowExit=$LASTEXITCODE
    Add-Result "Saída do --whitelist: $allowOutput"
    if ($allowExit -ne 0) { throw "QZ Tray recusou a whitelist (código $allowExit)." }

    $UserQz=Join-Path $env:APPDATA "qz"
    $Allowed=Join-Path $UserQz "allowed.dat"
    if (!(Test-Path $Allowed)) { throw "allowed.dat não foi criado em $UserQz" }
    Remove-MatchingBlockLines $Allowed (Join-Path $UserQz "blocked.dat")
    Add-Result "Certificado autorizado no usuário atual: $Allowed"

    $GlobalQz=Join-Path $env:ProgramData "qz"
    New-Item -ItemType Directory -Force -Path $GlobalQz | Out-Null
    Copy-Item -Force $Allowed (Join-Path $GlobalQz "allowed.dat")
    Add-Result "Whitelist copiada também para o escopo global."

    $LocalDir=Join-Path $env:LOCALAPPDATA "NoromakQZHeadless"
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    Copy-Item -Force $RootSource (Join-Path $LocalDir "qz-root-ca.crt")
    Copy-Item -Force $CertSource (Join-Path $LocalDir "digital-certificate.txt")
    Copy-Item -Force $LauncherSource (Join-Path $LocalDir "INICIAR_ESTACAO_ZEBRA_SILENCIOSA.ps1")

    # Remove o inicializador visual criado nas versões anteriores.
    try { Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "NoromakQZTray" -ErrorAction SilentlyContinue } catch {}
    Get-Process -Name "qz-tray","qz-tray-console" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

    $Startup=[Environment]::GetFolderPath("Startup")
    $Desktop=[Environment]::GetFolderPath("Desktop")
    $PowerShell=(Get-Command powershell.exe).Source
    $Launcher=Join-Path $LocalDir "INICIAR_ESTACAO_ZEBRA_SILENCIOSA.ps1"
    $launchArgs='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$Launcher+'"'
    New-Shortcut (Join-Path $Startup "Noromak QZ Silencioso.lnk") $PowerShell $launchArgs 7
    New-Shortcut (Join-Path $Desktop "Reiniciar Estacao Zebra.lnk") $PowerShell $launchArgs 1
    Add-Result "Inicialização silenciosa configurada com o Windows."

    $proc=Start-Process -FilePath $PowerShell -ArgumentList $launchArgs -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 8
    if ($proc.HasExited -and $proc.ExitCode -ne 0) { throw "O inicializador headless terminou com erro. Consulte $LocalDir\qz-headless.log" }
    Add-Result "QZ headless e Estação Zebra iniciados."
    Add-Result "CONFIGURAÇÃO CONCLUÍDA COM SUCESSO."
    exit 0
} catch {
    if (!(Test-Path $ResultPath)) { "CONFIGURACAO QZ TRAY HEADLESS - EXPEDICAO WEB 9.3" | Set-Content -Path $ResultPath -Encoding UTF8 }
    Add-Result ("ERRO: "+$_.Exception.Message)
    exit 1
}
