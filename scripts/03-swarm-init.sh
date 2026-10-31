#!/usr/bin/env bash
# STEP 3 · run on the FIRST PC only. Creates the swarm on the NetBird address.
#   scripts/03-swarm-init.sh mumbai
set -euo pipefail
source "$(dirname "$0")/../env.sh"

REGION="${1:-$FIRST_REGION}"; region_ok "$REGION"
IP=$(netbird_ip); [ -n "$IP" ] || die "NetBird is not connected (run scripts/02-join-netbird.sh first)"
MTU=$(overlay_mtu)

say "1/4  Create the swarm on $IP"
if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" = active ]; then
  ok "this PC is already in a swarm"
else
  # --advertise-addr: the address other nodes use to reach this manager
  # --data-path-addr: the address container-to-container (VXLAN) traffic leaves from
  # --listen-addr   : the manager port 2377 listens on the NetBird address only, not on the LAN
  docker swarm init --advertise-addr "$IP" --data-path-addr "$IP" --listen-addr "$IP:2377"
fi

say "2/4  Re-create the 'ingress' network with MTU $MTU (it defaults to 1500, too big for NetBird)"
cur=$(docker network inspect ingress -f '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null || true)
if [ "$cur" = "$MTU" ]; then ok "ingress already has MTU $MTU"
else
  [ -z "$(docker service ls -q)" ] || die "services exist already; remove them before re-creating ingress"
  echo y | docker network rm ingress >/dev/null
  sleep 3
  docker network create --driver overlay --ingress --opt com.docker.network.driver.mtu="$MTU" ingress >/dev/null
  ok "ingress re-created with MTU $MTU"
fi

say "3/4  Label this PC as region=$REGION"
docker node update --label-add region="$REGION" "$(docker info -f '{{.Swarm.NodeID}}')" >/dev/null
ok "labelled"

say "4/4  Join command for the other two PCs"
TOKEN=$(docker swarm join-token manager -q)
cat <<EOF
   All three PCs join as MANAGERS (3 managers: any one PC can be lost and the swarm still works).

   On the PC for hyderabad:   scripts/04-swarm-join.sh hyderabad $TOKEN $IP
   On the PC for delhi:       scripts/04-swarm-join.sh delhi     $TOKEN $IP

   The token is a password for your swarm: don't share it outside the team.
EOF
