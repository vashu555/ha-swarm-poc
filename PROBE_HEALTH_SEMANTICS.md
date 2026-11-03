# Probe status and RabbitMQ queue readiness

Container Running, broker availability and notification-queue readiness are separate observations. In a fresh bootstrap, RabbitMQ's `/api/nodes` can report a live broker while the queue endpoint returns HTTP 404 because bootstrap has not yet declared the queue.

The controller now retains node observations when the queue is missing or its probe fails, and separately reports `queue_probe.state` as `available`, `not_created`, or `failed`. HTTP 401/500 and timeouts are not interpreted as a missing queue. The combined map labels a successful Rabbit node check **Broker OK** and displays the separate queue-readiness note.

A missing/None service result means **Probe unknown**. An explicit negative observation still means **Probe failed**. Old snapshots remain stale, and tasks on unavailable hosts remain unverified regardless of their last Docker task state. A PostgreSQL member reported as creating its replica is degraded rather than labelled a failed probe.

`scripts/patch-rabbit-probes.py` installs the controller change idempotently while retaining an exact source backup. The image-build script invokes it for future builds. Existing controllers bind-mount the source; restarting only the controller loads the updated function. Record and restore intended simulator settings around that restart.

Verification uses mocked queue responses, explicit failed probes, absent probe data, in-flight pooler results, and stale snapshots. No live broker or database failure is needed for these tests.
