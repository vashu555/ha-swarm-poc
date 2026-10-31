#!/usr/bin/env bash
# Failure drills. Run on a manager (the first PC, unless that is the one you take down).
#   scripts/chaos.sh region-down <region>   planned outage: Swarm stops every task on that PC
#   scripts/chaos.sh region-up   <region>   bring it back
#   scripts/chaos.sh stop  <service>        e.g. stop pg-1, stop kafka-2, stop api-3  (scale to 0)
#   scripts/chaos.sh start <service>
#   scripts/chaos.sh real-cut               how to cut a region for real (network failure)
set -euo pipefail
source "$(dirname "$0")/../env.sh"

node_of() { docker node ls -q --filter node.label=region="$1"; }
cmd="${1:-}"; arg="${2:-}"

case "$cmd" in
  region-down)
    region_ok "$arg"; n=$(node_of "$arg")
    [ "$n" != "$(docker info -f '{{.Swarm.NodeID}}')" ] || die "run this from another PC: draining yourself would stop the containers you are watching from"
    docker node update --availability drain "$n" >/dev/null
    echo "region $arg drained: its pinned services (pg, etcd, kafka, …) stop and wait, movable ones would move."
    echo "watch:  watch -n2 scripts/status.sh" ;;
  region-up)
    region_ok "$arg"; docker node update --availability active "$(node_of "$arg")" >/dev/null
    echo "region $arg active again: its services start and rejoin their clusters." ;;
  stop)
    [ -n "$arg" ] || die "which service? e.g. pg-1"
    docker service scale "${STACK}_$arg=0" ;;
  start)
    [ -n "$arg" ] || die "which service? e.g. pg-1"
    docker service scale "${STACK}_$arg=1" ;;
  real-cut)
    cat <<'EOF'
A real region failure is a network cut, not a polite drain. On the PC of the region to cut:
   sudo netbird down          cut it off          sudo netbird up     reconnect
Or switch that PC off / close WSL there (wsl --shutdown).

What you will see:
 - The other two managers mark the PC "Down" after a few missed heartbeats (seconds).
 - Its containers KEEP RUNNING on the cut-off PC: Swarm cannot stop what it cannot reach. That is exactly why the
   lab uses majority votes: the cut-off PostgreSQL demotes itself, its router fences itself, and RabbitMQ
   (pause_minority) pauses it, while the other two regions carry on.
 - Pinned services of that region show "pending" elsewhere: they cannot move without their disk.
EOF
    ;;
  *) sed -n '2,8p' "$0"; exit 1 ;;
esac
