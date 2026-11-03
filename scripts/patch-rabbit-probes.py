"""Keep RabbitMQ node availability independent of notification-queue readiness."""
import argparse
import ast
from pathlib import Path
import shutil

REPLACEMENT = '''def rabbit() -> dict | None:
    def from_node(h):
        nodes = http_json(f"http://{h}:15672/api/nodes", auth="lab:lab", timeout=0.8)
        q, queue_state, queue_error = None, "available", None
        try:
            q = http_json(f"http://{h}:15672/api/queues/%2F/notifications", auth="lab:lab", timeout=0.8)
        except urllib.error.HTTPError as exc:
            queue_state = "not_created" if exc.code == 404 else "failed"
            queue_error = f"HTTP {exc.code}"
        except Exception as exc:
            queue_state, queue_error = "failed", type(exc).__name__
        return {"asked": h, "nodes": {n["name"].split("@")[-1]: n.get("running", False) for n in nodes},
                "queue_probe": {"state": queue_state, "error": queue_error},
                "queue": None if q is None else {
                    "leader": (q.get("leader") or "").split("@")[-1],
                    "members": [m.split("@")[-1] for m in q.get("members", [])],
                    "online": [m.split("@")[-1] for m in q.get("online", [])],
                    "ready": q.get("messages_ready", 0),
                    "unacked": q.get("messages_unacknowledged", 0),
                    "consumers": q.get("consumers", 0)}}
    return first_ok([lambda h=h: from_node(h) for h in ("rabbit-1", "rabbit-2", "rabbit-3")])
'''

def patch_text(source):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'rabbit')
    if any(isinstance(n, ast.Constant) and n.value == 'queue_probe' for n in ast.walk(node)):
        return source
    lines = source.splitlines(keepends=True)
    result = ''.join(lines[:node.lineno-1]) + REPLACEMENT + ''.join(lines[node.end_lineno:])
    ast.parse(result)
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    target = args.app / 'control.py'
    source = target.read_text(encoding='utf-8')
    patched = patch_text(source)
    if source == patched:
        print('Rabbit node and queue probes already separated')
    else:
        args.backup.mkdir(parents=True, exist_ok=False)
        shutil.copy2(target, args.backup / 'control.py')
        target.write_text(patched, encoding='utf-8')
        print('Separated RabbitMQ node availability from queue readiness; source backup retained')
