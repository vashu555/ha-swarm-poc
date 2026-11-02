"""Install Redis health/flow fixes in the separate lab source; preserve rollback copies."""
import argparse
import ast
from pathlib import Path
import shutil


def replace_once(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError('Unexpected lab source near: ' + old[:75])
    return text.replace(old, new, 1)


def python_source(raw, assets):
    tree = ast.parse(raw)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'redis_state')
    lines = raw.splitlines(keepends=True)
    raw = ''.join(lines[:fn.lineno-1]) + (assets/'redis_probe.py').read_text(encoding='utf-8').rstrip() + '\n' + ''.join(lines[fn.end_lineno:])
    replacements = [
        ('    master = (st["redis"] or {}).get("master")',
         '    master = (st["redis"] or {}).get("master") if (st["redis"] or {}).get("write_ready") else None'),
        ('delta(f"{svc}:redis", c.get("redis_ops", 0))',
         'delta(f"{svc}:redis", max(0, c.get("redis_ops", 0) - c.get("redis_errors", 0)))'),
        ('if n.startswith("redis-") and v == "REPLICA":',
         'if n.startswith("redis-") and v == "REPLICA" and (st["redis"] or {}).get("nodes", {}).get(n, {}).get("link") == "up":'),
        ('    if LAYOUT == "zones":\n        return svc, DOCKER.containers.get(containers()[svc]["container"])\n    return svc, DOCKER.containers.get(f"hal-{svc}")',
         '    if LAYOUT in ("zones", "swarm"):\n        return svc, DOCKER.containers.get(containers()[svc]["container"])\n    return svc, DOCKER.containers.get(f"hal-{svc}")'),
    ]
    for old, new in replacements:
        raw = replace_once(raw, old, new)
    ast.parse(raw)
    return raw


def html_source(html, assets):
    if 'function redisHealth(' not in html:
        anchor = 'function metric(n,kind){'
        html = replace_once(html, anchor, (assets/'redis_health.js').read_text(encoding='utf-8')+'\n'+anchor)
    replacements = [
        ("  if(kind==='redis'){const r=((st.redis||{}).nodes||{})[n]||{};return r.up?(r.role==='master'?`${r.replicas} replicas`:`link ${r.link||'-'}`):''}\n  if(kind==='sentinel'){return ((st.redis||{}).sentinels||{})[n]?'watching':''}",
         "  if(kind==='redis'||kind==='sentinel')return redisHealth(n,st.containers[n])?.detail||'';"),
        ("    const r=$('r-'+n);r.textContent=role||c.kind;",
         "    const health=redisHealth(n,c);if(health){role=health.role;if(health.state)el.classList.add(health.state)}\n    const r=$('r-'+n);r.textContent=role||c.kind;"),
        ("r.className='role'+(/LEADER|MASTER|ACTIVE/.test(role)?' lead':/OLD|EXITED|CUT|PAUSED|RECONCILING|STALE/.test(role)?' warnr':'');",
         "r.className='role'+(/OLD|EXITED|CUT|PAUSED|RECONCILING|STALE|RESPONDING|TILT|NO QUORUM|BLOCKED|LINK DOWN|UNVERIFIED|UNCONFIRMED/.test(role)?' warnr':/LEADER|MASTER|ACTIVE/.test(role)?' lead':'');"),
        ("  for(const s of SITE_COLS){const all=",
         "  renderRedisHealth();\n  for(const s of SITE_COLS){const all="),
        ("`${up}/${all.length} running`",
         "`${up}/${all.length} containers running · ${all.filter(c=>redisHealth(c.service,c)?.state).length} Redis/Sentinel unhealthy`"),
        ('<span><i style="background:var(--redis)"></i>session (Redis)</span>',
         '<span><i style="background:var(--redis)"></i>Redis extras (sessions are signed tokens)</span>'),
        ("  const c=S.status.containers[sel]||{}, role=S.status.roles[sel]||'';\n  const st=c.state||'?';",
         "  const c=S.status.containers[sel]||{}, health=redisHealth(sel,c), role=health?.role||S.status.roles[sel]||'';\n  const st=health?.state||c.state||'?';"),
        ("()=>{const m=(S.status.redis||{}).master;return [m?'ok':'bad',m?`master ${m}`:'no master']}",
         "()=>{const r=S.status.redis||{};return [r.write_ready?'ok':'bad',r.master?`master ${r.master}${r.write_ready?'':' · writes blocked'}`:'no verified primary']}"),
    ]
    for old, new in replacements:
        html = replace_once(html, old, new)
    if '.chip.unhealthy{' not in html:
        html = replace_once(html, '</style>', (assets/'redis_health.css').read_text(encoding='utf-8')+'\n</style>')
    return html


def patch(source, backup, assets):
    source, backup, assets = Path(source), Path(backup), Path(assets)
    old = {name: (source/name).read_text(encoding='utf-8')
           for name in ('control.py', 'ui/index.html', 'swarm_faults.py')}
    # Validate all transformations before writing any source or creating backups.
    new = {'control.py': python_source(old['control.py'], assets),
           'ui/index.html': html_source(old['ui/index.html'], assets),
           'swarm_faults.py': (assets/'swarm_faults.py').read_text(encoding='utf-8')}
    ast.parse(new['swarm_faults.py'])
    changed = [name for name in old if old[name] != new[name]]
    if not changed:
        return False
    if backup.exists():
        raise RuntimeError('Backup directory already exists; choose a new path')
    for name in changed:
        saved = backup/name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source/name, saved)
    for name in changed:
        (source/name).write_text(new[name], encoding='utf-8')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    assets = Path(__file__).resolve().parent.parent/'lab-patches'
    print('Redis observability installed' if patch(args.source, args.backup, assets) else 'Redis observability already current')
