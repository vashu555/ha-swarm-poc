#!/usr/bin/env bash
# Installs the node console as a systemd service in Ubuntu (run as root; start-node.ps1 does this for you).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
bash "$DIR/scripts/install-lab-dns.sh"
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/10-wait-netbird.conf <<EOF
[Unit]
After=netbird.service
Wants=netbird.service

[Service]
ExecStartPre=/usr/bin/python3 "$DIR/scripts/docker-netbird-boot.py" --wait
EOF
cat > /etc/systemd/system/ha-docker-netbird-recovery.service <<EOF
[Unit]
Description=Recover Docker only from a late NetBird bind address
After=netbird.service docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 "$DIR/scripts/docker-netbird-boot.py" --recover
TimeoutStartSec=150
EOF
cat > /etc/systemd/system/ha-docker-netbird-recovery.timer <<'EOF'
[Unit]
Description=Check for the NetBird Docker boot race

[Timer]
OnBootSec=30s
OnUnitInactiveSec=30s
AccuracySec=5s
Unit=ha-docker-netbird-recovery.service

[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/ha-node-console.service <<EOF
[Unit]
Description=HA Swarm node console (http://127.0.0.1:9790)
After=network-online.target docker.service netbird.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 $DIR/console/console.py
WorkingDirectory=$DIR
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ha-docker-netbird-recovery.timer >/dev/null 2>&1
systemctl enable ha-node-console >/dev/null 2>&1
systemctl restart ha-node-console
echo "node console running on port 9790"
