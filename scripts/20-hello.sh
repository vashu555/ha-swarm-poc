#!/usr/bin/env bash
# STEP 7 (demo) · run on any PC. A tiny web service with 3 copies, one per region, to SEE how Swarm works:
# where it places them, how any PC answers on port 8088 (routing mesh), and how copies move when a PC is lost.
#   scripts/20-hello.sh          deploy and show
#   scripts/20-hello.sh rm       remove the demo
set -euo pipefail
source "$(dirname "$0")/../env.sh"

if [ "${1:-}" = rm ]; then docker stack rm hello; exit 0; fi

docker stack deploy --detach=true -c "$POC_DIR/stacks/hello.yml" hello
say "Waiting for 3 copies"
for _ in $(seq 1 60); do
  [[ "$(docker service ls --filter name=hello_web --format '{{.Replicas}}')" == 3/3* ]] && break; sleep 2   # "3/3 (max 2 per node)"
done
docker service ps hello_web --filter desired-state=running --format '   {{.Name}}  on {{.Node}}  {{.CurrentState}}'

say "Ask every PC on port 8088: each answer names the container that served it"
for n in $(docker node ls -q); do
  addr=$(docker node inspect -f '{{.Status.Addr}}' "$n"); region=$(docker node inspect -f '{{index .Spec.Labels "region"}}' "$n")
  for _ in 1 2 3; do
    who=$(curl -s -m 3 "http://$addr:8088" | awk '/^Hostname/ {print $2}')
    echo "   asked $region ($addr) → answered by ${who:-NO ANSWER}"
  done
done

cat <<'EOF'

== Now try it yourself
   Switch a PC off (or on that PC: sudo netbird down), then watch the copy move to another PC:
     watch -n1 "docker service ps hello_web --filter desired-state=running"
   Bring it back and rebalance:  docker service update --force hello_web
   Remove the demo:              scripts/20-hello.sh rm
EOF
