#!/usr/bin/env bash
# STEP 6 · run on the FIRST PC. Starts the swarm's own image registry and builds + pushes the lab's 3 images.
# Every PC then pulls them from 127.0.0.1:5000: the routing mesh forwards that port to the registry's PC.
set -euo pipefail
source "$(dirname "$0")/../env.sh"
[ -d "$LAB_SRC/app" ] || die "lab source not found at $LAB_SRC (set LAB_SRC in env.sh)"

# Keep the separate lab source's existing fixes and install abrupt drills before building.
if ! grep -q 'def swarm_chaos(' "$LAB_SRC/app/control.py"; then
  python3 "$POC_DIR/scripts/patch-crash-drills.py" "$LAB_SRC/app" \
    --backup "$LAB_SRC/backups/crash-drills-$(date -u +%Y%m%dT%H%M%SZ)"
fi
python3 "$POC_DIR/scripts/patch-redis-observability.py" "$LAB_SRC/app" \
  --backup "$LAB_SRC/backups/redis-health-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$POC_DIR/scripts/patch-order-recovery.py" "$LAB_SRC/app" \
  --backup "$LAB_SRC/backups/order-recovery-$(date -u +%Y%m%dT%H%M%SZ)"
say "1/3  Registry (stack 'infra', runs on region=$FIRST_REGION)"
FIRST_REGION="$FIRST_REGION" docker stack deploy --detach=true -c "$POC_DIR/stacks/registry.yml" infra
for _ in $(seq 1 60); do curl -fs "http://$REGISTRY/v2/" >/dev/null 2>&1 && break; sleep 2; done
curl -fs "http://$REGISTRY/v2/" >/dev/null || die "registry not answering on $REGISTRY: 'docker service ps infra_registry'"
ok "registry answers on $REGISTRY"

say "2/3  Build the images from $LAB_SRC"
docker build -t "$REGISTRY/ha-lab-app:$TAG"       "$LAB_SRC/app"
docker build -t "$REGISTRY/ha-lab-postgres:$TAG"  "$LAB_SRC/images/postgres"
docker build -t "$REGISTRY/ha-lab-pgbouncer:$TAG" "$LAB_SRC/images/pgbouncer"

say "3/3  Push them to the registry"
for i in ha-lab-app ha-lab-postgres ha-lab-pgbouncer; do docker push -q "$REGISTRY/$i:$TAG"; done
curl -s "http://$REGISTRY/v2/_catalog"; echo

say "Done. Try the demo first: scripts/20-hello.sh   then the lab: scripts/30-deploy-lab.sh"
