"""Verify token preservation during lost-quorum recovery in disposable daemons."""
import json
import re
import subprocess
import time
from pathlib import Path

names = ['finflock-token-qa-a', 'finflock-token-qa-b']
network = 'finflock-token-qa-net'
created = []
network_created = False

def run(*args, check=True, timeout=30):
    p = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(re.sub(r'SWMTKN-\S+', '[hidden]', p.stdout + p.stderr))
    return p

def inside(n, *args, **kw):
    return run('exec', n, *args, **kw)

def wait(fn):
    for _ in range(60):
        if fn():
            return
        time.sleep(1)
    raise RuntimeError('Wait timed out')

result = {}
try:
    for n in names:
        assert run('inspect', n, check=False).returncode != 0
    run('network', 'create', network)
    network_created = True
    for n in names:
        run('run', '-d', '--privileged', '--memory', '600m', '--label', 'finflock.swarm.qa=true', '--name', n,
            '--network', network, '-e', 'DOCKER_TLS_CERTDIR=', '-e', 'DOCKER_HOST=unix:///var/run/docker.sock',
            'finflock-swarm-qa:local', '--tls=false')
        created.append(n)
        wait(lambda: inside(n, 'docker', 'info', '--format', '{{.ID}}', check=False).returncode == 0)
    a, b = names
    ip = json.loads(run('inspect', a).stdout)[0]['NetworkSettings']['Networks'][network]['IPAddress']
    inside(a, 'docker', 'swarm', 'init', '--advertise-addr', ip)
    token = inside(a, 'docker', 'swarm', 'join-token', '-q', 'manager').stdout.strip()
    node_id = inside(a, 'docker', 'info', '--format', '{{.Swarm.NodeID}}').stdout.strip()
    inside(a, 'docker', 'node', 'update', '--label-add', 'region=mumbai', '--label-add', 'role=main', node_id)
    inside(b, 'docker', 'swarm', 'join', '--token', token, ip + ':2377')
    run('stop', '-t', '1', b)
    wait(lambda: inside(a, 'timeout', '5', 'docker', 'node', 'ls', check=False, timeout=8).returncode != 0)
    inside(a, 'docker', 'swarm', 'init', '--force-new-cluster', '--advertise-addr', ip)
    after = inside(a, 'docker', 'swarm', 'join-token', '-q', 'manager').stdout.strip()
    assert token == after, 'Recovery changed the token'
    labels = json.loads(inside(a, 'docker', 'node', 'inspect', node_id, '--format', '{{json .Spec.Labels}}').stdout)
    assert labels['region'] == 'mumbai' and labels['role'] == 'main'
    result = {'passed': True, 'token_unchanged': True, 'node_identity_and_labels_preserved': True}
    print('PASS Lost-quorum recovery preserves the same manager token, node identity, and Mumbai labels')
except Exception as exc:
    result = {'passed': False, 'error': str(exc)}
    print('FAIL ' + str(exc))
finally:
    Path(__file__).with_name('token-recovery-report.json').write_text(json.dumps(result, indent=2))
    for n in created:
        run('rm', '-f', '-v', n, check=False)
    if network_created:
        run('network', 'rm', network, check=False)
if not result.get('passed'):
    raise SystemExit(1)
