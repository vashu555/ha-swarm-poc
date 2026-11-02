import concurrent.futures as cf
import importlib.util
from pathlib import Path
import struct
import unittest
from types import SimpleNamespace

root=Path(__file__).parent
spec=importlib.util.spec_from_file_location('negative_dns',root/'scripts/lab-negative-dns.py')
dns=importlib.util.module_from_spec(spec);spec.loader.exec_module(dns)

class Checks(unittest.TestCase):
    def test_dns_negative_answer_preserves_question_and_id(self):
        for kind in (1,28):
            question=b'\x07redis-3\x00'+struct.pack('!HH',kind,1)
            packet=struct.pack('!6H',143,256,1,0,0,0)+question
            reply=dns.response(packet)
            ident,flags,count,answers,authority,extra=struct.unpack('!6H',reply[:12])
            self.assertEqual((ident,flags&15,count,answers,authority,extra),(143,3,1,0,0,0))
            self.assertEqual(reply[12:],question)

    def test_dns_drops_responses_and_malformed_queries(self):
        for packet in (b'',b'x'*2000,struct.pack('!6H',1,0x8000,1,0,0,0),
                       struct.pack('!6H',1,0,1,0,0,0)+b'\x40broken'):
            self.assertIsNone(dns.response(packet))

    def probe(self,values,reported='redis-3',tilt=False):
        class RedisError(Exception):pass
        class Client:
            def __init__(self,host,**kwargs):self.host=host
            def info(self,section):
                if section=='sentinel':return {'sentinel_tilt':int(tilt)}
                value=values.get(self.host)
                if value is None:raise RedisError()
                return value
            def config_get(self,name):return {name:1}
            def execute_command(self,*args):return b'OK'
        Redis=SimpleNamespace(Redis=Client,exceptions=SimpleNamespace(RedisError=RedisError))
        class Sentinel:
            def discover_master(self,name):return reported,6379
        namespace={'cf':cf,'redis':Redis,'_SENT':Sentinel()}
        exec((root/'lab-patches/redis_probe.py').read_text(encoding='utf-8'),namespace)
        return namespace['redis_state']()

    def test_stale_sentinel_address_is_not_a_live_primary(self):
        value=self.probe({'redis-1':{'role':'slave','master_link_status':'down'}})
        self.assertIsNone(value['master'])
        self.assertFalse(value['write_ready'])

    def test_primary_needs_a_replica_to_be_writable(self):
        value=self.probe({'redis-3':{'role':'master','connected_slaves':0,'min_slaves_good_slaves':0}})
        self.assertEqual(value['master'],'redis-3')
        self.assertFalse(value['write_ready'])

    def test_tilt_is_not_a_failover_vote(self):
        value=self.probe({'redis-3':{'role':'master','connected_slaves':1,'min_slaves_good_slaves':1}},tilt=True)
        self.assertTrue(value['write_ready'])
        self.assertFalse(value['failover_ready'])

    def test_healthy_primary_and_quorum(self):
        value=self.probe({'redis-3':{'role':'master','connected_slaves':2,'min_slaves_good_slaves':2}})
        self.assertTrue(value['write_ready'])
        self.assertTrue(value['failover_ready'])

    def test_recovery_timer_waits_for_writable_redis(self):
        spec=importlib.util.spec_from_file_location('drill',root/'lab-patches/swarm_faults.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        ledger=module.DrillLedger();ident=ledger.begin('redis-3','redis','MASTER')
        ledger.injected(ident,{'killed_at':__import__('time').time()})
        state={'containers':{'redis-1':{'kind':'redis','state':'running'}},'roles':{'redis-1':'MASTER'},
               'redis':{'master':'redis-1','write_ready':False}}
        ledger.observe(state)
        self.assertEqual(ledger.snapshot()[0]['status'],'waiting')
        state['redis']['write_ready']=True
        ledger.observe(state)
        self.assertEqual(ledger.snapshot()[0]['replacement'],'redis-1')

    def test_sentinel_tilt_does_not_complete_recovery_timer(self):
        spec=importlib.util.spec_from_file_location('drill',root/'lab-patches/swarm_faults.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        ledger=module.DrillLedger();ident=ledger.begin('sentinel-3','sentinel','')
        ledger.injected(ident,{'killed_at':__import__('time').time()})
        state={'containers':{'sentinel-1':{'kind':'sentinel','state':'running'}},'roles':{},
               'redis':{'sentinels':{'sentinel-1':True},'sentinel_details':{'sentinel-1':{'tilt':True,'quorum_ok':True}}}}
        ledger.observe(state)
        self.assertEqual(ledger.snapshot()[0]['status'],'waiting')




class PatchChecks(unittest.TestCase):
    def patcher(self):
        spec=importlib.util.spec_from_file_location('redis_patch',root/'scripts/patch-redis-observability.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_unknown_source_is_rejected_before_writes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'app';(source/'ui').mkdir(parents=True)
            original='def redis_state():\n    return {}\n'
            (source/'control.py').write_text(original,encoding='utf-8')
            (source/'ui/index.html').write_text('custom dashboard',encoding='utf-8')
            (source/'swarm_faults.py').write_text('unchanged',encoding='utf-8')
            with self.assertRaises(RuntimeError):
                self.patcher().patch(source,Path(folder)/'backup',root/'lab-patches')
            self.assertEqual((source/'control.py').read_text(encoding='utf-8'),original)
            self.assertFalse((Path(folder)/'backup').exists())

    def test_combined_map_rejects_tilt_and_blocked_primary(self):
        spec=importlib.util.spec_from_file_location('redis_map',root/'console/live_map.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        status={'redis':{'master':'redis-1','nodes':{'redis-1':{'up':True,'role':'master','write_ready':False}},
            'sentinels':{'sentinel-1':True},'sentinel_details':{'sentinel-1':{'up':True,'tilt':True,'quorum_ok':True}}}}
        self.assertEqual(module.probe_health('redis-1',status),'degraded')
        self.assertEqual(module.probe_health('sentinel-1',status),'degraded')
        status['redis']['nodes']['redis-1']['write_ready']=True
        self.assertEqual(module.probe_health('redis-1',status),'healthy')

if __name__=='__main__':unittest.main()
