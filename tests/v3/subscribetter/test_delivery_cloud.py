"""Protocol doubles only: exact raw fields, bounded reads, no live acceptance."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile
import test_planner as tp

class PB:
    def __getattr__(self,name):return lambda **kw:NS(**kw)

class Call:
    def __init__(self,rows):self.rows=rows;self.cancelled=False
    def __iter__(self):return iter(self.rows)
    def cancel(self):self.cancelled=True

def message(uid,kind,**kw):return NS(upload_id=uid,WhichOneof=lambda name:kind,**{kind:NS(**kw)})

class CloudTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((tp.PLUGIN/'delivery_cloud.py').exists(),'raw delivery adapter missing')
        self.m=tp.load('delivery_cloud');self.d=tp.load('delivery')
        self.stub=Mock();self.pb=PB();self.sources=NS(scopes={'s':{'root':'/115','allowed_prefixes':['/115/test']}},accounts={'s':'1'},plugin=Mock())
        self.sources._client=lambda *a:(NS(stub=self.stub),self.pb,[])
        self.cloud=self.m.HostDeliveryCloud(self.sources)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'x';self.path.write_bytes(b'a'*(2*1024*1024))
        self.source=self.d.LocalSource(self.path,[self.tmp.name]);self.source.hash();self.addCleanup(self.source.close)
    def test_move_explicit_skip_no_merge(self):
        self.stub.MoveFile.return_value=NS(success=True)
        self.cloud.move('s','/115/test/staging/b','/115/test/incoming/b')
        req=self.stub.MoveFile.call_args.args[0]
        self.assertEqual(2,req.conflictPolicy);self.assertFalse(req.moveAcrossClouds);self.assertFalse(req.handleConflictRecursively)
    def test_unknown_id_reads_nothing_and_overlap_is_not_completion(self):
        rows=[message('alien','read_data',offset=0,length=9,lazy_read=False),message('own','read_data',offset=0,length=1024*1024,lazy_read=False),message('own','read_data',offset=0,length=1024*1024,lazy_read=False)]
        call=Call(rows);self.stub.RemoteUploadChannel.return_value=call
        self.stub.RemoteReadData.side_effect=lambda r,**kw:NS(success=True,bytes_received=len(r.data),is_last_chunk=r.is_last_chunk)
        result=self.cloud.pump('s','own','device',self.source,budget=5)
        self.assertTrue(call.cancelled);self.assertEqual(2,self.stub.RemoteReadData.call_count)
        self.assertEqual('UNKNOWN',result['state']);self.assertTrue(result['reader_stopped']);self.assertEqual(2*1024*1024,result['bytes_sent'])
    def test_cancel_ack_alone_is_unknown_and_never_reads(self):
        self.stub.RemoteUploadChannel.return_value=Call([message('own','read_data',offset=0,length=10,lazy_read=False)])
        result=self.cloud.pump('s','own','device',self.source,cancel=True,budget=5)
        self.stub.RemoteReadData.assert_not_called();self.assertEqual('UNKNOWN',result['state']);self.assertTrue(result['reader_stopped'])
    def test_live_cancel_subscribes_before_control_and_observe_never_reads(self):
        def control(*a,**kw):
            self.stub.RemoteUploadChannel.assert_called_once()
        self.stub.RemoteUploadControl.side_effect=control
        self.stub.RemoteUploadChannel.return_value=Call([message('own','status_changed',status=2)])
        self.assertEqual('CANCELLED',self.cloud.pump('s','own','device',self.source,cancel=True)['state'])
        self.stub.RemoteUploadChannel.return_value=Call([message('own','read_data',offset=0,length=10,lazy_read=False),message('own','hash_data',hash_type=1),message('alien','status_changed',status=5)])
        result=self.cloud.pump('s','own','device',NS(stop=self.source.stop),observe_only=True)
        self.assertEqual('UNKNOWN',result['state']);self.assertEqual(0,result['bytes_sent'])
        self.stub.RemoteReadData.assert_not_called();self.stub.RemoteHashProgress.assert_not_called()

    def test_live_placeholder_hash_is_not_remote_verification(self):
        raw=NS(fullPathName='/115/test/x',id='placeholder-uuid',isDirectory=False,fileHashes={2:self.source.sha1},size=self.source.size)
        self.stub.FindFileByPath.return_value=raw
        self.sources.cloud_stat=Mock(side_effect=ValueError('P115_OBJECT_AMBIGUOUS'))
        with self.assertRaisesRegex(ValueError,'P115_OBJECT_AMBIGUOUS'):
            self.cloud.stat('s','/115/test/x')

    def test_live_budget_pause_then_restart_channel_before_resume(self):
        clock=[0.0];events=[]
        first=Call([message('own','read_data',offset=0,length=1024*1024,lazy_read=False),message('own','read_data',offset=1024*1024,length=1024*1024,lazy_read=False),message('own','status_changed',status=4)])
        self.stub.RemoteUploadChannel.side_effect=lambda *a,**k:(events.append('channel') or first)
        def read(req,**kw):
            clock[0]=4.0
            return NS(success=True,bytes_received=len(req.data),is_last_chunk=req.is_last_chunk)
        self.stub.RemoteReadData.side_effect=read
        self.stub.RemoteUploadControl.side_effect=lambda req,**kw:events.append('pause' if hasattr(req,'pause') else 'resume')
        with patch.object(self.m.time,'monotonic',side_effect=lambda:clock[0]):
            result=self.cloud.pump('s','own','device',self.source,budget=5)
        self.assertEqual('PAUSE',result['state']);self.assertEqual(1024*1024,result['bytes_sent']);self.assertEqual(['channel','pause'],events)
        resumed=self.m.HostDeliveryCloud(self.sources)
        self.stub.RemoteUploadChannel.side_effect=lambda *a,**k:(events.append('channel') or Call([message('own','read_data',offset=1024*1024,length=1024*1024,lazy_read=False),message('own','status_changed',status=5)]))
        result=resumed.pump('s','own','device',self.source,budget=5,resume=True)
        self.assertEqual('FINISH',result['state']);self.assertEqual(['channel','pause','channel','resume'],events)
        self.stub.StartRemoteUpload.assert_not_called()

    def test_live_start_subscribes_once_and_pump_reuses_that_channel(self):
        events=[];call=Call([message('own','status_changed',status=5)])
        self.stub.RemoteUploadChannel.side_effect=lambda *a,**k:(events.append('channel') or call)
        self.stub.StartRemoteUpload.side_effect=lambda *a,**k:(events.append('start') or NS(upload_id='own'))
        uid=self.cloud.start('s','/115/test/x',self.source,device_id='device',budget=5)
        self.assertEqual('FINISH',self.cloud.pump('s',uid,'device',self.source,budget=5)['state'])
        self.assertEqual(['channel','start'],events);self.assertTrue(call.cancelled)

    def test_live_block_hash_pause_keeps_completed_blocks_for_next_pump(self):
        clock=[0.0];cache={};self.source.hash_blocks=cache
        req=message('own','hash_data',hash_type=1,block_size=1024*1024,HasField=lambda name:True)
        self.stub.RemoteUploadChannel.return_value=Call([req,message('own','status_changed',status=4)])
        original=self.source.read;offsets=[]
        def slow(offset,size):
            offsets.append(offset);clock[0]=4.0
            return original(offset,size)
        with patch.object(self.source,'read',side_effect=slow),patch.object(self.m.time,'monotonic',side_effect=lambda:clock[0]):
            result=self.cloud.pump('s','own','device',self.source,budget=5)
        self.assertEqual('PAUSE',result['state']);self.stub.RemoteHashProgress.assert_not_called()
        self.stub.RemoteUploadChannel.return_value=Call([req,message('own','status_changed',status=5)])
        self.source.hash_blocks=cache
        with patch.object(self.source,'read',side_effect=lambda offset,size:(offsets.append(offset) or original(offset,size))):
            result=self.cloud.pump('s','own','device',self.source,budget=5,resume=True)
        self.assertEqual('FINISH',result['state']);self.assertEqual([0,1024*1024],offsets)
        proof=self.stub.RemoteHashProgress.call_args.args[0]
        self.assertEqual(self.source.md5,proof.hash_value);self.assertEqual(2,len(proof.block_hashes))

    def test_live_pause_ack_without_event_remains_unknown(self):
        clock=[0.0]
        def rows():
            clock[0]=4.0
            yield message('own','read_data',offset=0,length=10,lazy_read=False)
        self.stub.RemoteUploadChannel.return_value=Call(rows())
        with patch.object(self.m.time,'monotonic',side_effect=lambda:clock[0]):
            result=self.cloud.pump('s','own','device',self.source,budget=5)
        self.assertEqual('UNKNOWN',result['state']);self.assertTrue(result['pause_requested'])
        self.stub.RemoteReadData.assert_not_called()

    def test_rapid_state_true_status_one_still_miss(self):
        client=Mock();client.upload_file_init.return_value={'state':True,'status':1,'reuse':False,'bucket':'secret'}
        self.cloud._p115=lambda scope:client;self.cloud._parent=lambda *args:'5'
        result=self.cloud.rapid('s','/115/test/x',self.source)
        self.assertEqual({'state':'MISS'},result);client.upload_file.assert_not_called()
        self.assertEqual(self.source.range_hash('1-5'),client.upload_file_init.call_args.kwargs['read_range_bytes_or_hash']('1-5'))
    def test_bad_range_and_unknown_hash_fail_without_body(self):
        self.stub.RemoteUploadChannel.return_value=Call([message('own','read_data',offset=self.source.size,length=1,lazy_read=False)])
        with self.assertRaises(ValueError):self.cloud.pump('s','own','device',self.source,budget=5)
        self.stub.RemoteReadData.assert_not_called()

if __name__=='__main__':unittest.main()
