#!/usr/bin/env bash
# STEP 2 · run on EVERY PC. Connects this PC to your NetBird network with a setup key.
#   scripts/02-join-netbird.sh <SETUP_KEY> [MANAGEMENT_URL]
# MANAGEMENT_URL only if you host NetBird yourself; leave it out for NetBird Cloud (app.netbird.io).
set -euo pipefail
source "$(dirname "$0")/../env.sh"

KEY="${1:-}"; MGMT="${2:-}"
[ -n "$KEY" ] || die "usage: $0 <SETUP_KEY> [MANAGEMENT_URL]   (NetBird dashboard → Setup Keys → Create, reusable)"

if [ -n "$(netbird_ip)" ]; then
  ok "already connected to NetBird as $(netbird_ip)"
else
  say "Connecting to NetBird"
  # MTU 1200, not NetBird's default 1280: some internet paths carry less, and the swarm join then hangs
  sudo netbird up --setup-key "$KEY" --mtu "${NB_MTU:-1200}" ${MGMT:+--management-url "$MGMT"}
  for _ in $(seq 1 30); do [ -n "$(netbird_ip)" ] && break; sleep 1; done
  [ -n "$(netbird_ip)" ] || die "no address on $WG_IF after 30 s: check 'netbird status -d'"
fi

say "This PC on the NetBird network"
echo "   NetBird IP : $(netbird_ip)        ← write this down, with this PC's region"
echo "   MTU        : $(cat /sys/class/net/$WG_IF/mtu)   → overlay MTU will be $(overlay_mtu)"
netbird status | sed 's/^/   /' | head -20

say "Done. When all 3 PCs show 'Peers count: 2/2 Connected' in 'netbird status':"
echo "   on the FIRST PC ($FIRST_REGION):  scripts/03-swarm-init.sh $FIRST_REGION"
