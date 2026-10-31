import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('node_console', Path(__file__).parent / 'console/console.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def info(manager=True, state='active'):
    return {'ok': True, 'swarm': {'NodeID': 'self', 'ControlAvailable': manager, 'LocalNodeState': state}}

def node(id='self', state='ready', manager=True, leader=False):
    return dict(id=id, state=state, manager=manager, leader=leader, reachability='reachable', hostname=id, region='delhi')

class Departures(unittest.TestCase):
    def setUp(self):
        c.JOB['log'] = []

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

    def test_leader_cleans_down_manager(self):
        members = [node(leader=True), node('departed', 'down')]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=members), patch.object(c, 'sh', return_value=(0, 'ok')) as sh:
            c.cleanup_departed()
        self.assertEqual(sh.call_args_list[0].args[0], ['docker', 'node', 'demote', 'departed'])
        self.assertEqual(sh.call_args_list[1].args[0], ['docker', 'node', 'rm', 'departed'])

    def test_cleanup_without_quorum_keeps_region(self):
        members = [node(leader=True), node('departed', 'down')]
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=members), patch.object(c, 'sh', return_value=(1, 'no quorum')) as sh:
            c.cleanup_departed()
        self.assertEqual(sh.call_count, 1)

    def test_returning_node_is_not_removed(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', side_effect=[[node(leader=True), node('other', 'down')], [node(leader=True), node('other')]]), patch.object(c, 'sh') as sh:
            c.cleanup_departed()
        sh.assert_not_called()

    def test_follower_does_not_cleanup(self):
        with patch.object(c, 'docker_info', return_value=info()), patch.object(c, 'nodes', return_value=[node(), node('other', 'down')]), patch.object(c, 'sh') as sh:
            c.cleanup_departed()
        sh.assert_not_called()

if __name__ == '__main__':
    unittest.main()
