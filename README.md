# HA Trading Lab on Docker Swarm: 3 PCs as 3 regions, joined by NetBird

The HA Trading Lab (38 services: PostgreSQL + Patroni + etcd, PgBouncer, Kafka, RabbitMQ, Redis + Sentinel, HAProxy,
our Python services) spread over **three PCs**, each one a "region". Docker Swarm places and runs the containers,
NetBird connects the PCs, and a **node console** in the browser lets anyone join with one PowerShell command.

| Region | Role | Was in the single-PC lab | Services |
|---|---|---|---|
| **mumbai** | **main PC**: creates the swarm, builds images, runs the registry, simulated exchange and lab dashboard | `azure-z1` | 15 |
| **hyderabad** | second region | `azure-z2` | 14 |
| **delhi** | third region, the tie-breaking vote | `oracle` | 9 |

Every stateful system has one member per region, so **any one PC can be lost** and the other two keep a majority.
The exchange is simulated and lab only. Nothing here connects to IBKR or any real market.

---

## Join a PC: one PowerShell command

**You need:** Windows 10/11, about 4 GB of free RAM, internet, and a **NetBird setup key** from the person who runs the
main PC.

**Once per PC, only if Ubuntu (WSL) isn't installed yet:** open PowerShell **as administrator**, run
`wsl --install -d Ubuntu`, restart the PC, open **Ubuntu** from the Start menu once and create a user name and password.

**Then, in a normal PowerShell window:**

```powershell
irm https://raw.githubusercontent.com/22f1000513-Vanshaj/ha-swarm-poc/main/start-node.ps1 | iex
```

The command:

1. checks WSL and Ubuntu, and turns on systemd inside Ubuntu;
2. installs **Docker Engine**, **NetBird**, git and python3 inside Ubuntu (first time: a few minutes);
3. downloads this project into Ubuntu (`/opt/ha-swarm-poc`) and starts the **node console** (a service, port 9790);
4. keeps Ubuntu running in the background, so the PC stays in the swarm;
5. **opens the console in your browser:** <http://127.0.0.1:9790>.

In the browser:

| Step | What you see | What you do |
|---|---|---|
| 1 | *Connect this PC to the private network* | paste the NetBird setup key, click **Connect** |
| 2 | *Who should this PC become?* Three cards: Mumbai (main), Hyderabad, Delhi, each **free** or **taken by &lt;PC&gt;** | click **Become …** on a free region |
| 3 | the steps running live (find the swarm, ask permission, join, label) | wait ~10–30 s |
| 4 | **You are Hyderabad** (or Delhi): this PC's zone, its services, CPU/memory, the database cluster, the leader | nothing, it stays up to date |

**A region that is already taken is refused**, with the name of the PC that has it. Two PCs clicking the same region at
the same moment: the first wins, the second is refused and steps back out.

Run the same command again any time: it updates the project and reopens the console.

## The main PC (Mumbai)

The main PC is the one that **becomes Mumbai first**: with no swarm on the network yet, only Mumbai can be chosen,
and choosing it creates the swarm. Once a swarm exists, Mumbai is refused for everyone else. (For now this is the
project owner's PC: it has the lab's source code in `D:\HA_Trading_Lab`, from which the images are built.)

On the main PC the command opens **two windows**:

- **My zone**: Mumbai's own services, like every other PC;
- **All zones**: every PC (with the current ★ leader), every service in a region-by-service grid, the database roles,
  and the buttons **Build and push images → Deploy the demo → Deploy the lab**, plus **Release** for a region whose
  PC is gone for good.

Before **Deploy the lab** on the main PC, stop the single-PC lab: `cd /mnt/d/HA_Trading_Lab && docker compose down`
(keeps all its data volumes).

## NetBird, once (main PC owner, in a browser)

1. Sign in at **app.netbird.io** (or your own NetBird server).
2. **Setup Keys → Create Setup Key**: *Reusable*, auto-assign group **`ha-swarm`**. Give this key to the people joining.
3. **Access Control → Policies**: allow `ha-swarm` ↔ `ha-swarm` (the default "allow all" also works). If you restrict
   ports: TCP 2377, TCP+UDP 7946, UDP 4789 (Swarm), TCP 9790 (node console), 5000, 8088, 8443, 8404, 9700.

---

## How it fits together

**Three layers, three jobs:**

| Layer | Connects | Tool |
|---|---|---|
| Machines | the 3 PCs to each other, encrypted, through any router or NAT | **NetBird** (WireGuard) gives each PC a fixed 100.x.y.z address |
| Containers | every container to every other, **inside a region and between regions alike**: one flat overlay network over all PCs | **Docker Swarm** (overlay network, VXLAN inside the NetBird tunnel) |
| Data | copies of the data between regions, and who leads | the **databases themselves**: Patroni + etcd, Kafka replication, RabbitMQ quorum queues, Redis Sentinel |

So Swarm is not "the link between zones" by itself: NetBird is the road between the PCs, Swarm runs the containers on
the PCs and lets them reach each other by name (`pg-1`, `kafka-2`, …), and the databases decide on their own how to
copy data and who leads. If a region later has several servers, the same swarm also connects the servers inside it.

**Leader vs main: two different things.**

| | What it is | Who | Changes? |
|---|---|---|---|
| **Swarm leader** | the manager that currently writes the swarm's decisions | elected by the 3 managers (Raft); usually the PC that created the swarm at first | **yes, automatically**: if the leader's PC goes down, the other two elect a new one in seconds. Shown with ★ in the console |
| **Main PC** | our choice: the PC with the registry, exchange, dashboard and deploy buttons | Mumbai (the PC that created the swarm) | no, it is a label (`role=main`) |

All three PCs are **managers**, so all three can be leader. One PC down: the swarm keeps working. Two down: running
containers keep running, but nothing can be changed until one comes back (and the lab stops writing on purpose).

**What runs where** is fixed by labels: each PC is labelled `region=…` when it joins, and every lab service is pinned
to its region's PC, because its data volume lives on that PC's disk. More in [docs/HOW_SWARM_WORKS.md](docs/HOW_SWARM_WORKS.md).

**How a joining PC finds the swarm and is refused if needed:** the console on a joining PC asks every connected NetBird
peer `http://<peer>:9790/peer/info` and finds a manager (the main PC first). It asks that manager
`/peer/enroll` for its region. The manager refuses if a node already carries that region (up or down) or another PC is
joining as it right now; otherwise it hands over a join token. After joining, the PC checks once more and steps back
out if another PC got the same region at the same moment.

---

## Manual way (without the console)

The console runs these same scripts; you can also run them yourself inside Ubuntu, in this folder:

| # | Where | Command |
|---|---|---|
| 1 | all 3 PCs | `bash scripts/01-prepare-node.sh` |
| 2 | all 3 PCs | `bash scripts/02-join-netbird.sh <SETUP_KEY>` |
| 3 | main PC | `bash scripts/03-swarm-init.sh mumbai` |
| 4 | PC 2, PC 3 | `bash scripts/04-swarm-join.sh hyderabad\|delhi <TOKEN> <MAIN_NETBIRD_IP>` |
| 5 | any | `bash scripts/05-check.sh` (3 managers, labels, MTU, containers on different PCs can talk) |
| 6 | main PC | `bash scripts/10-registry-and-images.sh` |
| 7 | any | `bash scripts/20-hello.sh` (demo) |
| 8 | main PC | `bash scripts/30-deploy-lab.sh` |

Day to day: `scripts/status.sh` (where everything runs), `scripts/chaos.sh` (failure drills: `region-down delhi`,
`stop pg-1`, `real-cut`), `scripts/teardown.sh` (`lab`, `all`, `leave`).

## Known limits of this POC

- **Windows browsers can't open other PCs' pages directly** (NetBird runs inside Ubuntu, not in Windows). Each PC's
  console shows its own pages on 127.0.0.1, and the main PC's *All zones* view shows every region. To open another
  PC's pages from Windows, also install NetBird for Windows.
- **The lab dashboard's container boxes and chaos buttons** look for Docker Compose containers, so they don't see
  Swarm's. Audit, traders and order flow work; the node console and `scripts/chaos.sh` replace the rest.
- **Backups** (pgBackRest via the dashboard) only reach the database on the main PC; each PC keeps its own backup volume.
- **Security is POC level:** any NetBird peer can ask a manager for a join token, and the registry, dashboard and
  console API are open to NetBird peers. Your NetBird network (who gets a setup key) is the fence. Lab passwords
  in `stacks/hal.yml` are the lab's defaults. Not for production.
- **A sleeping or closed PC drops out.** The start command keeps Ubuntu running; also turn off sleep.

## Files

| Path | What |
|---|---|
| `start-node.ps1` | the one PowerShell command: installs, downloads, starts and opens the console |
| `console/` | the node console (`console.py`: Python standard library only; `ui.html`: the page; systemd service installer) |
| `env.sh` | settings (regions, image tag, lab source path, registry) |
| `scripts/` | numbered setup steps, then `status.sh`, `chaos.sh`, `teardown.sh` |
| `stacks/hal.yml` | the lab as a Swarm stack: 38 services, each pinned to its region |
| `stacks/hello.yml`, `stacks/registry.yml` | the demo service, the swarm's image registry |
| `config/` | HAProxy, RabbitMQ, Redis, Sentinel files (delivered as Swarm configs); the lab certificate is created on first deploy |
| `docs/HOW_SWARM_WORKS.md` | how Swarm works, explained on this POC |
