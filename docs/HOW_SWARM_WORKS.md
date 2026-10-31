# How Docker Swarm works, explained on this POC

## 1. The one idea: you describe, Swarm keeps it true

With `docker compose` you say *"start these containers on this PC"* and it does it once.
With Swarm you say *"this is how the system should look"*, and Swarm keeps making reality match, forever:

```
  you:     "pg-1: 1 copy, on the PC labelled region=mumbai, 512 MB"      ← desired state (stacks/hal.yml)
  Swarm:   checks all the time: is it so?  no → fixes it  (start, restart, move, replace)
```

If a container crashes, Swarm starts a new one. If a PC disappears, Swarm notices and moves whatever is allowed to
move. You never start containers by hand; you change the description and deploy it again.

## 2. Nodes: managers and workers

Every PC that joins the swarm is a **node**.

| Role | Job | In this POC |
|---|---|---|
| **Manager** | Keeps the desired state, decides where containers run, answers `docker service …` commands | all 3 PCs |
| **Worker** | Only runs the containers it is given | (every manager is also a worker) |

The managers keep **one shared copy of the desired state** and agree on every change by **majority vote (Raft)**,
the same idea as etcd in the lab:

```
   PC 1 mumbai          PC 2 hyderabad        PC 3 delhi
   manager (leader) ◄──► manager          ◄──► manager
          \___________ 2 of 3 must agree to change anything ___________/
```

- 1 PC lost → 2 of 3 still agree → everything keeps working, including deploys.
- 2 PCs lost → no majority → **running containers keep running**, but nothing can be deployed, scaled or moved
  until a second manager is back.

Managers talk to each other on **TCP 2377**, encrypted with certificates Swarm creates and renews by itself
(its own little certificate authority). The join token is how a new PC proves it may join.

## 3. Services, tasks, containers

| Word | Meaning | Example |
|---|---|---|
| **Stack** | a group of services deployed from one file | `hal` (from `stacks/hal.yml`) |
| **Service** | the description: image, settings, how many copies, where | `hal_pg-1` |
| **Task** | one copy of a service, given to one node | `hal_pg-1.1` |
| **Container** | what actually runs on that node for the task | `hal_pg-1.1.x7k2…` |

A task is given to a node once and stays there. If its container dies, the task fails and Swarm creates a **new task**
(possibly on another node, if the placement rules allow it).

## 4. The scheduler: who runs where

When a task needs a home, the leader picks a node that passes every rule:

| Rule (in the stack file) | Effect | Used here |
|---|---|---|
| `constraints: [node.labels.region == mumbai]` | only nodes with this label | every lab service is pinned to its region |
| `preferences: [spread: node.labels.region]` | spread copies evenly over the label's values | the demo: one copy per region |
| `max_replicas_per_node: 2` | at most N copies on one node | the demo |
| `resources.limits.memory` | the container may not use more | every service |

Labels are put on the PCs once: `docker node update --label-add region=mumbai <node>` (the setup scripts do this).

**Why the lab pins everything.** PostgreSQL, Kafka, etcd, RabbitMQ and Redis keep their data in a **volume on the
PC's own disk**. A volume does not move between PCs, so moving pg-1 to another PC would start it with an empty
disk. So these services are pinned: if their PC is lost, they *wait* (shown as "pending"), and the lab's own
replication (Patroni, Kafka, RabbitMQ quorum, Sentinel) keeps the system running on the other two regions.
Swarm keeps the processes alive; the databases keep the data safe. Each does what it is good at.

The demo (`stacks/hello.yml`) is the opposite: stateless, so when a region is lost its copy **moves** to another PC.

## 5. Networking: three layers

```
 container api-1 (PC 1) ──► "pgbouncer-2" ──► container pgbouncer-2 (PC 2)
        │ overlay network ha-lab-backbone (10.0.x.x, one flat network over all PCs)
        │ packet wrapped in VXLAN (UDP 4789) and sent to PC 2's NetBird address
        │ NetBird / WireGuard tunnel wt0 (100.x.y.z, encrypted, works through routers and NAT)
        ▼ the internet / office LAN
```

1. **NetBird (underlay).** Gives each PC a fixed private address (100.x.y.z) and an encrypted tunnel to the
   others, even behind home routers. Swarm is told to use *only* these addresses (`--advertise-addr`,
   `--data-path-addr`, `--listen-addr` in the scripts), so nothing Swarm does is exposed on the LAN or internet.
2. **Overlay network.** One virtual network spanning all PCs. Containers on different PCs talk as if they were on
   one switch. Each packet is wrapped in **VXLAN** (UDP 4789) and sent through the NetBird tunnel.
3. **Service discovery (DNS).** Inside the overlay, every service name resolves through Docker's DNS (127.0.0.11).
   That is why `pg-1`, `kafka-2`, `rabbit-3`… work unchanged from the single-PC lab.

**MTU (the one trap).** Every layer adds headers, and the internet path between two PCs may carry less than you'd
think. NetBird's default is 1280 bytes per packet, but between our first two PCs only ~1215 got through: small pings
worked while the swarm join (a TLS handshake with full-size packets) silently timed out. So we set NetBird to **1200**
(`--mtu 1200`, done by the console), and the overlay to 1200 − 50 (VXLAN) = **1150**. With too big an MTU, small
requests work but big ones (a long order list, a WebSocket batch) silently hang. `03-swarm-init.sh` and the stack
set this; `05-check.sh` tests it. Measure a path with `ping -M do -s <size> <peer>`.

**Two ways a name resolves:**

| Mode | `api-1` resolves to | Used for |
|---|---|---|
| **VIP** (default) | one virtual IP; Swarm load-balances to the copies behind it | the demo |
| **dnsrr** | the container's own IP | every lab service: one copy each, and VIP silently cuts connections idle for 15 min |

**Publishing ports to the outside:**

| Mode | Who answers | Used for |
|---|---|---|
| **ingress** ("routing mesh") | *every* PC answers on the port and forwards to a PC that runs a copy | demo 8088, registry 5000 |
| **host** | only the PC running the container | HAProxy 8443/8404 (PC 1 and PC 2), dashboard 9700 (PC 1) |

## 6. One order across the three PCs

```
 trader ──https──► PC 2:8443  haproxy-2 (hyderabad)
                       │  round robin over api-1..4 (health check every 1 s)
                       ▼
                   api-1 on PC 1 (mumbai)      ← crossed from PC 2 to PC 1 through the overlay
                       │
                       ▼
                   pgbouncer-1 ──► PostgreSQL leader, e.g. pg-2 on PC 2
                       │           commit confirmed once pg-1 OR pg-3 (another PC) also has it
                       ▼
                   outbox → relay → Kafka (3 brokers on 3 PCs) → router → exchange (sim, PC 1)
```

Nothing in the application changed: it still uses names, and the overlay carries them across PCs.

## 7. Configs and secrets

Files the services need (`haproxy-edge.cfg`, `rabbitmq.conf`, the Redis/Sentinel/RabbitMQ start scripts) become
**Swarm configs**; the certificate becomes a **Swarm secret** (stored encrypted by the managers, mounted in memory at
`/run/secrets/`). Swarm copies them to whichever PC runs the task, so no PC needs this folder except the one you
deploy from.

They can't be edited in place. `30-deploy-lab.sh` names each one after a hash of its content (`hal-haproxy-edge-3fa1c2d9`):
edit a file, deploy again, and only the services using that file restart with the new version.

## 8. When something fails

| What happens | Who notices | What Swarm does | What the lab does |
|---|---|---|---|
| A container crashes | Docker on that PC, at once | starts a new task (restart policy) | e.g. Patroni rejoins as a replica |
| You stop a service (`chaos.sh stop pg-1`) | — | runs 0 copies as asked | Patroni elects a new leader (~20 s) |
| You drain a region (`chaos.sh region-down delhi`) | — | stops every task on that PC; pinned ones wait | majority of 2 regions carries on |
| A PC dies or loses NetBird | managers, after missed heartbeats (seconds) | marks the node Down; moves movable tasks; pinned ones wait | majority of 2 regions carries on |
| A PC is **cut off** but still running | managers see it Down | can't reach it, so **its containers keep running there** | the cut-off members have 1 vote of 3: PostgreSQL demotes itself, the router fences itself, RabbitMQ pauses (`pause_minority`). No second leader. |
| 2 PCs lost | — | no manager majority: nothing changes, containers keep running | writing stops on purpose; nothing confirmed is lost |

The cut-off row is the important lesson: an orchestrator **cannot** stop a machine it can't reach. Data safety must
come from majority votes inside the data systems, which is exactly how the lab is built.

## 9. Updates without downtime

Each service has an `update_config`:

- **stop-first** (databases, brokers): stop the old container, then start the new one. Never two copies on one disk.
- **start-first** (APIs): start the new one, wait until it runs, then stop the old one, so there's no gap.
- **parallelism 1, delay 10s**: one service at a time.
- **failure_action**: `rollback` for APIs (a broken new version goes back by itself), `pause` for data services
  (a person looks first).

Rolling out new code: build with a new tag, deploy with that tag (see README). This replaces the manual
one-at-a-time recreation of the single-PC lab.

## 10. Who does what

| Concern | Handled by |
|---|---|
| PCs reaching each other securely | **NetBird** (WireGuard) |
| Which container runs on which PC, restarting, updates, configs, secrets | **Docker Swarm** |
| Containers on different PCs talking by name | **Swarm overlay network + DNS** |
| Sending traders to a healthy API | **HAProxy** (health check every 1 s) |
| Which database copy is the leader, no lost commits | **Patroni + etcd** (2 of 3 votes) |
| No lost or doubled messages | **Kafka** (acks=all, 2 of 3), **RabbitMQ** quorum queues, outbox, `client_ref` |
| One active router | the **lease + epoch** in PostgreSQL |

## 11. Commands to remember

```bash
docker node ls                                   # the PCs, their role and state
docker node inspect <node> --pretty              # one PC: labels, address, resources
docker stack services hal                        # every service: copies wanted vs running
docker service ps hal_pg-1                       # where pg-1 runs, its history and errors
docker service logs -f hal_api-1                 # logs of a service, from whichever PC runs it
docker service scale hal_api-1=0                 # stop a service (1 to start it again)
docker service update --force hal_api-1          # restart a service (new task)
docker node update --availability drain <node>   # empty a PC for maintenance (active to undo)
docker stack deploy -c stacks/hal.yml hal        # apply changes (the scripts add the variables)
docker stack rm hal                              # remove the stack (volumes stay)
```
