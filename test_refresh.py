import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('refresh_console', Path(__file__).parent/'console/console.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class Refresh(unittest.TestCase):
    def test_database_roles_do_not_present_stale_probes_as_current(self):
        status = {'observed_at': time.time() - 60, 'patroni': {'members': [{'name':'pg-1','role':'leader','state':'running','lag':0}]}}
        with patch.object(c, 'control_read', return_value=(200, {'status':status})):
            self.assertEqual(c.patroni(), [])
            status['observed_at'] = time.time()
            self.assertEqual(c.patroni()[0]['name'], 'pg-1')

    def test_parallel_reads_coalesce_and_results_are_isolated(self):
        cache, calls = c.ReadCache(), []
        def load():
            calls.append(1)
            time.sleep(.03)
            return {'items': []}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: cache.get('status', load), range(8)))
        self.assertEqual(len(calls), 1)
        results[0]['items'].append('changed')
        self.assertEqual(cache.get('status', load), {'items': []})

    def test_failures_are_retried(self):
        cache = c.ReadCache()
        with self.assertRaises(ValueError):
            cache.get('state', lambda: (_ for _ in ()).throw(ValueError('offline')))
        self.assertEqual(cache.get('state', lambda: 'recovered'), 'recovered')

    def test_mutation_invalidates_inflight_read(self):
        cache, entered, finish = c.ReadCache(), threading.Event(), threading.Event()
        def load():
            entered.set()
            finish.wait(2)
            return 'old'
        with ThreadPoolExecutor() as pool:
            future = pool.submit(cache.get, 'state', load)
            self.assertTrue(entered.wait(2))
            cache.clear()
            finish.set()
            self.assertEqual(future.result(), 'old')
        self.assertEqual(cache.get('state', lambda: 'new'), 'new')

    def test_native_combined_keeps_logical_regions_and_excludes_secrets(self):
        sw = {'manager':True,'nodes':[{'id':'node','hostname':'fixture','region':'mumbai','main':True,'state':'ready','addr':'192.0.2.1','leader':True}], 'leader':None,'is_main':True}
        services = [{'ID':'svc','Spec':{'Name':'hal_api-3','Labels':{'com.docker.stack.namespace':'hal','lab.region':'hyderabad'},'TaskTemplate':{'Placement':{'Constraints':['node.labels.region == mumbai']},'ContainerSpec':{'Env':['PASSWORD=private']}}}},
                    {'ID':'private','Spec':{'Name':'unrelated','Labels':{}}}]
        tasks = [{'ServiceID':'svc','NodeID':'node','Status':{'State':'running'}}, {'ServiceID':'private','Status':{'State':'running'}}]
        with patch.object(c,'swarm',return_value=sw), patch.object(c,'engine_get',side_effect=[services,tasks]), patch.object(c,'patroni',return_value=[]):
            result = c.view_combined()
        self.assertTrue(result['pilot'])
        self.assertEqual(len(result['tasks']),1)
        self.assertEqual(result['tasks'][0]['region'],'hyderabad')
        self.assertNotIn('private', str(result))

if __name__ == '__main__': unittest.main()

