# Local swarm regression tests

The tests use disposable Docker-in-Docker daemons. They do not join the host's swarm or use its token.

Run from the repository root on a Linux Docker host:

```sh
python3 test_departures.py
docker build -t finflock-swarm-qa:local -f Dockerfile.qa .
python3 local_integration.py
python3 test_token_recovery.py
```

The integration tests require permission to create privileged containers and bridge networks. They remove only the containers and networks they created. The recovery test performs `--force-new-cluster` inside its disposable daemon, never on the host daemon.

Covered behavior:

- Three managers join using the real console HTTP API, with one region each.
- Zone down/up stops and restarts a real Alpine workload while managers stay joined.
- Repeated joins are idempotent; an interrupted region-label step can resume.
- Frontend Leave demotes a manager, disconnects it, and permits region cleanup.
- Nodes rejoin with the same test token after a clean leave.
- Quorum loss retains the known local identity and prevents unsafe cleanup.
- Manager connectivity recovers after restarting the daemons.
- Nodes belonging to a foreign swarm are rejected without relabelling.
- Pending joins are not submitted twice; Leave clears them before retrying.
- Abruptly disconnected managers are removed only while a majority remains.
- Previously removed managers clear stale local membership only after a healthy manager of the same swarm confirms their removal.
- Disaster recovery preserves the existing test token, node identity, and labels.

`local-integration-report.json` and `token-recovery-report.json` contain results without join tokens. `qa-alpine.tar` is a generated test fixture, not source code.

These tests do not verify NetBird/NAT routing between physical PCs or PostgreSQL, Kafka, RabbitMQ, and Redis application-level failover.
