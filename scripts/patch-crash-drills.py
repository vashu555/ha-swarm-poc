"""Install abrupt simulated failures while preserving an exact backup of existing lab fixes."""
import ast
import argparse
from pathlib import Path
import shutil

FUNCTION = '''
def swarm_chaos(target, action, why):
    if action in ('pause', 'cut'):
        return {'error': 'Use Crash for an abrupt held failure. Network partition and freeze are different drills.'}
    ctr = containers().get(target)
    if not ctr or target in PROTECTED:
        return {'error':'Not an eligible lab component'}
    kind = ctr['kind']
    was = STATE.get('status',{}).get('roles',{}).get(target,'')
    if action in ('kill','stop'):
        ident = FAULT_DRILLS.begin(target,kind,was)
        # A new drill must not inherit recovery clocks from earlier attempts.
        PENDING_RECOVERY[:] = [p for p in PENDING_RECOVERY if p['kind'] != kind]
        MANUAL_FAILURES.add(target)
        try:
            result = crash_task(DOCKER,target,SWARM_STACK)
        except Exception:
            FAULT_DRILLS.error(ident)
            ev(f'{target}: abrupt failure injection could not be confirmed; inspect its actual state','warn')
            return {'error':'Crash/hold not confirmed. Check the host console and live tasks.'}
        FAULT_DRILLS.injected(ident,result)
        ev(f'{target} CRASHED (SIGKILL), signal/hold confirmed in {result["signal_ms"]} ms; held down until Restore: by {why}','bad')
        _CACHE['t'] = 0
        return {'ok':True,'drill_id':ident,**result}
    if action in ('start','heal','unpause'):
        DOCKER.services.get(f'{SWARM_STACK}_{target}').scale(1)
        MANUAL_FAILURES.discard(target)
        FAULT_DRILLS.restore(target)
        PENDING_RECOVERY[:] = [p for p in PENDING_RECOVERY if p['kind'] != kind]
        _CACHE['t'] = 0
        ev(f'{target}: Restore requested; waiting for application readiness','info')
        return {'ok':True}
    return {'error':'Choose Crash or Restore'}

'''

def patch(root,backup,assets):
    source = root/'control.py'
    raw = source.read_text(encoding='utf-8')
    if 'def swarm_chaos(' in raw:
        raise RuntimeError('Crash drill patch already present; refusing to overwrite local changes')
    ui = root/'ui/index.html'
    if backup.exists(): raise RuntimeError('Choose a new backup directory')
    backup.mkdir(parents=True)
    shutil.copy2(source,backup/'control.py')
    shutil.copy2(ui,backup/'index.html')
    raw = raw.replace('import uuid\n','import uuid\nfrom swarm_faults import crash_task, DrillLedger\n')
    raw = raw.replace('SETTINGS = {"traders": 40}', 'SETTINGS = {"traders": int(os.environ.get("LAB_TRADERS", "40"))}')
    raw = raw.replace('SETTINGS["autoheal"] = True', 'SETTINGS["autoheal"] = False')
    raw = raw.replace('AUTOHEAL_AFTER = 15.0', 'AUTOHEAL_AFTER = 15.0\nMANUAL_FAILURES = set()\nFAULT_DRILLS = DrillLedger()')
    start = raw.index('    if LAYOUT == "swarm":\n',raw.index('def chaos('))
    end = raw.index('    elif action == "kill":',start)
    raw = raw[:start]+'    if LAYOUT == "swarm":\n        return swarm_chaos(target, action, why)\n'+raw[end:]
    raw = raw.replace('\ndef chaos(',FUNCTION+'\ndef chaos(',1)
    raw = raw.replace('if (c["state"] in ("exited", "dead") and svc not in PROTECTED', 'if (c["state"] in ("exited", "dead") and svc not in MANUAL_FAILURES and svc not in PROTECTED')
    raw = raw.replace('    ro, ctr = st["roles"], st["containers"]','    FAULT_DRILLS.observe(st)\n    ro, ctr = st["roles"], st["containers"]',1)
    raw = raw.replace('"events": list(EVENTS)[:80],', '"failure_drills": FAULT_DRILLS.snapshot(), "events": list(EVENTS)[:80],')
    # Do not keep a stopped database marked leader from its expiring DCS record.
    raw = raw.replace('    for m in (st["patroni"] or {}).get("members", []):\n', '    for m in (st["patroni"] or {}).get("members", []):\n        if st.get("containers",{}).get(m["name"],{}).get("state") != "running" or m.get("state") not in ("running","streaming"):\n            continue\n',1)
    # Fresh write-readiness verification, rather than the DCS role alone.
    old = '    return first_ok([lambda h=h: http_json(f"http://{h}:8008/cluster", timeout=0.8) for h in ("pg-1", "pg-2", "pg-3")])'
    new = '''    cluster = first_ok([lambda h=h: http_json(f"http://{h}:8008/cluster", timeout=0.8) for h in ("pg-1", "pg-2", "pg-3")])
    for member in (cluster or {}).get('members',[]):
        if member.get('role') == 'leader':
            try:
                http_json(f"http://{member['name']}:8008/primary",timeout=0.6)
            except Exception:
                member['role'] = 'unverified'
    return cluster'''
    if old not in raw: raise RuntimeError('Unexpected Patroni probe layout')
    raw = raw.replace(old,new,1)
    # Parallel workload failures: this simulates lost workloads, never powers off a PC.
    anchor = '    for svc, c in containers().items():\n        if c["site"] == site:'
    replacement = '''    if LAYOUT == 'swarm':
        targets = [svc for svc,c in containers().items() if c['site']==site and svc not in PROTECTED]
        with cf.ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(lambda svc: chaos(svc,mapping[action],why=f'zone {action}'),targets))
        errors = [r['error'] for r in outcomes if r.get('error')]
        return {'error': '; '.join(errors)} if errors else {'ok':True}
    for svc, c in containers().items():
        if c["site"] == site:'''
    if anchor not in raw: raise RuntimeError('Unexpected site action layout')
    raw = raw.replace(anchor,replacement,1)
    ast.parse(raw)
    html = ui.read_text(encoding='utf-8')
    html = html.replace('<button class="danger" data-a="stop">Stop (graceful)</button>','')
    html = html.replace('<button class="danger" data-site="${s}:stop" title="docker stop: clean shutdown of every service">Shut down</button>','')
    old = ':`<button class="danger" data-site="${s}:down">Zone down</button><button data-site="${s}:up">Zone up</button><button class="danger" data-site="${s}:cut">Cut network</button><button data-site="${s}:heal">Heal</button>`'
    new = ':`<button class="danger" data-site="${s}:down">Crash zone workloads</button><button data-site="${s}:up">Restore zone</button>`'
    html = html.replace(old,new)
    html = html.replace('Kill (crash)','Crash (SIGKILL)')
    html = html.replace('A few seconds of retries.', 'Retries while each subsystem detects failure; PostgreSQL can take around 20 seconds or longer.')
    html = html.replace('Like Kubernetes: a crashed container is started again after 15 s. A lost zone stays down until Zone up.', 'Unexpected crashes may be restarted. Injected failures stay down until Restore.')
    html = html.replace('</script>', '\n'+(assets/'failure_panel.js').read_text(encoding='utf-8')+'\n</script>')
    source.write_text(raw,encoding='utf-8')
    ui.write_text(html,encoding='utf-8')
    shutil.copy2(assets/'swarm_faults.py',root/'swarm_faults.py')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--backup',type=Path,required=True)
    args=parser.parse_args()
    patch(args.source,args.backup,Path(__file__).resolve().parent.parent/'lab-patches')
