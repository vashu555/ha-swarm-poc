import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import threading
import types
import unittest

spec = importlib.util.spec_from_file_location('probe_patch', Path(__file__).parent/'scripts/patch-pgbouncer-probe.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
SOURCE = '''import threading
_BOUNCER = {}
def pgbouncer(h: str) -> dict:
    return native(h)
'''

def wrapper(native):
    scope = {'native': native}
    exec(patcher.patch_text(SOURCE), scope)
    return scope['pgbouncer']

class ProbeSafety(unittest.TestCase):
    def test_overlapping_probe_never_reuses_busy_native_connection(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def native(host):
            calls.append(host)
            entered.set()
            if not release.wait(3):
                raise TimeoutError('test gate')
            return {'up': True}
        probe = wrapper(native)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(probe, 'pgbouncer-1')
            self.assertTrue(entered.wait(1))
            try:
                self.assertIsNone(pool.submit(probe, 'pgbouncer-1').result(timeout=1))
                self.assertEqual(calls, ['pgbouncer-1'])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=1), {'up': True})
        self.assertEqual(probe('pgbouncer-1'), {'up': True})

    def test_other_pooler_is_not_blocked_by_a_stuck_probe(self):
        entered, release = threading.Event(), threading.Event()
        def native(host):
            if host == 'pgbouncer-1':
                entered.set()
                release.wait(3)
            return {'up': True}
        probe = wrapper(native)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(probe, 'pgbouncer-1')
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(pool.submit(probe, 'pgbouncer-2').result(timeout=1), {'up': True})
            finally:
                release.set()
            first.result(timeout=1)

    def test_exception_releases_host_lock_for_next_probe(self):
        count = 0
        def native(host):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError('connection failed')
            return {'up': False}
        probe = wrapper(native)
        with self.assertRaises(RuntimeError):
            probe('pgbouncer-1')
        self.assertEqual(probe('pgbouncer-1'), {'up': False})

    def test_patch_is_idempotent_and_preserves_existing_function_body(self):
        patched = patcher.patch_text(SOURCE)
        self.assertEqual(patched, patcher.patch_text(patched))
        functions = {n.name: n for n in ast.parse(patched).body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(ast.dump(functions['_pgbouncer_unlocked'].body[0]), ast.dump(ast.parse(SOURCE).body[-1].body[0]))

if __name__ == '__main__':
    unittest.main()
