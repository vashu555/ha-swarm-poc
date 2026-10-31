#!/usr/bin/env bash
# STEP 5 · run on any PC. Proves the swarm is healthy: 3 managers, every region labelled once, MTU right, and
# containers on different PCs can really talk to each other through the overlay network.
set -euo pipefail
source "$(dirname "$0")/../env.sh"
fail=0

say "1/4  Nodes"
docker node ls
printf '\n   %-22s %-16s %-11s %s\n' HOSTNAME NETBIRD-ADDR REGION STATE
for n in $(docker node ls -q); do
  docker node inspect -f '   {{printf "%-22s" .Description.Hostname}} {{printf "%-16s" .Status.Addr}} {{printf "%-11s" (index .Spec.Labels "region")}} {{.Status.State}}/{{.ManagerStatus.Reachability}}' "$n"
done

say "2/4  Every region on exactly one PC"
for r in $REGIONS; do
  c=$(docker node ls -q --filter node.label=region="$r" | wc -l)
  if [ "$c" = 1 ]; then ok "$r"; else warn "$r is on $c PCs (must be 1)"; fail=1; fi
done
m=$(docker node ls --filter role=manager -q | wc -l)
if [ "$m" = 3 ]; then ok "3 managers (any one PC can be lost)"; else warn "$m managers (want 3)"; fi

say "3/4  MTU"
want=$(overlay_mtu)
ing=$(docker network inspect ingress -f '{{index .Options "com.docker.network.driver.mtu"}}' 2>/dev/null || true)
if [ "$ing" = "$want" ]; then ok "ingress MTU $ing (NetBird $(cat /sys/class/net/$WG_IF/mtu) − 50)"; else warn "ingress MTU '$ing', want $want: see scripts/03-swarm-init.sh"; fail=1; fi

say "4/4  Container-to-container across PCs (a test container on every PC pings all the others)"
docker network create -d overlay --attachable --opt com.docker.network.driver.mtu="$want" poc-nettest >/dev/null 2>&1 || true
docker service create -d --name poc-nettest --mode global --network poc-nettest --restart-condition none \
  alpine:3.20 sleep 300 >/dev/null 2>&1 || true
for _ in $(seq 1 60); do
  run=$(docker service ps poc-nettest --filter desired-state=running --format '{{.CurrentState}}' | grep -c Running || true)
  [ "$run" -ge "$(docker node ls -q | wc -l)" ] && break; sleep 2
done
me=$(docker ps -q --filter name=poc-nettest | head -1)
if [ -z "$me" ]; then warn "test container did not start on this PC"; fail=1
else
  ips=$(docker exec "$me" sh -c "nslookup tasks.poc-nettest 2>/dev/null | awk '/^Address/ && !/#53/ {print \$NF}'")
  for ip in $ips; do
    # 1100-byte pings: big enough to catch a wrong MTU, small enough to fit a correct one
    if docker exec "$me" ping -c 2 -W 2 -s 1100 "$ip" >/dev/null 2>&1; then ok "reached $ip"; else warn "cannot reach $ip"; fail=1; fi
  done
fi
docker service rm poc-nettest >/dev/null 2>&1 || true
sleep 3; docker network rm poc-nettest >/dev/null 2>&1 || true

if [ "$fail" = 0 ]; then say "All good. Next (first PC): scripts/10-registry-and-images.sh"; else die "fix the warnings above first"; fi
