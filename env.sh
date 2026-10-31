# Shared settings for every script in this folder. Change things here, not in the scripts.
# (Sourced by the scripts; you never run this file yourself.)

POC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STACK=hal                                        # the HA lab's stack name: services are hal_pg-1, hal_api-1, …
LAB_SRC="${LAB_SRC:-/mnt/d/HA_Trading_Lab}"      # the lab's source code; images are built from it on the FIRST PC only
REGISTRY="${REGISTRY:-127.0.0.1:5000}"           # the swarm's own registry (every node reaches it on 127.0.0.1:5000)
TAG="${TAG:-poc1}"                               # image tag; bump it (poc2, …) to roll out new code
WG_IF="${WG_IF:-wt0}"                            # NetBird's WireGuard interface
REGIONS="mumbai hyderabad delhi"                 # one PC per region (was azure-z1, azure-z2, oracle in the lab)
FIRST_REGION="${FIRST_REGION:-mumbai}"           # the PC that creates the swarm, builds the images, runs the registry

die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok  %s\n' "$*"; }
warn() { printf '   !!  %s\n' "$*"; }

# This PC's NetBird address (100.x.y.z), or nothing if NetBird isn't connected.
netbird_ip() { ip -4 -o addr show "$WG_IF" 2>/dev/null | awk '{print $4}' | cut -d/ -f1; }

# Overlay networks wrap every packet in VXLAN (50 bytes), and VXLAN itself travels inside NetBird's WireGuard
# tunnel. So the overlay MTU must be NetBird's MTU minus 50, or large packets are silently dropped.
overlay_mtu() {
  local m; m=$(cat "/sys/class/net/$WG_IF/mtu" 2>/dev/null || echo 1200)
  echo $((m - 50))
}

region_ok() { [[ " $REGIONS " == *" $1 "* ]] || die "region must be one of: $REGIONS (got '$1')"; }
