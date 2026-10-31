#!/usr/bin/env bash
# Remove things again. Nothing here deletes data volumes: that is printed for you to run by hand if you mean it.
#   scripts/teardown.sh lab      remove the lab stack (each PC keeps its data volumes)
#   scripts/teardown.sh all      remove lab, demo and registry stacks
#   scripts/teardown.sh leave    THIS PC leaves the swarm (run on each PC, the last one is forced)
set -euo pipefail
source "$(dirname "$0")/../env.sh"

case "${1:-}" in
  lab) docker stack rm "$STACK" ;;
  all) docker stack rm "$STACK" hello infra ;;
  leave)
    if [ "$(docker node ls -q 2>/dev/null | wc -l)" -le 1 ]; then docker swarm leave --force
    else
      me=$(docker info -f '{{.Swarm.NodeID}}')
      docker node demote "$me" >/dev/null 2>&1 || true
      docker swarm leave
      echo "left. On a remaining manager remove it from the list: docker node rm $me"
    fi ;;
  *) sed -n '2,6p' "$0"; exit 1 ;;
esac

cat <<EOF

Data volumes are still on each PC. To wipe the lab's data on a PC (cannot be undone), run THERE:
   docker volume ls -q --filter label=com.docker.stack.namespace=$STACK | xargs -r docker volume rm
EOF
