"""Protocol doubles only: exact raw fields, bounded reads, no live acceptance."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
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
