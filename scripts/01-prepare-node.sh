#!/usr/bin/env bash
# STEP 1 · run on EVERY PC, inside Ubuntu (WSL 2 or a real Linux install).
# Checks the basics and installs Docker Engine and NetBird if they are missing. Safe to run again.
set -euo pipefail
source "$(dirname "$0")/../env.sh"

say "1/5  systemd (NetBird and Docker run as services)"
if [ "$(ps -p 1 -o comm=)" = systemd ]; then ok "systemd is running"
else
  die "systemd is off. In WSL: put these two lines in /etc/wsl.conf
       [boot]
       systemd=true
     then in Windows PowerShell run: wsl --shutdown   and open Ubuntu again."
fi

say "2/5  Docker Engine (not Docker Desktop)"
if ! command -v docker >/dev/null; then
  echo "   Docker is not installed: installing Docker Engine from get.docker.com …"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  die "Docker installed. Close Ubuntu, run 'wsl --shutdown' in Windows, open Ubuntu and run this script again."
fi
docker info >/dev/null 2>&1 || die "docker is installed but not usable: 'sudo systemctl start docker', and make sure you are in the docker group"
if docker info --format '{{.OperatingSystem}}' | grep -qi 'docker desktop'; then
  die "this is Docker Desktop. Swarm across PCs needs Docker Engine inside Ubuntu: quit Docker Desktop
     (and turn off its WSL integration), then run this script again."
fi
ok "Docker Engine $(docker version --format '{{.Server.Version}}')"
sudo bash "$POC_DIR/scripts/install-lab-dns.sh"

say "3/5  NetBird (the private network between the PCs)"
if ! command -v netbird >/dev/null; then
  echo "   NetBird is not installed: installing from pkgs.netbird.io …"
  curl -fsSL https://pkgs.netbird.io/install.sh | sh
fi
sudo systemctl enable --now netbird >/dev/null 2>&1 || true
ok "NetBird $(netbird version 2>/dev/null | head -1)"

say "4/5  Clock (Swarm's certificates and Raft need roughly correct time)"
if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then ok "clock synchronised"
else warn "clock not synchronised. In WSL after sleep: 'sudo hwclock -s' or 'wsl --shutdown' fixes it"; fi

say "5/5  Resources"
mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$mem" -ge 4500 ]; then ok "${mem} MB RAM for Docker"
else warn "only ${mem} MB RAM. This PC's share of the lab needs ~3 GB: raise WSL memory in %UserProfile%\\.wslconfig ([wsl2] memory=6GB)"; fi
df -h /var/lib/docker 2>/dev/null | awk 'NR==2 {print "   ok  disk free for Docker: " $4}'

say "Done. Next: scripts/02-join-netbird.sh <SETUP_KEY>"
