import importlib.util
from pathlib import Path
import unittest
import urllib.error

spec = importlib.util.spec_from_file_location('rabbit_patch', Path(__file__).parent/'scripts/patch-rabbit-probes.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

def load(queue_result):
    def http_json(url, **kwargs):
        if url.endswith('/nodes'):
            return [{'name': 'rabbit@rabbit-1', 'running': True}, {'name': 'rabbit@rabbit-2', 'running': False}]
        if isinstance(queue_result, Exception):
            raise queue_result
        return queue_result
    scope = {'http_json': http_json, 'urllib': urllib, 'first_ok': lambda probes: probes[0]()}
    exec(patcher.REPLACEMENT, scope)
    return scope['rabbit']()

class RabbitProbes(unittest.TestCase):
    def test_missing_queue_keeps_node_health(self):
        result = load(urllib.error.HTTPError('http://fixture/queue', 404, 'Not found', {}, None))
        self.assertTrue(result['nodes']['rabbit-1'])
        self.assertFalse(result['nodes']['rabbit-2'])
        self.assertIsNone(result['queue'])
        self.assertEqual(result['queue_probe']['state'], 'not_created')

    def test_queue_timeout_is_not_reported_as_missing_queue_or_dead_node(self):
        result = load(TimeoutError())
        self.assertTrue(result['nodes']['rabbit-1'])
        self.assertEqual(result['queue_probe']['state'], 'failed')
        self.assertEqual(result['queue_probe']['error'], 'TimeoutError')

    def test_authorization_failure_is_not_hidden_as_missing_queue(self):
        result = load(urllib.error.HTTPError('http://fixture/queue', 401, 'Unauthorized', {}, None))
        self.assertEqual(result['queue_probe'], {'state': 'failed', 'error': 'HTTP 401'})

    def test_healthy_queue_keeps_its_existing_fields(self):
        result = load({'leader':'rabbit@rabbit-3','members':['rabbit@rabbit-1'],'online':['rabbit@rabbit-1'],'messages_ready':4,'messages_unacknowledged':2,'consumers':1})
        self.assertEqual(result['queue']['leader'], 'rabbit-3')
        self.assertEqual(result['queue']['ready'], 4)
        self.assertEqual(result['queue']['unacked'], 2)
        self.assertEqual(result['queue_probe']['state'], 'available')

    def test_source_patch_is_idempotent(self):
        source = 'import urllib.request\n\ndef rabbit():\n    return None\n\nx = 7\n'
        patched = patcher.patch_text(source)
        self.assertEqual(patched, patcher.patch_text(patched))
        self.assertIn('x = 7', patched)

if __name__ == '__main__':
    unittest.main()
