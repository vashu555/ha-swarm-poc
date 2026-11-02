"""Read-only cluster topology; node connectivity, task state and probe health stay separate."""
from copy import deepcopy
import http.client
import json
import re
import socket
import threading
import time
from urllib.parse import urlencode

REGIONS = ('mumbai', 'hyderabad', 'delhi')
_CACHE = None
_LOCK = threading.Lock()

def engine_get(path, filters=None):
    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect('/var/run/docker.sock')
    connection = UnixConnection('localhost', timeout=6)
    try:
        query = '?' + urlencode({'filters': json.dumps(filters)}) if filters else ''
        connection.request('GET', path + query)
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status != 200:
            raise RuntimeError(data.get('message', 'Docker membership unavailable'))
        return data
    finally:
        connection.close()

def probe_health(name, status):
    if name.startswith('pg-'):
        member = next((m for m in (status.get('patroni') or {}).get('members', []) if m.get('name') == name), {})
        return 'healthy' if member.get('state') in ('running', 'streaming') else 'unresponsive'
    if name.startswith('etcd-'):
        return 'healthy' if (status.get('etcd', {}).get(name) or {}).get('up') else 'unresponsive'
    if name.startswith('kafka-'):
        return 'healthy' if int(name.rsplit('-', 1)[1]) in (status.get('kafka') or {}).get('brokers', []) else 'unresponsive'
    if name.startswith('rabbit-'):
        return 'healthy' if (status.get('rabbit') or {}).get('nodes', {}).get(name) else 'unresponsive'
    if name.startswith('redis-'):
        node = (status.get('redis') or {}).get('nodes', {}).get(name, {})
        return 'unresponsive' if not node.get('up') else 'degraded' if node.get('link') == 'down' else 'healthy'
    if name.startswith('sentinel-'):
        return 'healthy' if (status.get('redis') or {}).get('sentinels', {}).get(name) else 'unresponsive'
    if name.startswith('pgbouncer-'):
        return 'healthy' if (status.get('bouncers') or {}).get(name, {}).get('up') else 'unresponsive'
    if name.startswith('haproxy-'):
        return 'responding' if (status.get('lbs') or {}).get(name) else 'unresponsive'
    if name == 'control':
        return 'responding'
    if name == 'exchange':
        value = status.get('exchange') or {}
        return 'unresponsive' if not value else 'degraded' if value.get('broken') or value.get('halted') else 'responding'
    service = (status.get('services') or {}).get(name)
    if not service or service.get('error'):
        return 'unresponsive'
    if any(service.get(key) is False for key in ('rabbit_ok', 'exchange_ok')):
        return 'degraded'
    return 'responding'  # A stats response does not prove all dependencies are healthy.

def build_topology(nodes, services, tasks, control, now=None):
    now = time.time() if now is None else now
    by_id = {n['ID']: n for n in nodes}
    by_region = {r: [n for n in nodes if (n.get('Spec', {}).get('Labels') or {}).get('region') == r] for r in REGIONS}
    status = (control or {}).get('status') or {}
    observed = status.get('observed_at')
    age = max(0, now - observed) if isinstance(observed, (int, float)) else None
    probe_fresh = age is not None and age <= 20 and observed <= now + 5
    zones = {r: {'region': r, 'node': None, 'pc_state': 'not_joined', 'components': []} for r in REGIONS}
    for r, candidates in by_region.items():
        zone = zones[r]
        if len(candidates) > 1:
            zone['pc_state'] = 'duplicate_region'
            continue
        if not candidates:
            continue
        node = candidates[0]
        spec, state = node.get('Spec', {}), node.get('Status', {})
        manager = node.get('ManagerStatus') or {}
        zone['node'] = {'id': node['ID'], 'host': node.get('Description', {}).get('Hostname', ''), 'address': state.get('Addr', ''),
                        'availability': spec.get('Availability'), 'manager': spec.get('Role') == 'manager', 'leader': bool(manager.get('Leader'))}
        zone['pc_state'] = ('unreachable' if state.get('State') != 'ready' else 'drained' if spec.get('Availability') == 'drain'
                            else 'manager_unreachable' if manager.get('Reachability') == 'unreachable' else 'online')
    task_groups = {}
    for task in tasks:
        if task.get('DesiredState') == 'running':
            task_groups.setdefault(task.get('ServiceID'), []).append(task)
    pilot = False
    for service in services:
        spec = service.get('Spec') or {}
        region = (spec.get('Labels') or {}).get('lab.region')
        if region not in zones:
            continue
        name = spec.get('Name', '').removeprefix('hal_')
        placement = spec.get('TaskTemplate', {}).get('Placement') or {}
        constraints = placement.get('Constraints') or []
        if region != 'mumbai' and any(re.fullmatch(r'node\.labels\.region\s*==\s*mumbai', c) for c in constraints):
            pilot = True
        candidates = sorted(task_groups.get(service['ID'], []), key=lambda t: t.get('UpdatedAt', ''), reverse=True)
        running = [t for t in candidates if t.get('Status', {}).get('State') == 'running']
        task = running[0] if running else candidates[0] if candidates else {}
        actual_node = by_id.get(task.get('NodeID'), {})
        raw_state = task.get('Status', {}).get('State', '')
        replicas = (spec.get('Mode', {}).get('Replicated') or {}).get('Replicas', 1)
        state = ('stopped' if replicas == 0 else 'pending' if not task else 'unreachable'
                 if raw_state == 'running' and actual_node.get('Status', {}).get('State') != 'ready' else 'running'
                 if raw_state == 'running' else 'failed' if raw_state in ('failed', 'rejected') else 'starting')
        try:
            health = 'not_checked' if state != 'running' else 'stale' if not probe_fresh else probe_health(name, status)
        except (TypeError, ValueError, AttributeError):
            health = 'unknown'
        zones[region]['components'].append({'name': name, 'state': state, 'health': health,
            'role': (status.get('roles') or {}).get(name, '') if probe_fresh and state == 'running' else '',
            'host': actual_node.get('Description', {}).get('Hostname', ''), 'error': task.get('Status', {}).get('Err', ''),
            'in': 0.0, 'out': 0.0})
    lookup = {c['name']: (r, c) for r, z in zones.items() for c in z['components']}
    edges = {}
    if probe_fresh:
        for flow in status.get('flows') or []:
            source, target = lookup.get(flow.get('from')), lookup.get(flow.get('to'))
            if not source or not target:
                continue
            if source[1]['state'] != 'running' or target[1]['state'] != 'running':
                continue
            rate = max(0, float(flow.get('rate', 0)))
            source[1]['out'] += rate
            target[1]['in'] += rate
            if source[0] != target[0]:
                key = tuple(sorted((source[0], target[0])))
                edges[key] = edges.get(key, 0) + rate
    for z in zones.values():
        z['components'].sort(key=lambda c: c['name'])
        z['running'] = sum(c['state'] == 'running' for c in z['components'])
        z['probe_issues'] = sum(c['health'] in ('unresponsive', 'degraded') for c in z['components'])
    ready = all(z['pc_state'] == 'online' and z['node']['manager'] for z in zones.values())
    return {'ok': True, 'stale': False, 'fetched_at': now, 'probe_observed_at': observed, 'probe_age': age,
            'probe_fresh': probe_fresh, 'pilot': pilot, 'three_pc_ready': ready, 'zones': list(zones.values()),
            'edges': [{'from': a, 'to': b, 'rate': round(rate, 1)} for (a, b), rate in edges.items()],
            'control_available': bool(control)}

def snapshot(fetch_control):
    global _CACHE
    try:
        nodes = engine_get('/nodes')
        services = engine_get('/services', {'label': ['com.docker.stack.namespace=hal']})
        tasks = engine_get('/tasks', {'desired-state': ['running']})
        try:
            control = fetch_control()
        except Exception:
            control = None
        result = build_topology(nodes, services, tasks, control)
        with _LOCK:
            previous = {z['region']: z for z in (_CACHE or {}).get('zones', [])}
            for zone in result['zones']:
                old = previous.get(zone['region']) or {}
                if zone['pc_state'] == 'not_joined' and old.get('node'):
                    zone.update(pc_state='departed', node=deepcopy(old['node']),
                                last_seen_at=old.get('last_seen_at', _CACHE['fetched_at']))
            _CACHE = deepcopy(result)
        return result
    except Exception as exc:
        with _LOCK:
            result = deepcopy(_CACHE) if _CACHE else {'fetched_at': None, 'zones': [], 'edges': []}
        result.update(ok=False, stale=True, error=str(exc)[:400], three_pc_ready=False, probe_fresh=False)
        for zone in result['zones']:
            zone['pc_state'] = 'unknown'
            for component in zone['components']:
                component.update(state='unknown', health='stale', role='')
        return result
