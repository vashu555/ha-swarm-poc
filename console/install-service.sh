#!/usr/bin/env bash
# Installs the node console as a systemd service in Ubuntu (run as root; start-node.ps1 does this for you).
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
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
systemctl enable ha-node-console >/dev/null 2>&1
systemctl restart ha-node-console
echo "node console running on port 9790"
