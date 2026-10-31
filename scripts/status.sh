#!/usr/bin/env bash
# Where everything runs, per region, plus a look inside the lab (database roles, API health). Run on any manager.
#   scripts/status.sh              watch -n2 scripts/status.sh
set -uo pipefail
source "$(dirname "$0")/../env.sh"

say "Regions"
for r in $REGIONS; do
  n=$(docker node ls -q --filter node.label=region="$r")
  if [ -z "$n" ]; then printf '\n-- %s: no PC labelled\n' "$r"; continue; fi
  docker node inspect -f '{{printf "\n-- %s" (index .Spec.Labels "region")}}  {{.Description.Hostname}}  {{.Status.Addr}}  node={{.Status.State}}  availability={{.Spec.Availability}}  manager={{.ManagerStatus.Reachability}}' "$n"
  docker node ps "$n" --filter desired-state=running --format '{{.Name}}|{{.CurrentState}}' 2>/dev/null \
    | grep "^${STACK}_\|^infra_\|^hello_" | sed -E 's/\.[0-9]+\|/|/' | sort \
    | awk -F'|' '{printf "   %-22s %s\n", $1, $2}'
done

say "Not running (should be empty once everything is up)"
docker stack ps "$STACK" --filter desired-state=running --format '{{.Name}}|{{.Node}}|{{.CurrentState}}|{{.Error}}' 2>/dev/null \
  | grep -v '|Running' | awk -F'|' '{printf "   %-18s %-16s %-26s %s\n", $1, $2, $3, $4}'

say "Inside the lab"
docker run --rm -i --network ha-lab-backbone "$REGISTRY/ha-lab-app:$TAG" python - <<'EOF' 2>/dev/null || echo "   (lab network not reachable from this PC yet)"
import json, urllib.request
def get(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r: return r.status, r.read()
    except Exception as e: return None, str(e)
for h in ("pg-1", "pg-2", "pg-3"):
    s, b = get(f"http://{h}:8008/cluster")
    if s == 200:
        for m in json.loads(b)["members"]:
            print(f"   PostgreSQL {m['name']:5} {m['role']:13} {m['state']:10} lag={m.get('lag', 0)}")
        break
else:
    print("   PostgreSQL: no Patroni answering yet")
for a in ("api-1", "api-2", "api-3", "api-4"):
    s, _ = get(f"http://{a}:8000/health")
    print(f"   {a}: {'healthy' if s == 200 else 'DOWN'}")
EOF
