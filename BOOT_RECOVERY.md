# Docker, NetBird and WSL startup

`console/install-service.sh` installs a Docker service drop-in with `After=netbird.service` and `Wants=netbird.service`. Its pre-start helper waits up to 90 seconds for an IPv4 address on `wt0`. The wait is bounded so Docker remains usable before NetBird is configured or while it is offline.

`ha-docker-netbird-recovery.timer` checks every 30 seconds after startup. It restarts Docker only if all of these hold:

- Docker is already running.
- Docker reports swarm state `error` with the specific failed-listen / cannot-assign-address startup error.
- The exact failed IPv4 bind address is now present on the NetBird interface.
- At least five minutes have elapsed since the last automatic recovery attempt.

It does not recover quorum, reset a swarm, rotate tokens, delete containers, or start a Docker service the user deliberately stopped. A recovery restart briefly interrupts containers managed by this Docker daemon. Journal entries identify the reason for the restart.

`start-node.ps1` installs a per-user Windows logon task through `windows/keep-wsl-alive.ps1 -InstallLogonTask`. The task runs a hidden watcher and keeps the chosen Ubuntu distro open with `sleep infinity`. The helper is copied into `%LOCALAPPDATA%\HA-Swarm-POC` so it can start before WSL is running. It resumes at that Windows user's logon, not before login.

To stop the watcher intentionally (replace Ubuntu if your distro has a different name):

```powershell
Disable-ScheduledTask -TaskName 'HA Swarm WSL - Ubuntu'
Stop-ScheduledTask -TaskName 'HA Swarm WSL - Ubuntu'
```

An already running `wsl.exe ... sleep infinity` may still keep Ubuntu open. An intentional WSL termination also interrupts any other workloads in that distro; obtain the user's approval first. Re-enable/start the task afterward to restore unattended keep-alive.

Validation:

```sh
python3 test_boot_recovery.py
systemctl cat docker.service
systemctl status ha-docker-netbird-recovery.timer
journalctl -u ha-docker-netbird-recovery.service
docker info --format '{{.Swarm.LocalNodeState}}'
```

Helper tests simulate missing, late and mismatched addresses, a stopped Docker service, a healthy swarm, unrelated swarm errors, and the restart cooldown. They do not substitute for an approved cold-boot/WSL restart test.
