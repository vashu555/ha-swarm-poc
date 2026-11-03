import importlib.util
import ipaddress
from pathlib import Path
import unittest
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('node_console', Path(__file__).parent / 'console/console.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def info(manager=True, state='active'):
    return {'ok': True, 'swarm': {'NodeID': 'self', 'ControlAvailable': manager, 'LocalNodeState': state}}

def node(id='self', state='ready', manager=True, leader=False):
    return dict(id=id, state=state, manager=manager, leader=leader, reachability='reachable', hostname=id, region='delhi')

class Departures(unittest.TestCase):
    def test_stale_membership_can_clear_only_after_authoritative_removal(self):
        local = {'NodeID': 'removed', 'ControlAvailable': True, 'LocalNodeState': 'pending', 'RemoteManagers': [{'Addr': '192.0.2.1:2377'}], 'Cluster': {'ID': 'verified-cluster'}}
        peer = {'manager': True, 'leader': {'hostname': 'main'}, 'cluster_id': 'verified-cluster', 'member_ids': ['main', 'other']}
        with patch.object(c, 'docker_info', return_value={'ok': True, 'swarm': local}), patch.object(c, 'nodes', return_value=[]), patch.object(c, 'settings', return_value={}), patch.object(c, 'http_json', return_value=(200, peer)), patch.object(c, 'stream') as stream:
            c.job_leave()
        stream.assert_called_once_with(['docker', 'swarm', 'leave', '--force'])

    def test_force_leave_refused_for_live_member_or_foreign_cluster(self):
        local = {'NodeID': 'self', 'RemoteManagers': [{'Addr': '192.0.2.1:2377'}], 'Cluster': {'ID': 'our-cluster'}}
        for peer in (
            {'manager': True, 'leader': {}, 'cluster_id': 'our-cluster', 'member_ids': ['other']},
            {'manager': True, 'leader': {'hostname': 'main'}, 'cluster_id': 'foreign-cluster', 'member_ids': ['other']},
            {'manager': True, 'leader': {'hostname': 'main'}, 'cluster_id': 'our-cluster', 'member_ids': ['self', 'other']},
            {'manager': True, 'leader': {'hostname': 'main'}, 'cluster_id': 'our-cluster', 'member_ids': ['other'], 'membership_error': 'no quorum'},
        ):
            with patch.object(c, 'settings', return_value={}), patch.object(c, 'http_json', return_value=(200, peer)):
                self.assertFalse(c.confirmed_departure(local))

    def test_concurrent_identity_updates_preserve_main_pc_setting(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c, 'STATE_DIR', Path(directory)):
            c.save_settings(main_ip='192.0.2.1')
            def update(i):
                c.save_settings(identity={'node_id': str(i), 'region': 'delhi'})
                self.assertEqual(c.settings()['main_ip'], '192.0.2.1')
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(update, range(100)))
            self.assertEqual(c.settings()['main_ip'], '192.0.2.1')

    def test_join_tokens_are_redacted_from_job_log(self):
        c.log('docker swarm join --token SWMTKN-1-private-token 192.0.2.3')
        self.assertNotIn('SWMTKN-', c.JOB['log'][-1])
        self.assertIn('[join token hidden]', c.JOB['log'][-1])

    def test_missing_region_label_can_resume_in_verified_swarm(self):
        sw = {'docker': {'ok': True, 'desktop': False}, 'state': 'active', 'region': '', 'manager': True, 'node_id': 'self', 'nodes': [{'main': True, 'state': 'ready', 'addr': '192.0.2.1'}]}
        with patch.object(c, 'netbird', return_value={'ip': '192.0.2.2'}), patch.object(c, 'swarm', return_value=sw), patch.object(c, 'http_json', side_effect=[(200, {'token': 'same-token'}), (200, {})]), patch.object(c, 'sh', return_value=(0, 'same-token')), patch.object(c, 'stream') as stream:
            c.job_become('delhi')
        stream.assert_called_once_with(['docker', 'node', 'update', '--label-add', 'region=delhi', 'self'])

    def test_region_resume_rejects_a_different_swarm(self):
        sw = {'docker': {'ok': True, 'desktop': False}, 'state': 'active', 'region': '', 'manager': True, 'node_id': 'self', 'nodes': [{'main': True, 'state': 'ready', 'addr': '192.0.2.1'}]}
        with patch.object(c, 'netbird', return_value={'ip': '192.0.2.2'}), patch.object(c, 'swarm', return_value=sw), patch.object(c, 'http_json', side_effect=[(200, {'token': 'different-token'}), (200, {})]) as http, patch.object(c, 'sh', return_value=(0, 'local-token')), patch.object(c, 'stream') as stream:
            with self.assertRaises(c.Reject):
                c.job_become('delhi')
        stream.assert_not_called()
        self.assertEqual(http.call_count, 2)  # reservation released even when validation fails

    def test_zone_drill_requires_three_ready_regions(self):
        with patch.object(c, 'swarm', return_value={'is_main': True, 'nodes': [dict(node(), region='mumbai', availability='active')]}), patch.object(c, 'stream') as stream:
            with self.assertRaises(c.Reject):
                c.job_zone('mumbai', 'down')
        stream.assert_not_called()

    def test_zone_down_crashes_selected_workloads_without_draining_pc(self):
        members = [dict(node(r), region=r, availability='active') for r in c.REGIONS]
        with patch.object(c, 'swarm', return_value={'is_main': True, 'nodes': members}), patch.object(c, 'sh', return_value=(0, 'service-id')), patch.object(c, 'lab_base', return_value='http://fixture'), patch.object(c, 'http_json', return_value=(200, {'ok':True})) as http, patch.object(c, 'stream') as stream:
            c.job_zone('delhi', 'down')
        stream.assert_not_called()
        http.assert_called_once_with('http://fixture/api/site', {'site':'oracle','action':'down'},timeout=30)

    def test_zone_up_can_restore_after_drill(self):
        members = [dict(node('delhi'), region='delhi', availability='active')]
        with patch.object(c, 'swarm', return_value={'is_main': True, 'nodes': members}), patch.object(c, 'lab_base', return_value='http://fixture'), patch.object(c, 'http_json', return_value=(200, {'ok':True})) as http, patch.object(c, 'stream') as stream:
            c.job_zone('delhi', 'up')
        stream.assert_not_called()
        http.assert_called_once_with('http://fixture/api/site', {'site':'oracle','action':'up'},timeout=30)

    def test_pending_join_is_not_started_again(self):
        for state in ('pending', 'error', 'locked'):
            with self.subTest(state=state), patch.object(c, 'netbird', return_value={'ip': '192.0.2.2'}), patch.object(c, 'swarm', return_value={'docker': {'ok': True, 'desktop': False}, 'state': state}), patch.object(c, 'ensure_mtu') as mtu, patch.object(c, 'discover') as discover:
                with self.assertRaises(c.Reject):
                    c.job_become('delhi')
                mtu.assert_not_called()
                discover.assert_not_called()

    def test_lost_quorum_retains_verified_local_identity(self):
        identity = {'node_id': 'self', 'region': 'mumbai', 'is_main': True}
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', side_effect=RuntimeError('no leader')), patch.object(c, 'settings', return_value={'identity': identity}):
            sw = c.swarm()
        self.assertEqual(sw['region'], 'mumbai')
        self.assertTrue(sw['is_main'])
        self.assertEqual(sw['membership_error'], 'no leader')

    def test_rejoined_node_does_not_inherit_old_identity(self):
        identity = {'node_id': 'old-node', 'region': 'mumbai', 'is_main': True}
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', side_effect=RuntimeError('no leader')), patch.object(c, 'settings', return_value={'identity': identity}):
            sw = c.swarm()
        self.assertEqual(sw['region'], '')
        self.assertEqual(sw['membership_error'], 'no leader')

    def test_membership_failure_blocks_new_enrollment(self):
        with patch.object(c, 'peer_info', return_value={'manager': True, 'membership_error': 'no leader'}):
            with self.assertRaises(c.Reject):
                c.peer_enroll({'region': 'delhi'}, '192.0.2.2')

    def setUp(self):
        c.JOB['log'] = []
        # Unit tests must never discover the live pilot and send real zone-drill requests.
        pilot = patch.object(c, 'lab_is_pilot', return_value=False)
        pilot.start()
        self.addCleanup(pilot.stop)
        network = patch.object(c, 'NETBIRD_NET', ipaddress.ip_network('192.0.2.0/24'))
        network.start()
        self.addCleanup(network.stop)

    def test_worker_leaves_without_force(self):
        with patch.object(c, 'docker_info', return_value=info(False)), patch.object(c, 'stream') as stream:
            c.job_leave()
        stream.assert_called_once_with(['docker', 'swarm', 'leave'])

    def test_manager_waits_for_demotion(self):
        with patch.object(c, 'docker_info', side_effect=[info(), info(), info(False)]), patch.object(c, 'nodes', return_value=[node(), node('other')]), patch.object(c, 'sh', return_value=(0, 'demoted')) as sh, patch.object(c, 'stream') as stream, patch.object(c.time, 'sleep') as sleep:
            c.job_leave()
        sh.assert_called_once_with(['docker', 'node', 'demote', 'self'], 20)
        sleep.assert_called_once()
        stream.assert_called_once_with(['docker', 'swarm', 'leave'])

    def test_failed_demotion_never_forces_leave(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[node(), node('other')]), patch.object(c, 'sh', return_value=(1, 'failed')), patch.object(c, 'stream') as stream, patch.object(c.time, 'sleep'):
            with self.assertRaises(c.Reject):
                c.job_leave()
        stream.assert_not_called()

    def test_missing_quorum_does_not_demote(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[node(), node('other', 'down')]), patch.object(c, 'sh') as sh, patch.object(c, 'stream') as stream:
            with self.assertRaises(c.Reject):
                c.job_leave()
        sh.assert_not_called()
        stream.assert_not_called()

    def test_unreadable_membership_is_not_singleton(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[]), patch.object(c, 'stream') as stream:
            with self.assertRaises(c.Reject):
                c.job_leave()
        stream.assert_not_called()

    def test_true_singleton_can_leave(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[node()]), patch.object(c, 'stream') as stream:
            c.job_leave()
        stream.assert_called_once_with(['docker', 'swarm', 'leave', '--force'])

    def test_disconnected_manager_keeps_its_membership(self):
        members = [node(leader=True), node('departed', 'down')]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=members), patch.object(c, 'sh', return_value=(0, 'ok')) as sh:
            c.cleanup_departed()
        sh.assert_not_called()

    def test_completed_leave_cleans_down_worker_without_force(self):
        members = [node(leader=True), node('departed', 'down', manager=False)]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=members), patch.object(c, 'sh', return_value=(0, 'ok')) as sh:
            c.cleanup_departed()
        sh.assert_called_once_with(['docker', 'node', 'rm', 'departed'], 10)

    def test_worker_promoted_during_cleanup_is_retained(self):
        before = [node(leader=True), node('other', 'down', manager=False)]
        after = [node(leader=True), node('other', 'down', manager=True)]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', side_effect=[before, after]), patch.object(c, 'sh') as sh:
            c.cleanup_departed()
        sh.assert_not_called()

    def test_cleanup_without_quorum_keeps_region(self):
        members = [node(leader=True), node('departed', 'down', manager=False)]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=members), patch.object(c, 'sh', return_value=(1, 'no quorum')) as sh:
            c.cleanup_departed()
        self.assertEqual(sh.call_count, 1)

    def test_returning_node_is_not_removed(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', side_effect=[[node(leader=True), node('other', 'down', manager=False)], [node(leader=True), node('other', manager=False)]]), patch.object(c, 'sh') as sh:
            c.cleanup_departed()
        sh.assert_not_called()

    def test_follower_does_not_cleanup(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[node(), node('other', 'down')]), patch.object(c, 'sh') as sh:
            c.cleanup_departed()
        sh.assert_not_called()

if __name__ == '__main__':
    unittest.main()
