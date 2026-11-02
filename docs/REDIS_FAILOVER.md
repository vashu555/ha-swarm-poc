# Redis failover, health reporting and upgrade

Container state is not application readiness. Redis probes now report replication role, link state and writable
replica count; Sentinel probes report responsiveness, TILT and failover quorum. A cached Sentinel primary address
does not count as a live primary. The combined map and flow dashboard show these differences explicitly.

## Why promotion previously stalled

When a held service has zero replicas, its DNS name can disappear. The previous fallback returned SERVFAIL.
Redis's Alpine resolver retried synchronously, blocking the event loop for roughly five seconds; Sentinel
repeatedly entered TILT and could not promote a replica. Running containers still looked green in the flow map.

`ha-lab-negative-dns.service` is a negative-only DNS responder on the host's private Docker bridge. Existing
overlay names are still answered by Docker. Missing names reach this fallback and get immediate NXDOMAIN instead
of a retried SERVFAIL. The helper does not resolve Internet names and does not bind to NetBird/public interfaces.
Install it on every Docker Engine host. The default bridge address is `172.17.0.1`; custom bridge configurations
must set `LAB_FALLBACK_DNS` consistently for installation and stack deployment.

## Existing-PC upgrade

On **each PC**, update the checkout and rerun the Windows `start-node.ps1` command from the README, or inside
Ubuntu run:

```bash
git pull --ff-only
sudo bash scripts/install-lab-dns.sh
```

On the **main PC**, with its existing separate lab source available:

```bash
bash scripts/10-registry-and-images.sh
# Existing one-PC pilot:
bash scripts/30-deploy-lab.sh --pilot
# Three real PCs: use 30-deploy-lab.sh without --pilot only after the documented data migration and preflight.
```

The build step installs the probe/UI/fault-module fixes with rollback copies in the separate lab's `backups/`
directory. It validates the expected source before writing and is safe to rerun. Stack deployment mounts the main
PC's patched controller, dashboard HTML and fault module. Existing data volumes are retained. These commands may
roll services; schedule them while the simulated lab can tolerate an interruption. Refresh the browser once with
Ctrl+F5 to load the updated JavaScript. The main PC still needs the separate lab source; this repository does not
contain a copy of the entire base lab.

For a nondefault bridge, install with `sudo env LAB_FALLBACK_DNS=<docker0-address> bash scripts/install-lab-dns.sh`
and pass the same environment variable to stack deployment. Every PC used by that stack must provide the fallback
at the configured bridge address.

## What happens when Redis fails

The APIs verify signed session tokens locally. Redis login tracking and per-account rate limiting are best effort
and are bypassed on Redis errors. Orders remain in PostgreSQL. Continued API availability therefore does not
prove Redis health; the banner now says when these Redis extras are unavailable. The fallback policy has not been
changed by this repair.

Redis requires one sufficiently current replica for primary writes (`min-replicas-to-write=1`). If two Redis nodes
are down, a remaining primary can be visible but reject writes. That must show as **MASTER · WRITES BLOCKED**, not
healthy recovery. Three healthy Sentinels cannot replace missing Redis data replicas. Recovery timing now waits
for a verified writable primary, and failed Redis operations are excluded from successful flow animation.

## Verified 2 November 2026

Two local-pilot drills measured writable-primary recovery at 5.43 s and 4.81 s from the crash request. In the final
drill, signal/hold confirmation took 653.5 ms, and the controller observed readiness 3.791 s after confirmed crash.
With two Redis nodes stopped, the remaining primary correctly blocked writes, all Sentinels stayed out of TILT,
and login plus authenticated order-history reads returned HTTP 200. No trading orders were submitted by these
checks. Tested nodes were restored afterward.

These are observed one-PC pilot timings, not guarantees for three PCs or cloud VMs. Cross-host latency,
replication lag, DNS availability, host load and Sentinel election timing can change recovery time. Backups invoked
from the dashboard still require a leader accessible through the local Docker engine; cross-PC backup orchestration
is a separate limitation.

See the [Redis Sentinel documentation](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/)
for DNS requirements, TILT and quorum behavior.
