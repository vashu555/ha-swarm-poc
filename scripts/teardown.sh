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
    python3 "$POC_DIR/console/console.py" --leave ;;
  *) sed -n '2,6p' "$0"; exit 1 ;;
esac

cat <<EOF

Data volumes are still on each PC. To wipe the lab's data on a PC (cannot be undone), run THERE:
   docker volume ls -q --filter label=com.docker.stack.namespace=$STACK | xargs -r docker volume rm
EOF
