import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('boot', Path(__file__).parent / 'scripts/docker-netbird-boot.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

ERROR = {'LocalNodeState': 'error', 'Error': 'manager stopped: failed to listen on remote API address: listen tcp 192.0.2.10:2377: bind: cannot assign requested address'}
def response(code=0, out=''):
    return SimpleNamespace(returncode=code, stdout=out, stderr='')

class RecoveryTests(unittest.TestCase):
    def test_wait_returns_when_interface_arrives(self):
        with patch.object(b, 'addresses', side_effect=[set(), {'192.0.2.10'}]), patch.object(b.time, 'sleep'):
            self.assertTrue(b.wait_for_address(90))

    def test_wait_timeout_keeps_docker_usable(self):
        with patch.object(b, 'addresses', return_value=set()):
            self.assertFalse(b.wait_for_address(0))

    def test_docker_stopped_is_not_started(self):
        with patch.object(b, 'command', return_value=response(3)) as command:
            self.assertFalse(b.recover())
        self.assertEqual(command.call_count, 1)

    def test_healthy_swarm_is_not_restarted(self):
        with patch.object(b, 'command', side_effect=[response(), response(out=json.dumps({'LocalNodeState': 'active'}))]) as command:
            self.assertFalse(b.recover())
        self.assertEqual(command.call_count, 2)

    def test_unrelated_swarm_error_is_not_restarted(self):
        error = {'LocalNodeState': 'error', 'Error': 'cannot reach a manager; no quorum'}
        self.assertIsNone(b.failed_address(error))

    def test_exact_bind_address_must_be_present(self):
        with patch.object(b, 'command', side_effect=[response(), response(out=json.dumps(ERROR))]) as command, patch.object(b, 'addresses', return_value={'192.0.2.11'}):
            self.assertFalse(b.recover())
        self.assertEqual(command.call_count, 2)

    def test_only_confirmed_bind_failure_restarts_docker(self):
        with patch.object(b, 'command', side_effect=[response(), response(out=json.dumps(ERROR)), response()]) as command, patch.object(b, 'addresses', return_value={'192.0.2.10'}), patch.object(Path, 'read_text', side_effect=FileNotFoundError), patch.object(Path, 'write_text'):
            self.assertTrue(b.recover())
        self.assertEqual(command.call_args.args, ('systemctl', 'restart', 'docker'))

    def test_restart_cooldown_prevents_loop(self):
        with patch.object(b, 'command', side_effect=[response(), response(out=json.dumps(ERROR))]) as command, patch.object(b, 'addresses', return_value={'192.0.2.10'}), patch.object(Path, 'read_text', return_value='1000'), patch.object(b.time, 'time', return_value=1100):
            self.assertFalse(b.recover())
        self.assertEqual(command.call_count, 2)

if __name__ == '__main__':
    unittest.main()
