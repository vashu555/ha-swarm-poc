# PostgreSQL crash delay and order recovery

## Why takeover takes about twenty seconds

The checked pilot runs Patroni 4.0.4 with `ttl=20`, `loop_wait=2`, and `retry_timeout=4` in etcd's dynamic
configuration. The old primary's leader lease can remain valid after its process dies. A surviving eligible
replica waits for lease expiry before election and promotion. Routing, connection recovery and observation add
time. The dashboard previously observed 21.529 seconds from confirmed crash to a replacement primary.

Patroni enforces a minimum `ttl` of 20 seconds. Setting it to two or five seconds is not a supported fix. Reducing
the polling interval does not remove the lease wait. Planned switchover can be faster because the healthy primary
cooperates and releases leadership; it is not the same test as an abrupt process or host failure. Do not delete
the leader key or force promotion merely because a network probe failed: the former primary might still accept
writes on the other side of a partition.

See [Patroni dynamic configuration](https://patroni.readthedocs.io/en/latest/dynamic_configuration.html) and
[Patroni replication modes](https://patroni.readthedocs.io/en/latest/replication_modes.html).

## The current order path

```text
Client retains request + client_ref
  -> API transaction: order + cash reservation + outbox records
  -> PostgreSQL commit / WAL / configured synchronous replication
  -> API acknowledges Received

Committed outbox
  -> relay publishes orders.submit and orders.events to Kafka (acks=all)
  -> router / simulated exchange / fill processing
```

Kafka publication is asynchronous after the PostgreSQL transaction. The relay waits for Kafka delivery before
marking outbox records published. Publication can repeat after an uncertain acknowledgement; consumers must
deduplicate. This does not mean new requests enter Kafka while PostgreSQL has no writable primary.

| Order situation when the primary fails | Where it waits / what happens |
|---|---|
| API already acknowledged Received | The order is committed in PostgreSQL/WAL, with its outbox records. Healthy configured synchronous replication protects it against the tested single-primary failure. |
| Committed but outbox not published | The durable outbox waits for database recovery; the relay resumes afterward. |
| Already published to Kafka | Broker replicas retain it according to topic retention. Processing that requires PostgreSQL can still pause. Existing exchange activity must be reconciled. |
| New request cannot commit during the gap | The caller retains the same payload/reference and retries. It is not durably accepted by the server or automatically queued in Kafka. |
| Commit succeeded but its response was lost | The caller cannot infer failure from a timeout. Retry/reconcile the same `client_ref`; deduplication returns the existing order. |

The simulated traders retain the payload in process memory and retry the same reference, with backoff, up to
30 attempts. This is not a durable client queue across a controller/PC restart. A caller that abandons retries
must treat the outcome as unconfirmed and reconcile it; the dashboard now says **Unconfirmed after retries**.

Accepted is also different from executed. The router expires an unsent market order after five seconds; it does
not blindly execute an old market order after a long outage. Limit orders and orders already at the exchange
follow their status/reconciliation logic.

## Durability limits

The checked configuration uses `synchronous_mode=quorum`, `synchronous_node_count=1`, PostgreSQL
`synchronous_commit=on`, and `ANY 1` standby acknowledgement while both replicas are healthy. A successful commit
then waits for a synchronous standby's durable WAL flush. See [PostgreSQL synchronous replication](https://www.postgresql.org/docs/16/warm-standby.html#SYNCHRONOUS-REPLICATION).

`synchronous_mode_strict` is currently **false**: the system can continue alone if no standby remains. That
trades replicated durability for availability. An unlimited zero-loss guarantee would be incorrect, especially
after successive failures. Strict mode would instead block commits when the required standby is unavailable;
that policy was not silently changed by the dashboard correction.

## Verified simulated-order drill, 2 November 2026

- Placed one nonmarketable limit order and received HTTP 202 before crashing the primary.
- Confirmed the PostgreSQL process crash/hold in 502.3 ms.
- A second order request during the gap timed out; a direct read on a survivor showed no row for that reference.
- Retried that exact payload/reference. The new primary acknowledged it 20.76 seconds after the crash request.
- The previously acknowledged order survived. Replaying its reference returned its original order ID.
- Direct database checks showed exactly one row per test reference. The two test limit orders were cancelled,
  and the crashed PostgreSQL service was restored.

This verifies one controlled process failure in the local three-zone pilot, not power-loss behavior across three
PCs/clouds. The measured outage was retained; no artificial early promotion or shorter reported timer was added.

## If requests must be durably received throughout a database outage

That requires a separate acceptance design: validate and durably append to a replicated ingress log first,
acknowledge a distinct Queued state, and have consumers perform later database/risk/exchange processing. It needs
idempotency, backlog limits, order expiry/cancellation rules and risk policy. Kafka being present elsewhere in the
current application does not supply this acceptance contract automatically. This repair does not rewire the order
path or claim uncommitted requests are already safe in Kafka.
