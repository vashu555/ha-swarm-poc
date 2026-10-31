#!/usr/bin/env bash
# STEP 4 · run on the SECOND and THIRD PC. Joins the swarm as a manager and labels the PC with its region.
#   scripts/04-swarm-join.sh <region> <TOKEN> <FIRST_PC_NETBIRD_IP>
set -euo pipefail
source "$(dirname "$0")/../env.sh"

REGION="${1:-}"; TOKEN="${2:-}"; MGR="${3:-}"
[ -n "$REGION" ] && [ -n "$TOKEN" ] && [ -n "$MGR" ] || die "usage: $0 <region> <TOKEN> <FIRST_PC_NETBIRD_IP>"
region_ok "$REGION"
IP=$(netbird_ip); [ -n "$IP" ] || die "NetBird is not connected (run scripts/02-join-netbird.sh first)"

say "1/3  Can this PC reach the first PC ($MGR) through NetBird?"
ping -c 2 -W 2 "$MGR" >/dev/null || die "no ping to $MGR: check 'netbird status' on both PCs and the NetBird access policy"
timeout 3 bash -c "</dev/tcp/$MGR/2377" 2>/dev/null || die "port 2377 on $MGR is closed: is the swarm created there, and does the NetBird policy allow TCP 2377?"
ok "reachable, manager port 2377 open"

say "2/3  Join the swarm as a manager, using this PC's NetBird address $IP"
if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" = active ]; then ok "already in a swarm"
else docker swarm join --token "$TOKEN" --advertise-addr "$IP" --data-path-addr "$IP" --listen-addr "$IP:2377" "$MGR:2377"; fi

say "3/3  Label this PC as region=$REGION"
docker node update --label-add region="$REGION" "$(docker info -f '{{.Swarm.NodeID}}')" >/dev/null
ok "labelled"
docker node ls

say "Done. When all three PCs have joined, on any PC: scripts/05-check.sh"
