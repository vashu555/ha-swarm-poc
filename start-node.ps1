<#
  HA Swarm POC: turn this Windows PC into a node of the 3-region swarm.

  Open PowerShell (no admin needed if Ubuntu/WSL is already installed) and run:

      irm https://raw.githubusercontent.com/vashu555/ha-swarm-poc/main/start-node.ps1 | iex

  What it does, all inside Ubuntu (WSL):
    1. checks WSL + Ubuntu      2. turns on systemd            3. installs Docker Engine, NetBird, git, python3
    4. downloads this project   5. starts the node console     6. keeps Ubuntu running
    7. opens http://127.0.0.1:9790 in your browser: there you connect to NetBird and choose who this PC becomes.
  Running it again is safe: it only updates and reopens.
#>

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/vashu555/ha-swarm-poc.git'
$AppDir  = '/opt/ha-swarm-poc'
$Console = 'http://127.0.0.1:9790'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Info($text) { Write-Host "   $text" }

# Runs a bash script inside Ubuntu as root (no sudo password needed). Throws if it fails.
# The script goes through a temporary file with Unix line endings: text piped from PowerShell gets "\r\n" added,
# and that invisible "\r" breaks the last command.
function Invoke-Ubuntu([string]$Bash) {
    $file = Join-Path $env:TEMP ("ha-node-step-{0}.sh" -f [guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($file, (($Bash -replace "`r", "") + "`n"), (New-Object Text.UTF8Encoding $false))
    try {
        $linux = (wsl.exe -d $script:Distro -e wslpath -a $file | Out-String).Trim()
        wsl.exe -d $script:Distro -u root -e bash $linux
        if ($LASTEXITCODE -ne 0) { throw "a step inside Ubuntu failed (exit code $LASTEXITCODE)" }
    } finally { Remove-Item $file -ErrorAction SilentlyContinue }
}

# ------------------------------------------------------------------ 1. WSL and Ubuntu
Step "1/7  WSL and Ubuntu"
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Info "WSL is not available on this PC. In an ADMINISTRATOR PowerShell run:  wsl --install -d Ubuntu"
    Info "Restart the PC, open 'Ubuntu' from the Start menu once to create your user, then run this command again."
    return
}
$names = @((wsl.exe -l -q 2>$null) | ForEach-Object { ($_ -replace "`0", "").Trim() } | Where-Object { $_ })
$script:Distro = $names | Where-Object { $_ -like 'Ubuntu*' } | Select-Object -First 1
if (-not $script:Distro) {
    Info "Ubuntu is not installed in WSL. In an ADMINISTRATOR PowerShell run:  wsl --install -d Ubuntu"
    Info "Restart the PC if asked, open 'Ubuntu' from the Start menu once to create your user, then run this command again."
    return
}
Info "using WSL distro '$script:Distro'"

# ------------------------------------------------------------------ 2. systemd
Step "2/7  systemd inside Ubuntu (Docker and NetBird run as services)"
$conf = (wsl.exe -d $script:Distro -u root -- cat /etc/wsl.conf 2>$null) -join "`n"
if ($conf -match 'systemd\s*=\s*true') {
    Info "already on"
} else {
    Invoke-Ubuntu @'
if grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then sed -i '/^\[boot\]/a systemd=true' /etc/wsl.conf
else printf '\n[boot]\nsystemd=true\n' >> /etc/wsl.conf; fi
'@
    wsl.exe --terminate $script:Distro | Out-Null
    Start-Sleep -Seconds 3
    Info "turned on (Ubuntu restarted)"
}

# ------------------------------------------------------------------ 3. Docker Engine, NetBird, tools
Step "3/7  Docker Engine, NetBird, git, python3 (first time: a few minutes)"
try {
    Invoke-Ubuntu @'
set -e
export DEBIAN_FRONTEND=noninteractive
if docker info 2>/dev/null | grep -qi 'Operating System: Docker Desktop'; then
  echo "   Docker Desktop is connected to this Ubuntu. The swarm needs Docker Engine INSIDE Ubuntu:"
  echo "   Docker Desktop > Settings > Resources > WSL integration: switch Ubuntu off (or quit Docker Desktop), then run again."
  exit 3
fi
need=""
for c in git python3 openssl curl ip; do command -v $c >/dev/null || need="$need $c"; done
# (</dev/null: no command here may wait for keyboard input)
if [ -n "$need" ]; then apt-get update -qq </dev/null && apt-get install -y -qq git python3 openssl curl iproute2 </dev/null >/dev/null; fi
if ! command -v docker >/dev/null; then echo "   installing Docker Engine ..."; curl -fsSL https://get.docker.com </dev/null | sh >/dev/null; fi
systemctl enable --now docker </dev/null >/dev/null 2>&1
u=$(getent passwd 1000 | cut -d: -f1); [ -n "$u" ] && usermod -aG docker "$u" </dev/null || true
if ! command -v netbird >/dev/null; then echo "   installing NetBird ..."; curl -fsSL https://pkgs.netbird.io/install.sh </dev/null | sh >/dev/null; fi
systemctl enable --now netbird </dev/null >/dev/null 2>&1 || true
echo "   Docker $(docker version --format '{{.Server.Version}}'), $(netbird version 2>/dev/null | head -1 | sed 's/^/NetBird /')"
'@
} catch {
    Info "Stopped: fix the message above and run the command again."
    return
}

# ------------------------------------------------------------------ 4. the project
Step "4/7  Download / update the project into Ubuntu ($AppDir)"
Invoke-Ubuntu (@'
set -e
if [ -d __DIR__/.git ]; then git -C __DIR__ pull -q --ff-only </dev/null; else git clone -q __REPO__ __DIR__ </dev/null; fi
echo "   $(git -C __DIR__ log -1 --format='version %h, %cd' --date=short)"
'@ -replace '__DIR__', $AppDir -replace '__REPO__', $RepoUrl)

# ------------------------------------------------------------------ 5. the node console
Step "5/7  Start the node console (a service inside Ubuntu, port 9790)"
Invoke-Ubuntu "bash $AppDir/console/install-service.sh"

# ------------------------------------------------------------------ 6. keep Ubuntu running
Step "6/7  Keep Ubuntu running now and after Windows logon"
$helper = Join-Path "\\wsl.localhost\$script:Distro" ($AppDir.TrimStart('/').Replace('/', '\') + '\windows\keep-wsl-alive.ps1')
& $helper -Distro $script:Distro -InstallLogonTask
Info "Keep this PC awake while the POC runs. Stop the HA Swarm WSL task before intentionally shutting down Ubuntu."

# ------------------------------------------------------------------ 7. open the console
Step "7/7  Open the console"
$up = $false
for ($i = 0; $i -lt 40; $i++) {
    try { Invoke-RestMethod "$Console/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { Start-Sleep -Seconds 1 }
}
if (-not $up) {
    Info "The console did not answer on $Console. Check inside Ubuntu:  sudo systemctl status ha-node-console"
    return
}
$state = Invoke-RestMethod "$Console/api/state"
if ($state.swarm.is_main) {
    Start-Process "$Console/#/zone"
    Start-Process "$Console/#/combined"
    Info "This PC is the MAIN PC: opened 'My zone' and 'All zones'."
} else {
    Start-Process "$Console/"
    if ($state.swarm.region) { Info "This PC is $($state.swarm.region)." } else { Info "Choose who this PC becomes in the browser window." }
}
Info "The console is always at $Console on this PC."
