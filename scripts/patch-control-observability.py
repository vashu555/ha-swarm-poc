"""Add collector timestamps without replacing the user's uncommitted lab source."""
import argparse
import ast
from pathlib import Path
import shutil

def patch(source, backup):
    raw = source.read_bytes()
    marker = b'            STATE["status"] = st'
    if b'st["observed_at"] = time.time()' in raw:
        return False
    if raw.count(marker) != 1:
        raise RuntimeError('Unexpected control.py layout; source was not changed')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    replacement = newline.join([b'            st["observed_at"] = time.time()',
        b'            st["collection_duration_s"] = st["observed_at"] - t0', marker])
    updated = raw.replace(marker, replacement)
    ast.parse(updated.decode('utf-8'))
    if backup.exists():
        raise RuntimeError('Backup path already exists; choose a new backup path')
    shutil.copy2(source, backup)
    source.write_bytes(updated)
    return True

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--backup', type=Path, required=True)
    args = p.parse_args()
    print('Collector timestamps installed' if patch(args.source, args.backup) else 'Collector timestamps already installed')
