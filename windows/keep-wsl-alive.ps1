param(
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string]$Distro = 'Ubuntu',
    [switch]$InstallLogonTask,
    [switch]$Watch
)
$ErrorActionPreference = 'Stop'
$taskName = "HA Swarm WSL - $Distro"
$wslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'

function Find-KeepAlive {
    Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match ('-d\s+"?' + [regex]::Escape($Distro) + '"?\s') -and $_.CommandLine -match 'sleep\s+infinity' }
}

if ($Watch) {
    while ($true) {
        if (Find-KeepAlive) { Start-Sleep -Seconds 5; continue }
        & $wslExe -d $Distro --exec sleep infinity
        Start-Sleep -Seconds 5
    }
}

if ($InstallLogonTask) {
    $folder = Join-Path $env:LOCALAPPDATA 'HA-Swarm-POC'
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $installed = Join-Path $folder 'keep-wsl-alive.ps1'
    if ($PSCommandPath -ne $installed) { Copy-Item -LiteralPath $PSCommandPath -Destination $installed -Force }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Distro {1} -Watch' -f $installed, $Distro
    $action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
    $principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description 'Keep the HA lab Ubuntu session running after Windows logon.' -Force | Out-Null
    if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { Start-ScheduledTask -TaskName $taskName }
    Write-Host "WSL keep-alive installed and started: $taskName (at this user's logon)."
} elseif (-not (Find-KeepAlive)) {
    Start-Process -FilePath $wslExe -ArgumentList '-d', $Distro, '--exec', 'sleep', 'infinity' -WindowStyle Hidden
    Write-Host "$Distro will keep running in the background for this session."
} else {
    Write-Host "$Distro keep-alive is already running."
}
# Stop/disable the named scheduled task before intentionally shutting down WSL.
