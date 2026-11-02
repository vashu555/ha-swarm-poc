"""Order Docker after NetBird and repair only the known missing-bind-address startup error."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import time

def command(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

def addresses():
    p = command('ip', '-4', '-o', 'addr', 'show', os.environ.get('WG_IF', 'wt0'))
    return set(re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)/', p.stdout)) if p.returncode == 0 else set()

def wait_for_address(seconds=90):
    deadline = time.monotonic() + seconds
    while True:
        if addresses():
            return True
        if time.monotonic() >= deadline:
            print('NetBird address not ready; allowing Docker to start. The recovery timer will check a late bind failure.', flush=True)
            return False
        time.sleep(1)

def failed_address(swarm):
    error = swarm.get('Error', '')
    if swarm.get('LocalNodeState') != 'error' or 'failed to listen' not in error or 'cannot assign requested address' not in error:
        return None
    match = re.search(r'listen tcp (\d+\.\d+\.\d+\.\d+):\d+:', error)
    if not match:
        return None
    try:
        return str(ipaddress.IPv4Address(match.group(1)))
    except ValueError:
        return None

def recover():
    # Do not start a Docker service that the user deliberately stopped.
    if command('systemctl', 'is-active', '--quiet', 'docker').returncode != 0:
        return False
    p = command('docker', 'info', '--format', '{{json .Swarm}}', timeout=15)
    if p.returncode:
        return False
    try:
        address = failed_address(json.loads(p.stdout))
    except ValueError:
        return False
    if not address or address not in addresses():
        return False
    stamp = Path('/run/ha-docker-netbird-recovery.last')
    now = time.time()
    try:
        if now - float(stamp.read_text()) < 300:
            return False
    except (OSError, ValueError):
        pass
    stamp.write_text(str(now))
    print('Recovering Docker from the NetBird startup bind error; its configured address is now present.', flush=True)
    p = command('systemctl', 'restart', 'docker', timeout=120)
    if p.returncode:
        raise RuntimeError('Docker restart failed: ' + p.stderr[-500:])
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--wait', action='store_true')
    group.add_argument('--recover', action='store_true')
    parser.add_argument('--wait-seconds', type=int, default=90)
    args = parser.parse_args()
    if args.wait:
        wait_for_address(max(0, args.wait_seconds))
    else:
        recover()
