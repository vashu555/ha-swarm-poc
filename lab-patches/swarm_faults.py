"""Abrupt failure injection and observed recovery measurements for the simulated Swarm lab."""
import concurrent.futures
import json
import threading
import time
import urllib.request
import uuid

def crash_task(client, target, stack='hal'):
    signal_started = time.monotonic()
    service = client.services.get(stack+'_'+target)
    tasks = [t for t in service.tasks(filters={'desired-state':'running'}) if t.get('Status',{}).get('State') == 'running']
    if not tasks:
        raise RuntimeError('No running task to crash; restore and wait for readiness first')
    if len(tasks) != 1:
        raise RuntimeError('Wait for the previous task transition to finish before injecting a crash')
    local_id = client.info()['Swarm']['NodeID']
    def kill(task):
        cid = task['Status']['ContainerStatus']['ContainerID']
        if task['NodeID'] == local_id:
            container = client.containers.get(cid)
            labels = container.attrs.get('Config',{}).get('Labels',{})
            if labels.get('com.docker.stack.namespace') != stack or labels.get('com.docker.swarm.service.name') != stack+'_'+target:
                raise RuntimeError('Task identity mismatch')
            container.kill(signal='SIGKILL')
            container.reload()
            if container.attrs.get('State',{}).get('Running'):
                raise RuntimeError('Crash not confirmed by Docker')
            return time.time()
        node = client.nodes.get(task['NodeID']).attrs
        address = node['Status']['Addr']
        client.swarm.reload()
        token = client.swarm.attrs['JoinTokens']['Manager']
        body = json.dumps({'target':target,'container_id':cid,'service_id':service.id}).encode()
        request = urllib.request.Request('http://'+address+':9790/peer/crash-local',data=body,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
        try:
            with urllib.request.urlopen(request,timeout=6) as response:
                result = json.load(response)
        except Exception:
            raise RuntimeError('Remote crash failed. Update/start the host console on the destination PC; no graceful fallback was used') from None
        if not result.get('ok'):
            raise RuntimeError('Remote crash was not confirmed')
        return time.time()  # controller receipt time avoids cross-PC wall-clock skew
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        confirmed = list(pool.map(kill,tasks))
    # Hold only after a confirmed kill. An unreachable peer never falls back to shutdown.
    service.scale(0)
    return {'killed_at':max(confirmed),'signal_ms':round((time.monotonic()-signal_started)*1000,1)}

class DrillLedger:
    def __init__(self):
        self.lock = threading.RLock()
        self.rows = []
    def begin(self,target,kind,role):
        with self.lock:
            for row in self.rows:
                if row['kind'] == kind and row['status'] == 'waiting':
                    row.update(status='superseded',note='A new drill replaced this measurement')
            row = dict(id=uuid.uuid4().hex[:12],target=target,kind=kind,old_role=role,
                       requested_at=time.time(),status='injecting',note='Sending abrupt crash signal')
            self.rows.insert(0,row)
            self.rows = self.rows[:30]
            return row['id']
    def injected(self,ident,result):
        with self.lock:
            row = next(r for r in self.rows if r['id']==ident)
            row.update(result,status='waiting',note='Crash confirmed; waiting for another healthy member')
    def error(self,ident):
        with self.lock:
            next(r for r in self.rows if r['id']==ident).update(status='error',note='Injection or hold failed; inspect actual task state')
    def restore(self,target):
        with self.lock:
            for row in self.rows:
                if row['target']==target and row['status']=='waiting':
                    row.update(status='cancelled',note='Restored before a takeover was observed')
    def snapshot(self):
        with self.lock:
            return [dict(row) for row in self.rows]
    def observe(self,st):
        containers, roles = st.get('containers',{}),st.get('roles',{})
        with self.lock:
            for row in self.rows:
                if row['status'] != 'waiting': continue
                target,kind = row['target'],row['kind']
                holder = None
                if kind == 'kafka':
                    partitions = [p for ps in (st.get('kafka') or {}).get('topics',{}).values() for p in ps]
                    broker = int(target.rsplit('-',1)[1])
                    if partitions and all(p.get('leader',-1)>=0 and p['leader']!=broker for p in partitions):
                        holder = 'surviving Kafka brokers'
                elif row['old_role'] in ('LEADER','MASTER','ACTIVE','QUEUE LEADER'):
                    holder = next((name for name,role in roles.items() if name!=target and role==row['old_role'] and
                                   containers.get(name,{}).get('kind')==kind and containers.get(name,{}).get('state')=='running'),None)
                else:
                    candidates = [name for name,c in containers.items() if name!=target and c.get('kind')==kind and c.get('state')=='running']
                    # Only a successful probe counts; a running process alone is insufficient.
                    services = st.get('services') or {}
                    holder = next((name for name in candidates if services.get(name) and not services[name].get('error')),None)
                    if kind=='haproxy': holder=next((name for name in candidates if (st.get('lbs') or {}).get(name)),None)
                    if kind=='pgbouncer': holder=next((name for name in candidates if (st.get('bouncers') or {}).get(name,{}).get('up')),None)
                    if kind=='postgres': holder=next((m['name'] for m in (st.get('patroni') or {}).get('members',[]) if m['name'] in candidates and m.get('state') in ('running','streaming')),None)
                    if kind=='redis': holder=next((name for name in candidates if (st.get('redis') or {}).get('nodes',{}).get(name,{}).get('up')),None)
                    if kind=='sentinel': holder=next((name for name in candidates if (st.get('redis') or {}).get('sentinels',{}).get(name)),None)
                    if kind=='etcd': holder=next((name for name in candidates if (st.get('etcd') or {}).get(name,{}).get('up')),None)
                    if kind=='rabbitmq': holder=next((name for name in candidates if (st.get('rabbit') or {}).get('nodes',{}).get(name)),None)
                if kind == 'redis':
                    rd = st.get('redis') or {}
                    holder = rd.get('master') if rd.get('write_ready') and rd.get('master') != target else None
                if kind == 'sentinel' and holder:
                    detail = (st.get('redis') or {}).get('sentinel_details', {}).get(holder, {})
                    if detail.get('tilt') or not detail.get('quorum_ok'):
                        holder = None
                if holder:
                    observed = time.time()
                    row.update(status='recovered',replacement=holder,observed_at=observed,
                               elapsed_s=round(observed-row['killed_at'],3),
                               note='Healthy peer observed; transaction recovery is shown separately by the audit')
