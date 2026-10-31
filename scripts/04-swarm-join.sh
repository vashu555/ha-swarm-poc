#!/usr/bin/env bash
# STEP 4 · run on the SECOND and THIRD PC. Joins the swarm as a manager and labels the PC with its region.
#   scripts/04-swarm-join.sh <region> <TOKEN> <FIRST_PC_NETBIRD_IP>
set -euo pipefail
source "$(dirname "$0")/../env.sh"

REGION="${1:-}"; TOKEN="${2:-}"; MGR="${3:-}"
[ -n "$REGION" ] && [ -n "$TOKEN" ] && [ -n "$MGR" ] || die "usage: $0 <region> <TOKEN> <FIRST_PC_NETBIRD_IP>"
region_ok "$REGION"
state=$(docker info --format '{{.Swarm.LocalNodeState}}')
case "$state" in
  pending|error|locked) die "Docker swarm is $state from an earlier attempt. Use Leave the swarm in the console, wait for completion, then retry. Unlock a locked swarm first." ;;
  active|inactive) ;;
  *) die "Cannot determine Docker swarm membership ($state); check Docker before retrying" ;;
esac
IP=$(netbird_ip); [ -n "$IP" ] || die "NetBird is not connected (run scripts/02-join-netbird.sh first)"

say "1/3  Can this PC reach the first PC ($MGR) through NetBird?"
# NetBird connects lazily and the first packets can be lost, so keep trying for up to a minute
reach=""
for _ in $(seq 1 20); do
  if ping -c 1 -W 2 "$MGR" >/dev/null 2>&1 && timeout 3 bash -c "</dev/tcp/$MGR/2377" 2>/dev/null; then reach=yes; break; fi
  sleep 2
done
[ -n "$reach" ] || die "can't reach $MGR on port 2377 through NetBird after 1 minute: check 'netbird status' on both PCs,
     that the main PC has created the swarm, and that the NetBird policy allows TCP 2377"
ok "reachable, manager port 2377 open"

say "2/3  Join the swarm as a manager, using this PC's NetBird address $IP"
if [ "$(docker info --format '{{.Swarm.LocalNodeState}}')" = active ]; then
  current_token=$(timeout 8 docker swarm join-token -q manager 2>/dev/null || true)
  [ -n "$current_token" ] && [ "$current_token" = "$TOKEN" ] || die "this PC already belongs to another swarm or its manager connection is unavailable. Use Leave the swarm before retrying; its labels have not been changed."
  ok "already in the requested swarm"
else
  if ! docker swarm join --token "$TOKEN" --advertise-addr "$IP" --data-path-addr "$IP" --listen-addr "$IP:2377" "$MGR:2377"; then
    state=$(docker info --format '{{.Swarm.LocalNodeState}}')
    [[ "$state" == active || "$state" == pending ]] || die "Docker rejected the join; check the error above"
    warn "Docker is still processing this join; waiting instead of submitting a second join"
  fi
fi

ready=""
for _ in $(seq 1 45); do
  if [ "$(docker info --format '{{.Swarm.ControlAvailable}}')" = true ] && timeout 3 docker node ls >/dev/null 2>&1; then ready=yes; break; fi
  sleep 1
done
[ -n "$ready" ] || die "the manager join has not completed. Check connectivity. If it remains pending, use Leave the swarm, wait for completion, then retry."

say "3/3  Label this PC as region=$REGION"
docker node update --label-add region="$REGION" "$(docker info -f '{{.Swarm.NodeID}}')" >/dev/null
ok "labelled"
docker node ls

say "Done. When all three PCs have joined, on any PC: scripts/05-check.sh"
