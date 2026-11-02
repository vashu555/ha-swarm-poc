# Move the existing pilot to three PCs

## Current data policy

The existing pilot's database and durable message history must be migrated. Do not start a fresh dataset, erase volumes, or use a plain placement change as a substitute for transferring data.

The pilot stores all regions' volumes on Mumbai. Docker named volumes are local to each daemon. If a service moves to another PC before its volume is restored there, Docker creates an empty volume with the same name.

## Readiness

1. Update each PC using the published startup command. Keep Windows awake and NetBird connected.
2. Join one manager per region: Mumbai, Hyderabad, Delhi. Verify unique node addresses, Ready/Active status and manager reachability.
3. Run `python3 scripts/three-pc-preflight.py` on Mumbai. It checks live membership, not merely region labels.
4. Open `http://127.0.0.1:9790/#/combined` on Mumbai. The map separates each PC's connection, Docker workload state and service probe results.

## Prepare an inventory before downtime

Run as root on Mumbai, saving outside the public repository:

```sh
python3 scripts/three-pc-preflight.py --plan --output /private/path/pilot-migration-plan.json
```

This is read-only. It lists current services, the existing named volumes required by each destination and their allocated sizes. It is not a backup and does not copy any data.

Arrange authenticated private transfer access to both peers and enough backup space before stopping the pilot. Do not publish archives, manifests containing machine addresses, database data or certificates. Do not assume SSH or file sharing is enabled merely because NetBird is connected.

## Controlled migration procedure

Perform with the user present after the peers, transfer method, storage capacity and rollback locations are verified:

1. Record all current replica counts, placement constraints, service/image versions, roles, current audit and outstanding simulated orders. Preserve the existing uncommitted lab source fixes.
2. Pause generation of new simulated orders and let in-flight processing settle. Record any outstanding work that cannot drain. This procedure never connects to IBKR.
3. Stop the pilot's trading workloads cleanly, preserving the registry, host console, manager processes and all volumes. Obtain approval for the planned outage. Wait until no pilot process is writing its volume.
4. Take consistent backups of the stateful volumes and service configuration. Archive with numeric ownership, permissions and required filesystem attributes preserved. Include the PostgreSQL, etcd, Kafka, RabbitMQ, Redis and Sentinel volumes listed for each destination. Account for the backup repository used by PostgreSQL.
5. Compute archive checksums. Transfer privately to the corresponding PC, then verify checksums there. Retain the originals on Mumbai and a rollback backup.
6. Inspect destination volumes before importing. Never overwrite a populated destination silently. Restore into the exact expected named volumes with the original ownership, and record each successful restoration and its checksum.
7. Only after every required volume is restored and verified, deploy without `--pilot`. The deployment guard requires explicit operator acknowledgement:

   ```sh
   PILOT_DATA_MIGRATION_CONFIRMED=yes bash scripts/30-deploy-lab.sh
   ```

   This acknowledgement does not copy or verify data by itself. It must not be used to skip the preceding steps.

8. Verify actual task placement on three different managers, overlay communication, PostgreSQL replication, etcd quorum, Kafka leaders/ISR, RabbitMQ membership/queues, Redis/Sentinel roles, both API zones and router fencing.
9. Validate retained database/message history and new simulated-order correctness before resuming the normal load. In-memory process state, browser sessions and live connections do not migrate with a disk archive.
10. Retain rollback volumes. A rollback after new orders begin requires reconciling new data; do not simply restart old pilot copies alongside the distributed cluster.

The actual cold-copy/import step is operator-controlled. The readiness tool and map do not perform remote filesystem writes, delete volumes or shut down the pilot.

## Map semantics

- Online: Docker reports the node Ready and its manager link is not flagged unreachable.
- Manager link lost: node/worker status and manager reachability disagree; workload state is shown separately.
- Unreachable: the manager cannot currently establish the node's live status. It does not prove a power-off.
- Not joined: no current member owns that region. Automatic departure cleanup may have removed an old entry.
- Running: Docker task state, not application correctness.
- Probe OK: the specific service probe returned an acceptable result.
- Responding: a stats/management endpoint answered; all dependencies are not thereby proven healthy.
- Probe stale / Unknown: current service health is unavailable. The last snapshot must not remain green indefinitely.

The controller's `status.observed_at` timestamps each completed collection. `scripts/patch-control-observability.py` adds this small change to the existing lab source while preserving a separate exact backup; it does not replace other uncommitted fixes.
