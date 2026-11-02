import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'console'))
import live_map as m

def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

preflight = load_script('three-pc-preflight')
patcher = load_script('patch-control-observability')

def node(region, state='ready', reachability='reachable'):
    n = list(m.REGIONS).index(region) + 1
    return {'ID': region, 'Spec': {'Labels': {'region': region}, 'Availability': 'active', 'Role': 'manager'},
            'Status': {'State': state, 'Addr': f'192.0.2.{n}'}, 'Description': {'Hostname': region + '-pc'},
            'ManagerStatus': {'Reachability': reachability}}

def service(name='pg-1', region='mumbai', placed=None, replicas=1):
    return {'ID': name, 'Spec': {'Name': 'hal_' + name, 'Labels': {'lab.region': region}, 'Mode': {'Replicated': {'Replicas': replicas}},
            'TaskTemplate': {'Placement': {'Constraints': ['node.labels.region == ' + (placed or region)]}}}}

def task(name='pg-1', host='mumbai'):
    return {'ID': name, 'ServiceID': name, 'NodeID': host, 'DesiredState': 'running', 'Status': {'State': 'running'}}

def control(observed=100):
    return {'status': {'observed_at': observed, 'patroni': {'members': [{'name': 'pg-1', 'state': 'running'}]}, 'roles': {'pg-1': 'LEADER'}}}

class TopologyTests(unittest.TestCase):
    def test_cleanup_does_not_make_a_departed_pc_disappear_from_map(self):
        m._CACHE = m.build_topology([node(r) for r in m.REGIONS], [], [], None, now=101)
        with patch.object(m, 'engine_get', side_effect=[[node('mumbai'),node('hyderabad')],[],[]]):
            result = m.snapshot(lambda: None)
        zone = next(z for z in result['zones'] if z['region']=='delhi')
        self.assertEqual(zone['pc_state'], 'departed')
        self.assertEqual(zone['node']['host'], 'delhi-pc')
        self.assertFalse(result['three_pc_ready'])

    def test_real_three_pc_readiness(self):
        result = m.build_topology([node(r) for r in m.REGIONS], [service()], [task()], control(), now=101)
        self.assertTrue(result['three_pc_ready'])
        self.assertEqual(result['zones'][0]['components'][0]['health'], 'healthy')

    def test_pilot_does_not_pretend_three_pcs_exist(self):
        result = m.build_topology([node('mumbai')], [service('pg-2', 'hyderabad', 'mumbai')], [task('pg-2')], control(), now=101)
        self.assertTrue(result['pilot'])
        self.assertFalse(result['three_pc_ready'])
        self.assertEqual(result['zones'][1]['pc_state'], 'not_joined')
        self.assertEqual(result['zones'][1]['components'][0]['host'], 'mumbai-pc')

    def test_unreachable_node_is_not_reported_running(self):
        result = m.build_topology([node('mumbai', 'down')], [service()], [task()], control(), now=101)
        component = result['zones'][0]['components'][0]
        self.assertEqual(component['state'], 'unreachable')
        self.assertEqual(component['health'], 'not_checked')
        self.assertFalse(component['role'])

    def test_lost_manager_link_is_separate_from_worker_state(self):
        result = m.build_topology([node('mumbai', reachability='unreachable')], [service()], [task()], control(), now=101)
        self.assertEqual(result['zones'][0]['pc_state'], 'manager_unreachable')
        self.assertEqual(result['zones'][0]['components'][0]['state'], 'running')

    def test_stopped_component_cannot_inherit_old_healthy_probe(self):
        result = m.build_topology([node('mumbai')], [service(replicas=0)], [task()], control(), now=101)
        self.assertEqual(result['zones'][0]['components'][0]['state'], 'stopped')
        self.assertEqual(result['zones'][0]['components'][0]['health'], 'not_checked')

    def test_stale_probe_data_does_not_stay_green(self):
        for value in ({'status': {}}, control(observed=50), control(observed=200)):
            result = m.build_topology([node('mumbai')], [service()], [task()], value, now=101)
            self.assertEqual(result['zones'][0]['components'][0]['health'], 'stale')
            self.assertFalse(result['zones'][0]['components'][0]['role'])

    def test_running_process_can_fail_its_probe(self):
        result = m.build_topology([node('mumbai')], [service()], [task()], {'status': {'observed_at': 100}}, now=101)
        self.assertEqual(result['zones'][0]['components'][0]['state'], 'running')
        self.assertEqual(result['zones'][0]['components'][0]['health'], 'unresponsive')

    def test_stats_response_does_not_imply_full_health(self):
        self.assertEqual(m.probe_health('api-1', {'services': {'api-1': {'counters': {}}}}), 'responding')
        self.assertEqual(m.probe_health('bridge-1', {'services': {'bridge-1': {'rabbit_ok': False}}}), 'degraded')

    def test_docker_failure_keeps_snapshot_but_invalidates_every_badge(self):
        m._CACHE = m.build_topology([node('mumbai')], [service()], [task()], control(), now=101)
        with patch.object(m, 'engine_get', side_effect=RuntimeError('no leader')):
            result = m.snapshot(lambda: control())
        self.assertFalse(result['ok'])
        self.assertEqual(result['fetched_at'], 101)
        self.assertEqual(result['zones'][0]['pc_state'], 'unknown')
        self.assertEqual(result['zones'][0]['components'][0]['health'], 'stale')

    def test_duplicate_regions_are_not_ready(self):
        result = m.build_topology([node('mumbai'), node('mumbai')], [], [], None, now=101)
        self.assertEqual(result['zones'][0]['pc_state'], 'duplicate_region')
        self.assertFalse(result['three_pc_ready'])

    def test_preflight_requires_all_regions_and_reachable_managers(self):
        self.assertFalse(preflight.check_nodes([node('mumbai')])['ready'])
        self.assertFalse(preflight.check_nodes([node('mumbai'), node('hyderabad', 'down'), node('delhi')])['ready'])
        self.assertTrue(preflight.check_nodes([node(r) for r in m.REGIONS])['ready'])

    def test_probe_timestamp_patch_preserves_source_and_is_idempotent(self):
        source = b'def status_loop():\n    while True:\n        try:\n            t0 = 1\n            st = {}\n            STATE["status"] = st\n        except Exception:\n            pass\n'
        with tempfile.TemporaryDirectory() as folder:
            path, backup = Path(folder)/'control.py', Path(folder)/'backup'
            path.write_bytes(source)
            self.assertTrue(patcher.patch(path, backup))
            self.assertEqual(backup.read_bytes(), source)
            self.assertFalse(patcher.patch(path, backup))
            self.assertIn(b'st["observed_at"]', path.read_bytes())

if __name__ == '__main__':
    unittest.main()
