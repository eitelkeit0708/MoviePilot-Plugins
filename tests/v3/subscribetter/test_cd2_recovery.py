"""Fake-clock original-ID recovery; no provider or runtime services."""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import threading
import unittest
import test_planner as tp
import test_delivery as td
import test_delivery_cloud as tc


class RecoveryTests(unittest.TestCase):
    def runtime(self, *, active=True, retired=False, current=True, owned=True, count=1):
        module=tp.load('runtime');clock=[0.0];calls=[];settings={}
        @contextmanager
        def connection():
            yield NS(execute=lambda sql,args:[dict(id=str(n)) for n in range(count)] if 'FROM delivery_bundles' in sql else [])
        def setting(name,*value):
            if value:settings[name]=value[0]
            return settings.get(name)
        def run(kind,bundle_id,limits):
            calls.append((kind,bundle_id,limits['seconds']))
            clock[0]+=limits['seconds'] if count==1 else 7
            return dict(state='UNKNOWN')
        worker=NS(safety_reconcile=lambda bid,*,limits:run('observe',bid,limits),reconcile=lambda bid,*,limits:run('serve',bid,limits),bundle=lambda _: {},_rule=lambda _:dict(enabled=True))
        runtime=object.__new__(module.Runtime);runtime.repository=NS(setting=setting,connection=connection)
        runtime.config=NS(recovery=NS(seconds=30,entries=count));runtime.generation=1
        runtime.plugin=NS(generation=1 if current else 2,_ordinary_work_active=lambda:active)
        runtime.stages=NS(retired=retired);runtime._io=threading.local();runtime.delivery=worker if owned else object();runtime.scope_worker=lambda _:worker
        return module,runtime,clock,calls

    def test_enabled_same_id_is_not_starved_by_observe_only_across_ticks(self):
        module,runtime,clock,calls=self.runtime()
        with patch.object(module.time,'monotonic',side_effect=lambda:clock[0]):
            for _ in range(2):runtime.deadline=clock[0]+30;runtime.safety(runtime.deadline)
        self.assertEqual(['serve','serve'],[r[0] for r in calls])
        self.assertEqual(['0','0'],[r[1] for r in calls])

    def test_off_retired_generation_and_old_scope_never_get_ordinary_work(self):
        for flags in ({'active':False},{'retired':True},{'current':False},{'owned':False}):
            with self.subTest(flags=flags):
                module,runtime,clock,calls=self.runtime(**flags)
                with patch.object(module.time,'monotonic',side_effect=lambda:clock[0]):
                    runtime.deadline=30;runtime.safety(30)
                self.assertEqual([('observe','0',30)],calls)

    def test_earlier_bundle_work_reduces_next_reader_budget(self):
        module,runtime,clock,calls=self.runtime(count=2)
        with patch.object(module.time,'monotonic',side_effect=lambda:clock[0]):
            runtime.deadline=30;runtime.safety(30)
        self.assertEqual([('serve','0',30),('serve','1',23)],calls)

    def fixture(self):
        td.DeliveryTests.setUpClass();f=td.DeliveryTests();f.setUp();self.addCleanup(f.doCleanups)
        f.rule.update(fallback=True,unlimited=True)
        f.worker=f.m.Delivery(f.repo,f.auth,None,f.cloud,rules=[f.rule],revalidate=lambda p:f.publication)
        bid=f.worker.prepare('A','r',publication=f.publication,now=tp.NOW)['bundle_id']
        f.worker.reconcile(bid,now=tp.NOW);f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        return f,bid

    def test_start_return_after_deadline_persists_id_and_closes_pending_channel(self):
        f,bid=self.fixture();clock=[0.0];call=tc.Call([]);stub=Mock();stub.RemoteUploadChannel.return_value=call
        def checkpoint():
            if clock[0]>=30:raise ValueError('TICK_DEADLINE')
        def start(*a,**k):clock[0]=30.1;return NS(upload_id='original-id')
        stub.StartRemoteUpload.side_effect=start
        cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[]),ordinary_checkpoint=checkpoint,safety_checkpoint=checkpoint))
        self.addCleanup(cloud.close);f.worker.cloud=cloud;f.worker.dispatch_gate=checkpoint
        with patch.object(f.worker,'_directories'),patch.object(td.time,'monotonic',side_effect=lambda:clock[0]):
            f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122),limits={'seconds':30})
        file=f.worker.bundle(bid)['files'][0]
        self.assertEqual('original-id',file['upload_id']);self.assertEqual('UNKNOWN',file['state'])
        self.assertFalse(file['reader_stopped']);self.assertTrue(file['local_reader_stopped'])
        self.assertTrue(call.cancelled);self.assertFalse(cloud.pending_channels)
        self.assertIn(file['reader_error']['reason'],('TICK_DEADLINE','READER_BUDGET'))
        stub.StartRemoteUpload.assert_called_once();stub.RemoteReadData.assert_not_called();stub.RemoteHashProgress.assert_not_called()

    def test_protocol_failure_keeps_partial_progress_and_safe_stage_reason(self):
        tc.CloudTests.setUp(self)
        self.addCleanup(self.cloud.close)
        call=tc.Call([tc.message('own','read_data',offset=0,length=9,lazy_read=False),tc.message('own','read_data',offset=0,length=-1,lazy_read=False)])
        self.stub.RemoteUploadChannel.return_value=call
        self.stub.RemoteReadData.side_effect=lambda req,**kw:NS(success=True,bytes_received=len(req.data),is_last_chunk=req.is_last_chunk)
        with self.assertRaises(ValueError) as caught:self.cloud.pump('s','own','device',self.source,budget=5)
        proof=caught.exception.reader_progress
        self.assertEqual(9,proof['bytes_sent']);self.assertEqual(2,proof['requests']);self.assertEqual('UNKNOWN',proof['state'])
        self.assertEqual({'stage':'read_data','reason':'READ_RANGE_INVALID'},proof['error']);self.assertTrue(call.cancelled)

    def test_pending_channel_cleanup_even_if_initial_cloud_checkpoint_rejects(self):
        tc.CloudTests.setUp(self);self.addCleanup(self.cloud.close)
        call=tc.Call([]);self.cloud.pending_channels['s','own']=('device',call)
        self.sources.ordinary_checkpoint=Mock(side_effect=ValueError('TICK_DEADLINE'))
        with self.assertRaises(ValueError):self.cloud.pump('s','own','device',self.source,budget=5)
        self.assertTrue(call.cancelled);self.assertFalse(self.cloud.pending_channels)
        self.stub.RemoteReadData.assert_not_called()

    def test_global_tick_deadline_caps_even_a_later_caller_deadline(self):
        module,runtime,clock,calls=self.runtime()
        with patch.object(module.time,'monotonic',side_effect=lambda:clock[0]):
            runtime.deadline=12;runtime.safety(30)
        self.assertEqual([('serve','0',12)],calls)

    def test_preparation_and_start_share_one_budget_and_reused_channel(self):
        f,bid=self.fixture();clock=[0.0];call=tc.Call([]);stub=Mock();stub.RemoteUploadChannel.return_value=call
        def start(*a,**k):clock[0]+=8;return NS(upload_id='original-id')
        stub.StartRemoteUpload.side_effect=start
        cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[])))
        self.addCleanup(cloud.close);f.worker.cloud=cloud
        def directories(*a):clock[0]+=20
        with patch.object(f.worker,'_directories',side_effect=directories),patch.object(td.time,'monotonic',side_effect=lambda:clock[0]),patch.object(cloud,'pump',wraps=cloud.pump) as pump:
            f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122),limits={'seconds':30})
        self.assertEqual(10,stub.RemoteUploadChannel.call_args.kwargs['timeout'])
        self.assertEqual(10,stub.StartRemoteUpload.call_args.kwargs['timeout'])
        self.assertEqual(2,pump.call_args.kwargs['budget']);self.assertTrue(call.cancelled)
        self.assertEqual(1,stub.RemoteUploadChannel.call_count);stub.StartRemoteUpload.assert_called_once()

    def test_persisted_protocol_progress_and_provider_text_never_become_terminal(self):
        f,bid=self.fixture();cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[])))
        self.addCleanup(cloud.close);stub=Mock();stub.StartRemoteUpload.return_value=NS(upload_id='original-id')
        stub.RemoteUploadChannel.return_value=tc.Call([tc.message('original-id','read_data',offset=0,length=1,lazy_read=False),tc.message('original-id','read_data',offset=0,length=-1,lazy_read=False)])
        stub.RemoteReadData.side_effect=lambda req,**kw:NS(success=True,bytes_received=len(req.data),is_last_chunk=req.is_last_chunk)
        f.worker.cloud=cloud
        with patch.object(f.worker,'_directories'):f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        file=f.worker.bundle(bid)['files'][0]
        self.assertEqual(1,file['progress']['bytes_sent']);self.assertEqual('READ_RANGE_INVALID',file['reader_error']['reason'])
        self.assertFalse(file['reader_stopped']);self.assertEqual('UNKNOWN',file['state'])
        def broken():
            yield tc.message('original-id','read_data',offset=0,length=1,lazy_read=False)
            raise RuntimeError('https://private.invalid/?token=FICTIONAL_SECRET')
        stub.RemoteUploadChannel.return_value=tc.Call(broken())
        f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=183))
        file=f.worker.bundle(bid)['files'][0]
        self.assertEqual(1,file['progress']['bytes_sent']);self.assertEqual('READER_FAILED',file['progress']['error']['reason'])
        self.assertFalse(file['reader_stopped']);self.assertEqual('original-id',file['upload_id'])
        with f.repo.connection() as db:self.assertNotIn('FICTIONAL_SECRET','\n'.join(db.iterdump()))
        stub.StartRemoteUpload.assert_called_once()


    def test_exception_after_start_id_save_before_pump_closes_channel(self):
        f,bid=self.fixture();stub=Mock();call=tc.Call([]);stub.RemoteUploadChannel.return_value=call;stub.StartRemoteUpload.return_value=NS(upload_id='original-id')
        cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[])))
        self.addCleanup(cloud.close);f.worker.cloud=cloud;original=f.worker._receipt
        def receipt(b,file,outcome,evidence,now):
            original(b,file,outcome,evidence,now)
            if evidence.get('state')=='CD2_UPLOADING':raise RuntimeError('FICTIONAL_PRIVATE_RECEIPT_ERROR')
        with patch.object(f.worker,'_directories'),patch.object(f.worker,'_receipt',side_effect=receipt):
            f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        file=f.worker.bundle(bid)['files'][0]
        self.assertTrue(call.cancelled);self.assertFalse(cloud.pending_channels)
        self.assertEqual('original-id',file['upload_id']);self.assertEqual('UNKNOWN',file['state']);self.assertFalse(file['reader_stopped'])
        stub.RemoteReadData.assert_not_called();stub.StartRemoteUpload.assert_called_once()


    def protocol(self,f,events=()):
        stub=Mock();stub.StartRemoteUpload.return_value=NS(upload_id='original-id')
        stub.RemoteUploadChannel.side_effect=lambda *a,**k:tc.Call(events)
        cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[])))
        self.addCleanup(cloud.close);f.worker.cloud=cloud
        return cloud,stub

    def test_proven_pre_start_budget_exhaustion_remains_retryable(self):
        for phase in ('directories','channel','checkpoint','directories_checkpoint'):
            with self.subTest(phase=phase):
                f,bid=self.fixture();cloud,stub=self.protocol(f);clock=[0.0];calls=[]
                def directories(*a):
                    if phase in ('directories','directories_checkpoint'):clock[0]=31
                def channel(*a,**kw):
                    call=tc.Call([]);calls.append(call)
                    if phase=='channel':clock[0]=31
                    return call
                stub.RemoteUploadChannel.side_effect=channel
                if phase=='directories_checkpoint':
                    def checkpoint():
                        if clock[0]>=30:raise ValueError('TICK_DEADLINE')
                    f.worker.dispatch_gate=checkpoint
                if phase=='checkpoint':cloud.sources.ordinary_checkpoint=Mock(side_effect=ValueError('TICK_DEADLINE'))
                with patch.object(f.worker,'_directories',side_effect=directories),patch.object(td.time,'monotonic',side_effect=lambda:clock[0]):
                    f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122),limits={'seconds':30})
                file=f.worker.bundle(bid)['files'][0]
                self.assertEqual('PENDING',file['state']);self.assertIsNone(file.get('upload_id'))
                self.assertEqual('UPLOAD_NOT_SENT_BUDGET',f.worker.bundle(bid)['reason'])
                stub.StartRemoteUpload.assert_not_called();self.assertTrue(all(c.cancelled for c in calls));self.assertFalse(cloud.pending_channels)
                f.worker.dispatch_gate=None
                cloud.sources.ordinary_checkpoint=lambda:None;stub.RemoteUploadChannel.side_effect=lambda *a,**kw:tc.Call([])
                with patch.object(f.worker,'_directories'),patch.object(f.worker,'_remote',return_value=None):
                    for seconds in (300,500):f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=seconds),limits={'seconds':30})
                self.assertEqual('original-id',f.worker.bundle(bid)['files'][0]['upload_id']);stub.StartRemoteUpload.assert_called_once()

    def test_sent_start_timeout_without_id_stays_unknown_and_never_restarts(self):
        for error in (TimeoutError('READER_BUDGET'),ValueError('TICK_DEADLINE')):
            with self.subTest(error=type(error).__name__):
                f,bid=self.fixture();cloud,stub=self.protocol(f);stub.StartRemoteUpload.side_effect=error
                with patch.object(f.worker,'_directories'),patch.object(f.worker,'_remote',return_value=None):
                    for seconds in (122,300,500):f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=seconds),limits={'seconds':30})
                file=f.worker.bundle(bid)['files'][0]
                self.assertEqual('UNKNOWN',file['state']);self.assertIsNone(file.get('upload_id'));stub.StartRemoteUpload.assert_called_once()

    def test_original_id_terminal_observation_survives_unavailable_sources_in_runtime_and_direct(self):
        for caller in ('runtime','direct'):
            for source_failure in ('changed','missing','other'):
                with self.subTest(caller=caller,source_failure=source_failure):
                    f,bid=self.fixture();cloud,stub=self.protocol(f)
                    with patch.object(f.worker,'_directories'):f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122),limits={'seconds':30})
                    files=f.worker.bundle(bid)['files'];path=td.Path(files[-1 if source_failure=='other' else 0]['snapshot']['path'])
                    if source_failure=='missing':path.unlink()
                    else:path.write_bytes(b'changed-source')
                    events=[tc.message('original-id','read_data',offset=0,length=1,lazy_read=False),tc.message('original-id','hash_data',hash_type=1),tc.message('original-id','status_changed',status=10)]
                    stub.RemoteUploadChannel.side_effect=lambda *a,**kw:tc.Call(events)
                    module,runtime,clock,_=self.runtime();runtime.repository=f.repo;runtime.delivery=f.worker;runtime.scope_worker=lambda _:f.worker
                    original=f.worker._source
                    def source(*a):clock[0]+=7;return original(*a)
                    with patch.object(f.worker,'_source',side_effect=source),patch.object(module.time,'monotonic',side_effect=lambda:clock[0]),patch.object(cloud,'pump',wraps=cloud.pump) as pump:
                        if caller=='runtime':runtime.deadline=30;runtime.safety(30)
                        else:f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=300),limits={'seconds':30})
                    file=f.worker.bundle(bid)['files'][0]
                    self.assertEqual('FAILED',file['state']);self.assertTrue(file['reader_stopped']);self.assertEqual('FATALERROR',file['progress']['state'])
                    self.assertEqual('original-id',file['upload_id']);self.assertTrue(pump.call_args.kwargs['observe_only'])
                    self.assertFalse(hasattr(pump.call_args.args[3],'read'));self.assertEqual(16 if source_failure=='other' else 23,pump.call_args.kwargs['budget'])
                    stub.StartRemoteUpload.assert_called_once();stub.RemoteReadData.assert_not_called();stub.RemoteHashProgress.assert_not_called()


    def test_generic_no_id_timeout_before_start_is_not_a_local_budget_receipt(self):
        f,bid=self.fixture();cloud,stub=self.protocol(f)
        cloud.sources._client=Mock(side_effect=TimeoutError('private connection timeout'))
        with patch.object(f.worker,'_directories'),patch.object(f.worker,'_remote',return_value=None):
            for seconds in (122,300,500):f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=seconds),limits={'seconds':30})
        file=f.worker.bundle(bid)['files'][0]
        self.assertEqual('UNKNOWN',file['state']);self.assertIsNone(file.get('upload_id'))
        cloud.sources._client.assert_called_once();stub.StartRemoteUpload.assert_not_called()


    def test_invalid_start_budget_cannot_open_a_channel(self):
        tc.CloudTests.setUp(self);self.addCleanup(self.cloud.close)
        for budget in (float('nan'),float('inf'),float('-inf'),True,31):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(ValueError,'INVALID_READER_BUDGET'):
                    self.cloud.start('s','/115/test/x',self.source,device_id='device',budget=budget)
        self.stub.RemoteUploadChannel.assert_not_called();self.stub.StartRemoteUpload.assert_not_called()



if __name__=='__main__':unittest.main()
