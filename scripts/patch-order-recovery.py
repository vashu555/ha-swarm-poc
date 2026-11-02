"""Explain the real PostgreSQL-first order contract without changing leader-election safety."""
import argparse
import ast
from pathlib import Path
import shutil


def replace(text,old,new):
    if new in text:return text
    if text.count(old)!=1:raise RuntimeError('Unexpected lab source near '+old[:70])
    return text.replace(old,new,1)


def patch(source,backup,assets):
    source,backup,assets=Path(source),Path(backup),Path(assets)
    raw=(source/'control.py').read_text(encoding='utf-8')
    html=(source/'ui/index.html').read_text(encoding='utf-8')
    updated=replace(raw,'TR.add("gave_up")                            # never "Received": the trader knows it did not go through',
        'TR.add("gave_up")                            # no acknowledgement: outcome may be unknown; reconcile the same client_ref')
    ast.parse(updated)
    if 'function databaseRecoveryMessage(' not in html:
        html=replace(html,'function tiles(){',(assets/'order_recovery.js').read_text(encoding='utf-8')+'\nfunction tiles(){')
    html=replace(html,"['Gave up (never Received)',T.gave_up||0,'n']","['Unconfirmed after retries',T.gave_up||0,'n']")
    old='  if(!lead)msg+=`<b>DATABASE DOWN: no PostgreSQL leader.</b> ${[\'pg-1\',\'pg-2\',\'pg-3\'].filter(up).length} of 3 database nodes running. New orders get “try again” and are NOT accepted, so nothing is lost; traders keep retrying with the same order id (errors in the last 10 s: ${errs10}). Prices still flow from Kafka. Start a pg node (or Restore everything) to recover.`;'
    html=replace(html,old,'  if(!lead)msg+=databaseRecoveryMessage(st);')
    html=replace(html,'PostgreSQL uses a 20 s leader lease; other components have their own detection timeouts. Peer readiness and successful transactions are separate measurements.',
        'PostgreSQL crash recovery waits for its leader lease (20 s configured; supported minimum), then promotion and reconnection. This is separate from successful order acknowledgement. New requests retry at the client; Kafka receives committed outbox messages afterward.')
    changes={}
    if raw!=updated:changes['control.py']=updated
    if html!=(source/'ui/index.html').read_text(encoding='utf-8'):changes['ui/index.html']=html
    if not changes:return False
    if backup.exists():raise RuntimeError('Choose a new backup directory')
    for name in changes:
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,saved)
    for name,text in changes.items():(source/name).write_text(text,encoding='utf-8')
    return True


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--backup',type=Path,required=True)
    args=parser.parse_args()
    print('Order-recovery explanation installed' if patch(args.source,args.backup,Path(__file__).resolve().parent.parent/'lab-patches') else 'Order-recovery explanation already current')
