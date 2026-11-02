#!/usr/bin/env bash
# Install on every Docker Engine host; does not restart Docker or workloads.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
TASK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TASK_DNS="${LAB_FALLBACK_DNS:-172.17.0.1}"
python3 - "$TASK_DNS" <<'PY'
import ipaddress, subprocess, sys
address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or not address.is_private or address.is_loopback:
    raise SystemExit('Fallback DNS must use the private local Docker bridge address')
links = subprocess.check_output(['ip','-4','-o','addr','show','docker0'], text=True)
if not any(item.split('/')[0] == str(address) for item in links.split()):
    raise SystemExit('LAB_FALLBACK_DNS does not match docker0; set it to this host bridge address')
PY
cat > /etc/systemd/system/ha-lab-negative-dns.service <<EOF
[Unit]
Description=Fast NXDOMAIN fallback for internal HA lab services
After=docker.service
Wants=docker.service

[Service]
ExecStart=/usr/bin/python3 "$TASK_DIR/scripts/lab-negative-dns.py" --listen "$TASK_DNS"
User=nobody
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now ha-lab-negative-dns.service
systemctl is-active --quiet ha-lab-negative-dns.service
