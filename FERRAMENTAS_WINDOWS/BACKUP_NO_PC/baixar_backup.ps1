# V18.65 — copia o backup diário da VPS para este PC (cópia fora do servidor).
#
# O servidor grava /home/expedicao/backups/expedicao_AAAA-MM-DD.db.gz uma vez por dia
# (gravação atômica: o .gz só aparece completo). Este script baixa o mais recente por
# SSH, usando o mesmo atalho "expedicao" do ~/.ssh/config que o publicar.sh usa.
#
# - Só lê do servidor. Nunca apaga nem altera nada lá.
# - Baixa para um .part e só renomeia depois de conferir tamanho e cabeçalho gzip.
# - Mantém as últimas 30 cópias na pasta de destino.
# - Se a cópia mais nova da pasta tiver mais de 3 dias, mostra um aviso na tela:
#   o SSH desta rede pode estar bloqueado (já aconteceu fora do cabo do escritório).
#
# Uso manual:  powershell -ExecutionPolicy Bypass -File baixar_backup.ps1
param(
    [string]$Destino = (Join-Path $env:USERPROFILE "Documents\BACKUP_NOROMAK"),
    [string]$Host_ = "expedicao",
    [string]$PastaRemota = "/home/expedicao/backups",
    [int]$Manter = 30,
    [int]$AvisarAposDias = 3,
    [string]$SshExe = "ssh",
    [string]$ScpExe = "scp",
    [switch]$SemAviso
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Destino | Out-Null
$log = Join-Path $Destino "baixar_backup.log"

function Registrar([string]$msg) {
    $linha = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $linha -Encoding UTF8
    Write-Output $linha
}

function AvisarSeAtrasado {
    $maisNovo = Get-ChildItem -Path $Destino -Filter "expedicao_????-??-??.db.gz" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    $dias = if ($maisNovo) { ((Get-Date) - $maisNovo.LastWriteTime).TotalDays } else { [double]::PositiveInfinity }
    if ($dias -gt $AvisarAposDias -and -not $SemAviso) {
        $texto = if ($maisNovo) { "A cópia mais recente do banco neste PC é de $([math]::Floor($dias)) dias atrás ($($maisNovo.Name))." } else { "Ainda não há nenhuma cópia do banco neste PC." }
        $texto += "`n`nVerifique se este PC alcança a VPS por SSH (no cabo do escritório funciona). Log: $log"
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show($texto, "Backup Noromak", "OK", "Warning") | Out-Null
    }
}

$sshOpts = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
try {
    # Nome e tamanho do backup diário mais recente (ignora .tmp e os predeploy).
    # Sem aspas duplas no comando remoto: o PowerShell 5.1 estraga aspas em argumentos
    # de programas externos. Os nomes não têm espaço; pasta vazia = saída vazia.
    $remoto = 'cd {0} && ls -1 expedicao_????-??-??.db.gz 2>/dev/null | sort | tail -n 1 | while read f; do echo $f $(stat -c %s $f); done' -f $PastaRemota
    $saida = & $SshExe @sshOpts $Host_ $remoto
    if ($LASTEXITCODE -ne 0 -or -not $saida) { throw "SSH sem resposta ou nenhum backup na VPS (código $LASTEXITCODE)." }
    $nome, $tamanhoTxt = ("$saida".Trim() -split "\s+", 2)
    $tamanho = [int64]$tamanhoTxt
    if ($nome -notmatch '^expedicao_\d{4}-\d{2}-\d{2}\.db\.gz$' -or $tamanho -le 0) { throw "Resposta inesperada do servidor: '$saida'" }

    $final = Join-Path $Destino $nome
    if ((Test-Path $final) -and ((Get-Item $final).Length -eq $tamanho)) {
        Registrar "OK  $nome já estava copiado ($tamanho bytes)."
    } else {
        $parte = "$final.part"
        Remove-Item -Force -ErrorAction SilentlyContinue $parte
        & $ScpExe @sshOpts -q "${Host_}:$PastaRemota/$nome" $parte
        if ($LASTEXITCODE -ne 0) { throw "scp falhou (código $LASTEXITCODE)." }
        $recebido = (Get-Item $parte).Length
        if ($recebido -ne $tamanho) { throw "Tamanho diferente: servidor $tamanho, recebido $recebido." }
        $cab = [byte[]]::new(2)
        $fs = [System.IO.File]::OpenRead($parte); try { [void]$fs.Read($cab, 0, 2) } finally { $fs.Close() }
        if ($cab[0] -ne 0x1F -or $cab[1] -ne 0x8B) { throw "O arquivo recebido não é gzip." }
        Move-Item -Force $parte $final
        Registrar "OK  $nome copiado ($tamanho bytes)."
    }

    # Retenção: só as últimas $Manter cópias diárias.
    Get-ChildItem -Path $Destino -Filter "expedicao_????-??-??.db.gz" | Sort-Object Name -Descending |
        Select-Object -Skip $Manter | ForEach-Object { Remove-Item -Force $_.FullName; Registrar "    apagada cópia antiga $($_.Name)" }
}
catch {
    Registrar "ERRO $($_.Exception.Message)"
    AvisarSeAtrasado
    exit 1
}
AvisarSeAtrasado
exit 0
