# Abrupt simulated failures

Crash is a real SIGKILL of a running lab task, followed by a replica-count change to hold the failed service down. Restore asks Swarm to recreate it. It is not a graceful shutdown, planned switchover, or physical PC power-off.

The crash/hold duration and subsequent observed recovery are shown separately at the top of both dashboards. Timings are measured; there is no guaranteed sub-second injection deadline on a loaded host or over a WAN. A healthy peer probe is not proof that every transaction has recovered; consult the audit separately.

## Safety and scope

- Only services in the simulated `hal` stack are eligible. The controller and simulated exchange stay protected.
- Manual injected failures remain held even if auto-heal is enabled. Auto-heal defaults off after controller startup.
- The old graceful Stop/Shut down controls are removed from the failure UI. Swarm network partition/freeze actions are rejected rather than misrepresented as a shutdown.
- A zone drill crashes its lab workloads concurrently. Host managers, Docker, NetBird and consoles remain on. Testing physical PC loss is a separate exercise.
- Every peer must run the updated host console. Remote task crashes require the current manager join token, transported inside the NetBird tunnel; requests also validate the service and container labels. Tokens are never returned in fault responses or committed.
- An unavailable remote crash agent produces an error. It does not silently substitute graceful shutdown.
- The deployment never deletes volumes, reinitializes databases, or accesses IBKR.

## PostgreSQL timing

Patroni currently uses `ttl=20`, `loop_wait=2`, `retry_timeout=4`; 20 seconds is the supported minimum leader lease. A genuine crash cannot promise a 1–2 second safe primary election with this configuration. The old 30-second graceful shutdown delay is absent from a crash drill. Start the drill with healthy replicas and quorum, inject one failure at a time, restore it, and wait for streaming before the next database drill.

The dashboard now verifies a reported primary via Patroni's `/primary` endpoint and excludes stopped or recovering database members from current leader roles. Per-drill timers do not reuse earlier attempts; restoring before takeover cancels that unfinished measurement.

## Installing the lab patch

The host console files ship in this repository. The controller's original source lives separately in `LAB_SRC/app`. `scripts/10-registry-and-images.sh` installs this patch before building if absent, preserving backups under `LAB_SRC/backups`.

For a manual installation from the repository:

```sh
python3 scripts/patch-crash-drills.py /path/to/HA_Trading_Lab/app --backup /private/path/crash-drills-backup
```

An already patched source is not overwritten. Keep the controller and dashboards updated together. Rebuild the app image and deploy the controller update; rebuilding alone does not update an existing service's pinned image digest.

Recovery history is held by the controller for this run; restarting the controller clears that history. Browser reloads do not clear it.
