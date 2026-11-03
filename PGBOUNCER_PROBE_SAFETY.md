# Dashboard PgBouncer probe safety

The controller caches a raw libpq connection for each PgBouncer admin endpoint and submits checks to a thread pool. A status collection timeout does not stop the underlying check. A subsequent collection could therefore submit another operation on the same native connection.

The repair adds a nonblocking lock per endpoint around the existing probe. While an earlier probe is still active, the next collection receives Unknown (`None`), not a fabricated healthy result. Different poolers are checked independently and the lock is released on return or exception. The existing admin protocol and connection code are preserved.

Build installs `scripts/patch-pgbouncer-probe.py` idempotently into the separate lab source, retaining an exact source backup when it changes. The current Swarm controller bind-mounts that source file, so installing the source patch and restarting only `hal_control` applies the repair without replacing database volumes. A controller restart resets its in-memory settings; record and restore the desired simulated trader count.

Verification: `python3 -m unittest test_pgbouncer_probe` exercises overlapping probes, independent hosts, exception recovery, and idempotent source transformation. It never contacts Docker or a database.

Observed during the first three-PC bootstrap: the controller exited with code 139 and the kernel reported a general protection fault in libpq. That native crash and the shared-connection race are verified observations; without a native backtrace, the precise crashing call is not conclusively identified. This patch removes the unsafe concurrent-use path.
