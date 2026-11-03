"""Serialize each cached raw libpq admin connection across timed-out probes."""
import argparse
import ast
from pathlib import Path
import shutil

WRAPPER = '''# A timed-out gather future may still be using its raw libpq connection.
# Never submit a second native operation on that same connection concurrently.
_BOUNCER_LOCKS = {h: threading.Lock() for h in ("pgbouncer-1", "pgbouncer-2")}


def pgbouncer(h: str) -> dict | None:
    lock = _BOUNCER_LOCKS[h]
    if not lock.acquire(blocking=False):
        return None  # Unknown this cycle; the previous probe is still running.
    try:
        return _pgbouncer_unlocked(h)
    finally:
        lock.release()


'''

def patch_text(source):
    tree = ast.parse(source)
    if any(isinstance(n, ast.FunctionDef) and n.name == '_pgbouncer_unlocked' for n in tree.body):
        return source
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'pgbouncer')
    if not any(isinstance(n, ast.Import) and any(a.name == 'threading' for a in n.names) for n in tree.body):
        raise ValueError('Expected existing threading import')
    lines = source.splitlines(keepends=True)
    original = ''.join(lines[node.lineno-1:node.end_lineno])
    renamed = original.replace('def pgbouncer(', 'def _pgbouncer_unlocked(', 1)
    result = ''.join(lines[:node.lineno-1]) + WRAPPER + renamed + ''.join(lines[node.end_lineno:])
    ast.parse(result)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    target = args.app / 'control.py'
    source = target.read_text()
    patched = patch_text(source)
    if patched == source:
        print('PgBouncer probe serialization already current')
    else:
        args.backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(target, args.backup / 'control.py')
        target.write_text(patched)
        print('Protected cached PgBouncer probe connections; original retained in the requested backup directory')
