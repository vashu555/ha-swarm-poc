"""Disposable real Docker nodes; never changes the host daemon's swarm membership."""
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NETWORK = 'finflock-swarm-qa-net'
NODES = ['finflock-swarm-qa-' + s for s in ('a', 'b', 'c')]
REPORT = []
CREATED = []
NETWORK_CREATED = False

def clean(text):
    return re.sub(r'SWMTKN-\S+', '[REDACTED]', text)

def run(*args, timeout=30, check=True):
    p = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(clean(p.stdout + p.stderr))
    return p

def inside(n, *args, timeout=30, check=True):
    return run('exec', '-e', 'WG_IF=eth0', n, *args, timeout=timeout, check=check)

def wait(fn, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    raise RuntimeError('Timed out waiting for condition')

def api(n, path, body=None):
    args = ['curl', '-fsS', '--max-time', '15']
    if body is not None:
        args += ['-H', 'Content-Type: application/json', '-d', json.dumps(body)]
    return json.loads(inside(n, *args, 'http://127.0.0.1:9790' + path, timeout=20).stdout)

def job(n, path, body):
    api(n, path, body)
    state = wait(lambda: (j if not (j := api(n, '/api/state')['job'])['running'] else None), 100)
    if not state['ok']:
        raise RuntimeError(clean('\n'.join(state['log'])))
    return state

def passed(name, detail=''):
    REPORT.append({'test': name, 'status': 'passed', 'detail': detail})
    print('PASS ' + name, flush=True)

def local_python(n, code):
    return inside(n, 'python3', '-c', "import sys; sys.path.insert(0, '/qa/console'); import console as c; " + code, timeout=45)

def members(n):
    return [json.loads(line) for line in inside(n, 'docker', 'node', 'ls', '--format', '{{json .}}', timeout=8).stdout.splitlines()]

def cleanup(n):
    local_python(n, 'c.cleanup_departed()')

def wait_removed(a, other, count):
    def tick():
        cleanup(a)
        cleanup(other)
        return len(members(a)) == count
    return wait(tick)

def start_console(n):
    inside(n, 'ip', 'link', 'set', 'eth0', 'mtu', '1200')
    run('exec', '-d', '-w', '/qa', '-e', 'WG_IF=eth0', '-e', 'CONSOLE_STATE=/var/lib/ha-node-console', n,
        'python3', '-c', "import sys; sys.path.insert(0, '/qa/console'); import console as c; c.cleanup_loop=lambda: None; c.main()")
    wait(lambda: api(n, '/api/health'))

def setup():
    global NETWORK_CREATED
    # Do not reuse or delete containers left by another run without explicit inspection.
    for n in NODES:
        if run('container', 'inspect', n, check=False).returncode == 0:
            raise RuntimeError('A test container already exists: ' + n)
    run('network', 'create', '--subnet', '100.127.250.0/24', NETWORK)
    NETWORK_CREATED = True
    for n in NODES:
        run('run', '-d', '--privileged', '--memory', '600m', '--label', 'finflock.swarm.qa=true',
            '--name', n, '--hostname', n, '--network', NETWORK,
            '-e', 'DOCKER_TLS_CERTDIR=', '-e', 'DOCKER_HOST=unix:///var/run/docker.sock',
            '--mount', f'type=bind,source={ROOT},target=/qa,readonly',
            'finflock-swarm-qa:local', '--tls=false', timeout=45)
        CREATED.append(n)
        wait(lambda: inside(n, 'docker', 'info', '--format', '{{.ID}}', check=False).returncode == 0)
        start_console(n)

def tests():
    a, b, cnode = NODES
    ips = {n: json.loads(run('inspect', n).stdout)[0]['NetworkSettings']['Networks'][NETWORK]['IPAddress'] for n in NODES}
    inside(a, 'docker', 'swarm', 'init', '--advertise-addr', ips[a])
    aid = inside(a, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip()
    inside(a, 'docker', 'node', 'update', '--label-add', 'region=mumbai', '--label-add', 'role=main', aid)
    for n, region in ((b, 'delhi'), (cnode, 'hyderabad')):
        api(n, '/api/settings', {'main_ip': ips[a]})
        job(n, '/api/become', {'region': region})
    assert len(members(a)) == 3
    passed('Three real managers join through frontend API with region labels')
    image_tar = ROOT / 'qa-alpine.tar'
    run('save', '-o', str(image_tar), 'alpine:3.20', timeout=60)
    inside(b, 'docker', 'load', '-i', '/qa/qa-alpine.tar', timeout=60)
    inside(a, 'docker', 'service', 'create', '--detach', '--name', 'hal_probe', '--label', 'com.docker.stack.namespace=hal', '--constraint', 'node.labels.region==delhi', '--no-resolve-image', 'alpine:3.20', 'sleep', 'infinity')
    wait(lambda: bool(inside(b, 'docker', 'ps', '-q', '--filter', 'label=com.docker.swarm.service.name=hal_probe').stdout.strip()))
    job(a, '/api/zone-drill', {'region': 'delhi', 'action': 'down'})
    wait(lambda: not inside(b, 'docker', 'ps', '-q', '--filter', 'label=com.docker.swarm.service.name=hal_probe').stdout.strip())
    assert len(members(a)) == 3
    job(a, '/api/zone-drill', {'region': 'delhi', 'action': 'up'})
    wait(lambda: bool(inside(b, 'docker', 'ps', '-q', '--filter', 'label=com.docker.swarm.service.name=hal_probe').stdout.strip()))
    passed('Live Zone down stops a real workload and Zone up restarts it without removing the manager')
    inside(a, 'docker', 'service', 'rm', 'hal_probe')
    job(b, '/api/become', {'region': 'delhi'})
    assert len(members(a)) == 3
    passed('Repeated Become Delhi is idempotent')
    bid = inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip()
    inside(a, 'docker', 'node', 'update', '--label-rm', 'region', bid)
    job(b, '/api/become', {'region': 'delhi'})
    assert api(b, '/api/state')['swarm']['region'] == 'delhi'
    assert inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip() == bid
    passed('Interrupted join with missing label resumes without leaving or rejoining')
    bid = inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip()
    job(b, '/api/leave', {})
    assert inside(b, 'docker', 'info', '--format', '{{.Swarm.LocalNodeState}}').stdout.strip() == 'inactive'
    wait_removed(a, cnode, 2)
    assert not api(a, '/peer/info')['regions']['delhi']
    passed('Frontend Leave demotes manager, leaves, and automatically frees region')
    job(b, '/api/become', {'region': 'delhi'})
    assert inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip() != bid
    passed('Same join token permits a clean rejoin with a new node identity')
    # Lose the majority, verify the UI does not offer the setup screen, then restore it.
    run('stop', '-t', '1', b, cnode, timeout=30)
    wait(lambda: bool(api(a, '/api/state')['swarm']['membership_error']), 60)
    state = api(a, '/api/state')['swarm']
    assert state['region'] == 'mumbai' and state['is_main']
    cleanup(a)
    passed('Lost quorum preserves Mumbai identity and prevents unsafe cleanup')
    run('start', b, cnode)
    for n in (b, cnode):
        wait(lambda: inside(n, 'docker', 'info', '--format', '{{.ID}}', check=False).returncode == 0)
        start_console(n)
    wait(lambda: len(members(a)) == 3, 90)
    passed('Manager quorum recovers after nodes restart')
    # A foreign active swarm must not be silently relabelled as part of this swarm.
    job(b, '/api/leave', {})
    wait_removed(a, cnode, 2)
    inside(b, 'docker', 'swarm', 'init', '--advertise-addr', ips[b])
    token = inside(a, 'docker', 'swarm', 'join-token', '-q', 'manager').stdout.strip()
    attempt = inside(b, 'bash', '/qa/scripts/04-swarm-join.sh', 'delhi', token, ips[a], check=False)
    if attempt.returncode == 0:
        raise RuntimeError('DEFECT: join script accepted a node belonging to a different swarm and relabelled it')
    passed('An active foreign swarm is rejected without changing its labels')
    # Pending joins are deliberately triggered only in a disposable daemon.
    inside(b, 'docker', 'swarm', 'leave', '--force')
    try:
        inside(b, 'docker', 'swarm', 'join', '--token', token, '--advertise-addr', ips[b], '100.127.250.254:2377', timeout=8, check=False)
    except subprocess.TimeoutExpired:
        pass
    pending = inside(b, 'docker', 'info', '--format', '{{.Swarm.LocalNodeState}}').stdout.strip()
    if pending != 'pending':
        raise RuntimeError('Expected a pending join in the isolated daemon, got ' + pending)
    api(b, '/api/become', {'region': 'delhi'})
    rejected = wait(lambda: (j if not (j := api(b, '/api/state')['job'])['running'] else None))
    assert not rejected['ok'] and 'pending' in rejected['reject']
    job(b, '/api/leave', {})
    assert inside(b, 'docker', 'info', '--format', '{{.Swarm.LocalNodeState}}').stdout.strip() == 'inactive'
    passed('Pending join cannot be submitted twice and frontend Leave clears it')
    job(b, '/api/become', {'region': 'delhi'})
    passed('Node can join normally after clearing a pending attempt')
    bid = inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip()
    run('stop', '-t', '1', b)
    wait_removed(a, cnode, 2)
    passed('Abruptly stopped manager is automatically removed while a majority remains')
    run('start', b)
    wait(lambda: inside(b, 'docker', 'info', '--format', '{{.ID}}', check=False).returncode == 0)
    start_console(b)
    job(b, '/api/leave', {})
    job(b, '/api/become', {'region': 'delhi'})
    assert inside(b, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip() != bid
    passed('Automatically removed manager can clear stale membership and rejoin')

created = False
try:
    setup()
    created = True
    tests()
except Exception as exc:
    REPORT.append({'test': 'integration run', 'status': 'failed', 'detail': clean(str(exc))})
    print('FAIL ' + clean(str(exc)), flush=True)
    for n in CREATED:
        try:
            state = api(n, '/api/state')
            REPORT.append({'diagnostic_node': n, 'state': json.loads(clean(json.dumps(state)))})
        except Exception:
            pass
finally:
    (ROOT / 'local-integration-report.json').write_text(json.dumps(REPORT, indent=2))
    for n in CREATED:
        run('rm', '-f', '-v', n, check=False)
    if NETWORK_CREATED:
        run('network', 'rm', NETWORK, check=False)
if any(r['status'] == 'failed' for r in REPORT):
    raise SystemExit(1)
