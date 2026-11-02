#!/usr/bin/env bash
# STEP 8 · run on any manager (normally the first PC). Deploys the whole HA Trading Lab over the 3 regions.
# Run it again after changing stacks/hal.yml or a file in config/: Swarm only changes what changed.
#   scripts/30-deploy-lab.sh
#   scripts/30-deploy-lab.sh --pilot           all 3 zones on THIS PC (same services and zone labels): try it first
#   RESOLVE=never scripts/30-deploy-lab.sh     if you copied the images to each PC by hand (docker load)
set -euo pipefail
source "$(dirname "$0")/../env.sh"
PILOT=""; [ "${1:-}" = --pilot ] && PILOT=yes

say "Preflight"
if [ -n "$PILOT" ]; then
  me=$(docker node inspect self -f '{{index .Spec.Labels "region"}}' 2>/dev/null || true)
  [ "$me" = "$FIRST_REGION" ] || die "the pilot runs on the main PC ($FIRST_REGION); this PC is '${me:-not in a swarm}'"
  # same zone labels, but Hyderabad's and Delhi's services are placed on this PC; haproxy-2 moves to 8444 / 8405
  export HYDERABAD_ON="$FIRST_REGION" DELHI_ON="$FIRST_REGION" HAPROXY2_HTTPS=8444 HAPROXY2_STATS=8405
  ok "pilot: all three zones on this PC ($(hostname))"
else
  python3 "$POC_DIR/scripts/three-pc-preflight.py" || die "three reachable, ready, active region managers are required"
  if docker service inspect "${STACK}_api-3" -f '{{json .Spec.TaskTemplate.Placement.Constraints}}' 2>/dev/null | grep -Eq '==[[:space:]]*mumbai'; then
    [ "${PILOT_DATA_MIGRATION_CONFIRMED:-}" = yes ] || die "the pilot's stateful volumes are still on Mumbai. Copy and verify the existing volumes first; see THREE_PC_MIGRATION.md. This deployment has not changed any services."
  fi
  unset MUMBAI_ON HYDERABAD_ON DELHI_ON HAPROXY2_HTTPS HAPROXY2_STATS
  ok "3 region managers ready; distributed placement selected"
fi
if [ -n "$(docker ps -q --filter label=com.docker.compose.project=ha-lab)" ]; then
  die "the single-PC lab (docker compose project ha-lab) is running on this PC. It uses the same names and the RAM:
     cd $LAB_SRC && docker compose down        (keeps all its data volumes)"
fi
if docker network inspect ha-lab-backbone -f '{{.Driver}}' 2>/dev/null | grep -qx bridge; then
  # the single-PC lab's network (stopped containers only): docker compose re-creates it if that lab is started again
  [ "$(docker network inspect ha-lab-backbone -f '{{len .Containers}}')" = 0 ] \
    || die "the old network 'ha-lab-backbone' still has running containers: stop the single-PC lab first"
  docker network rm ha-lab-backbone >/dev/null && ok "removed the single-PC lab's old bridge network (its data volumes stay)"
fi
ok "single-PC lab not running here"

pem="$POC_DIR/config/certs/lab.pem"
if [ ! -s "$pem" ]; then             # not in git: every swarm makes its own self-signed lab certificate
  mkdir -p "$(dirname "$pem")"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1095 -subj "/CN=ha-trading-lab" \
    -addext "subjectAltName=DNS:haproxy-1,DNS:haproxy-2,DNS:localhost,IP:127.0.0.1" \
    -keyout "$pem.key" -out "$pem.crt" 2>/dev/null
  cat "$pem.crt" "$pem.key" > "$pem" && rm -f "$pem.key" "$pem.crt"
  ok "created a self-signed lab certificate (config/certs/lab.pem)"
fi

# Swarm configs and secrets can't be changed in place, so each one's name carries a hash of its file: edit a file,
# deploy again, and only the services that use that file roll over to the new version.
h() { sha256sum "$POC_DIR/config/$1" | cut -c1-8; }
export HAPROXY_VER=$(h haproxy-edge.cfg) RABBITCONF_VER=$(h rabbitmq.conf) RABBITENTRY_VER=$(h rabbit-entry.sh) \
       REDISENTRY_VER=$(h redis-entry.sh) SENTINELENTRY_VER=$(h sentinel-entry.sh) PEM_VER=$(h certs/lab.pem)
export REGISTRY TAG LAB_SRC OVERLAY_MTU=$(overlay_mtu)

say "Deploy stack '$STACK' (images $REGISTRY/*:$TAG, overlay MTU $OVERLAY_MTU)"
docker stack deploy --detach=true --with-registry-auth --resolve-image "${RESOLVE:-always}" \
  -c "$POC_DIR/stacks/hal.yml" "$STACK"

cat <<EOF

== Deployed. It takes 1–3 minutes for PostgreSQL to elect a leader and Kafka to form.
   Watch it:          scripts/status.sh            (or: watch -n2 scripts/status.sh)
   Dashboard:         http://<first PC's NetBird IP>:9700
   Trader page:       https://<mumbai or hyderabad NetBird IP>:8443/live
   HAProxy stats:     http://<mumbai or hyderabad NetBird IP>:8404/stats
EOF
