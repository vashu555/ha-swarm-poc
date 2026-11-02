"""Check three region managers and optionally inventory existing pilot data. Never changes services or volumes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

REGIONS = ('mumbai', 'hyderabad', 'delhi')

def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout

def check_nodes(nodes):
    issues, result, addresses = [], {}, []
    for region in REGIONS:
        candidates = [n for n in nodes if (n.get('Spec', {}).get('Labels') or {}).get('region') == region]
        if len(candidates) != 1:
            issues.append(f'{region}: needs exactly one joined PC; found {len(candidates)}')
            result[region] = None
            continue
        node = candidates[0]
        spec, status, manager = node['Spec'], node.get('Status', {}), node.get('ManagerStatus') or {}
        result[region] = {'node_id': node['ID'], 'host': node.get('Description', {}).get('Hostname', ''), 'state': status.get('State')}
        if status.get('State') != 'ready' or spec.get('Availability') != 'active':
            issues.append(f'{region}: PC must be ready and active')
        if spec.get('Role') != 'manager' or not manager or manager.get('Reachability') == 'unreachable':
            issues.append(f'{region}: a reachable swarm manager is required')
        if not status.get('Addr'):
            issues.append(f'{region}: missing node address')
        else:
            addresses.append(status['Addr'])
    if len(addresses) != len(set(addresses)):
        issues.append('Region PCs must have different node addresses')
    return {'ready': not issues, 'issues': issues, 'regions': result}

def inventory(nodes, readiness):
    service_ids = docker('service', 'ls', '-q', '--filter', 'label=com.docker.stack.namespace=hal').split()
    services = json.loads(docker('service', 'inspect', *service_ids)) if service_ids else []
    copies = {r: set() for r in REGIONS if r != 'mumbai'}
    components = {r: [] for r in REGIONS}
    pilot = False
    for service in services:
        spec = service['Spec']
        region = (spec.get('Labels') or {}).get('lab.region')
        if region not in REGIONS:
            continue
        components[region].append(spec['Name'])
        constraints = (spec['TaskTemplate'].get('Placement') or {}).get('Constraints') or []
        on_mumbai = any(c.replace(' ', '') == 'node.labels.region==mumbai' for c in constraints)
        if region != 'mumbai' and on_mumbai:
            pilot = True
            copies[region].update(m['Source'] for m in spec['TaskTemplate'].get('ContainerSpec', {}).get('Mounts', []) if m['Type'] == 'volume')
    details = {}
    for name in sorted(set().union(*copies.values())):
        info = json.loads(docker('volume', 'inspect', name))[0]
        path = info['Mountpoint']
        size = subprocess.run(['du', '-sk', path], capture_output=True, text=True, timeout=30)
        details[name] = {'source_volume': name, 'source_mountpoint': path,
                         'allocated_bytes': int(size.stdout.split()[0]) * 1024 if size.returncode == 0 else None}
    return {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'pilot_detected': pilot,
            'readiness': readiness, 'services_by_region': components,
            'copies_by_destination': {r: [details[n] for n in sorted(names)] for r, names in copies.items()},
            'method': 'Cold-copy existing volumes after quiescing the entire hal stack; preserve source volumes and ownership.',
            'notice': 'Inventory only. No services stopped, no data archived, copied, erased or deployed.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    ids = docker('node', 'ls', '-q').split()
    readiness = check_nodes(json.loads(docker('node', 'inspect', *ids)) if ids else [])
    result = inventory([], readiness) if args.plan else readiness
    if args.output:
        with args.output.open('x', encoding='utf-8') as file:
            json.dump(result, file, indent=2)
        args.output.chmod(0o600)
        print('Saved private migration inventory: ' + str(args.output))
    else:
        print(json.dumps(result, indent=2))
    if not args.plan and not readiness['ready']:
        raise SystemExit(1)
