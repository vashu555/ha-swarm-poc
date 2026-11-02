import importlib.util
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'console'))
import fault_agent as agent
spec=importlib.util.spec_from_file_location('swarm_faults',ROOT/'lab-patches/swarm_faults.py')
f=importlib.util.module_from_spec(spec); spec.loader.exec_module(f)

class Faults(unittest.TestCase):
    def client(self,remote=False):
        c=MagicMock()
        c.info.return_value={'Swarm':{'NodeID':'local'}}
        c.services.get.return_value.tasks.return_value=[{'NodeID':'remote' if remote else 'local','Status':{'State':'running','ContainerStatus':{'ContainerID':'a'*64}}}]
        c.containers.get.return_value.attrs={'Config':{'Labels':{'com.docker.stack.namespace':'hal','com.docker.swarm.service.name':'hal_pg-1'}},'State':{'Running':False}}
        c.nodes.get.return_value.attrs={'Status':{'Addr':'192.0.2.2'}}
        c.swarm.attrs={'JoinTokens':{'Manager':'fixture-secret'}}
        return c
    def test_real_kill_precedes_holding_fault(self):
        c=self.client(); order=[]
        c.containers.get.return_value.kill.side_effect=lambda **kw:order.append(('kill',kw))
        c.services.get.return_value.scale.side_effect=lambda n:order.append(('scale',n))
        result=f.crash_task(c,'pg-1')
        self.assertEqual(order,[('kill',{'signal':'SIGKILL'}),('scale',0)])
        self.assertIn('signal_ms',result)
    def test_no_graceful_fallback_on_remote_failure(self):
        c=self.client(remote=True); c.services.get.return_value.id='b'*25
        with patch.object(f.urllib.request,'urlopen',side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError): f.crash_task(c,'pg-1')
        c.services.get.return_value.scale.assert_not_called()
    def test_no_crash_when_task_transition_is_unfinished(self):
        c=self.client(); c.services.get.return_value.tasks.return_value*=2
        with self.assertRaises(RuntimeError): f.crash_task(c,'pg-1')
        c.containers.get.assert_not_called()
    def test_nonlab_container_is_not_killed(self):
        c=self.client(); c.containers.get.return_value.attrs['Config']['Labels']['com.docker.stack.namespace']='finflock-live'
        with self.assertRaises(RuntimeError): f.crash_task(c,'pg-1')
        c.containers.get.return_value.kill.assert_not_called()
        c.services.get.return_value.scale.assert_not_called()
    def test_peer_rejects_protected_or_foreign_container(self):
        with self.assertRaises(ValueError):agent.crash_local({'target':'control','container_id':'a'*64,'service_id':'b'*25})
        with patch.object(agent,'engine_get',return_value={'Config':{'Labels':{}}}),patch.object(agent.subprocess,'run') as run:
            with self.assertRaises(ValueError):agent.crash_local({'target':'pg-1','container_id':'a'*64,'service_id':'b'*25})
            run.assert_not_called()
    def test_peer_authentication_never_accepts_missing_token(self):
        with patch.object(agent.subprocess,'run') as run:
            self.assertFalse(agent.authorized(''))
            run.assert_not_called()
    def test_recovery_does_not_accept_failed_member_or_wrong_kind(self):
        ledger=f.DrillLedger(); ident=ledger.begin('pg-1','postgres','LEADER')
        ledger.injected(ident,{'killed_at':time.time()-2,'signal_ms':5})
        st={'containers':{'pg-1':{'kind':'postgres','state':'exited'},'pg-2':{'kind':'postgres','state':'running'},'etcd-1':{'kind':'etcd','state':'running'}},'roles':{'pg-1':'LEADER','etcd-1':'LEADER'}}
        ledger.observe(st); self.assertEqual(ledger.snapshot()[0]['status'],'waiting')
        st['roles']['pg-2']='LEADER';ledger.observe(st)
        self.assertEqual(ledger.snapshot()[0]['replacement'],'pg-2')
    def test_restore_cancels_unfinished_timer(self):
        ledger=f.DrillLedger(); ident=ledger.begin('pg-1','postgres','LEADER')
        ledger.injected(ident,{'killed_at':time.time(),'signal_ms':5})
        ledger.restore('pg-1')
        self.assertEqual(ledger.snapshot()[0]['status'],'cancelled')
    def test_new_drill_cannot_inherit_earlier_duration(self):
        ledger=f.DrillLedger(); ident=ledger.begin('pg-1','postgres','LEADER')
        ledger.injected(ident,{'killed_at':time.time()-1000,'signal_ms':5})
        ledger.begin('pg-3','postgres','LEADER')
        self.assertEqual(ledger.snapshot()[1]['status'],'superseded')

if __name__=='__main__':unittest.main()
