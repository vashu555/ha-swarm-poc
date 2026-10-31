# Run on each Windows PC after logging in (right-click → Run with PowerShell).
# WSL stops Ubuntu when no window uses it, and a stopped Ubuntu means this PC drops out of the swarm.
# This starts a hidden, do-nothing process inside Ubuntu so it keeps running. Close it with: wsl --shutdown

Start-Process wsl.exe -ArgumentList '-d', 'Ubuntu', '--exec', 'sleep', 'infinity' -WindowStyle Hidden
Write-Host "Ubuntu (WSL) will keep running in the background."

# A sleeping PC also drops out. To stop this PC sleeping while plugged in (run once, optional):
#   powercfg /change standby-timeout-ac 0
