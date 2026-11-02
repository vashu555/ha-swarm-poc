"""Authenticated host-local SIGKILL for simulated lab tasks, never arbitrary containers."""
import hmac
import json
import re
import subprocess
import time
from live_map import engine_get

TARGET = re.compile(r'(?:api-[1-4]|(?:pg|etcd|kafka|rabbit|redis|sentinel)-[1-3]|(?:haproxy|pgbouncer|relay|router|feed|bridge|worker)-[12])')

def authorized(token):
    if not token or len(token) > 1024:
        return False
    result = subprocess.run(['docker','swarm','join-token','-q','manager'],capture_output=True,text=True,timeout=5)
    expected = result.stdout.strip() if result.returncode == 0 else ''
    return bool(expected) and hmac.compare_digest(expected, token)

def crash_local(body):
    target, cid, service = (str(body.get(key, '')) for key in ('target','container_id','service_id'))
    if not TARGET.fullmatch(target) or not re.fullmatch(r'[a-f0-9]{64}', cid) or not re.fullmatch(r'[a-z0-9]{25}',service):
        raise ValueError('Invalid simulated lab task')
    record = engine_get('/containers/'+cid+'/json')
    labels = (record.get('Config') or {}).get('Labels') or {}
    if (labels.get('com.docker.stack.namespace') != 'hal' or
        labels.get('com.docker.swarm.service.name') != 'hal_'+target or
        labels.get('com.docker.swarm.service.id') != service):
        raise ValueError('Container does not belong to the selected lab service')
    start = time.monotonic()
    result = subprocess.run(['docker','kill','--signal','KILL',cid],capture_output=True,text=True,timeout=5)
    if result.returncode:
        raise RuntimeError('Crash signal failed; task may already have exited')
    state = engine_get('/containers/'+cid+'/json').get('State') or {}
    if state.get('Running'):
        raise RuntimeError('Crash not confirmed by Docker')
    return {'ok':True,'signal':'SIGKILL','confirmed_at':time.time(),
            'signal_ms':round((time.monotonic()-start)*1000,1)}
