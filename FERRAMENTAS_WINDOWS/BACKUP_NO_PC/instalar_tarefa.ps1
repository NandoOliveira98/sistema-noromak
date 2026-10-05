# V18.65 — agenda o baixar_backup.ps1 no Agendador de Tarefas deste PC.
#
# Roda todo dia às 12:00 e 10 minutos depois de entrar no Windows. Se o PC estava
# desligado no horário, roda assim que possível. Só com o usuário logado, porque a
# chave SSH fica no perfil dele (~/.ssh/expedicao).
#
# Para remover:  powershell -ExecutionPolicy Bypass -File instalar_tarefa.ps1 -Remover
param([switch]$Remover)

$nome = "Noromak - Backup do banco no PC"
if ($Remover) {
    Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Tarefa '$nome' removida. As cópias já baixadas continuam na pasta."
    exit 0
}

$script = Join-Path $PSScriptRoot "baixar_backup.ps1"
$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$diario = New-ScheduledTaskTrigger -Daily -At "12:00"
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$logon.Delay = "PT10M"
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$usuario = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger @($diario, $logon) -Settings $config `
    -Principal $usuario -Description "Copia o backup diário da VPS da Expedição para Documentos\BACKUP_NOROMAK." -Force | Out-Null
Write-Output "Tarefa '$nome' instalada: todo dia às 12:00 e ao entrar no Windows."
Write-Output "Cópias em: $(Join-Path $env:USERPROFILE 'Documents\BACKUP_NOROMAK')"
