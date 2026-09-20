"""Real management ledger and delivery worker; only CD2 transport is fake."""
from datetime import timedelta
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch
import test_planner as tp
import test_delivery as td
import test_delivery_cloud as tc
from test_management import ManagementTests


class CancelEntryTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: ManagementTests.setUpClass())
    setUp = ManagementTests.setUp
    fixture = ManagementTests.fixture
    fence = ManagementTests.fence
    apply_body = ManagementTests.apply_body

    def uploaded(self):
        f=self.fixture(td.DeliveryTests)
        f.rule.update(fallback=True,unlimited=True)
        f.worker=f.m.Delivery(f.repo,f.auth,None,f.cloud,rules=[f.rule],revalidate=lambda p:f.publication)
        bid=f.prepared()
        f.worker.reconcile(bid,now=tp.NOW)
        f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        stub=Mock();stub.StartRemoteUpload.return_value=NS(upload_id='original-id')
        stub.RemoteUploadChannel.return_value=tc.Call([])
        cloud=tp.load('delivery_cloud').HostDeliveryCloud(NS(scopes={'cloud':{'allowed_prefixes':['/115/staging']}},_client=lambda *a:(NS(stub=stub),tc.PB(),[])))
        self.addCleanup(cloud.close);f.worker.cloud=cloud
        with patch.object(f.worker,'_directories'):
            f.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        file=f.worker.bundle(bid)['files'][0]
        self.assertEqual(('original-id','UNKNOWN',False),(file['upload_id'],file['state'],file['reader_stopped']))
        self.plugin.config=self.plugin.config.model_copy(update={'delivery':{'rules':[f.rule]}})
        self.plugin.runtime=NS(lock=threading.RLock(),busy=False,stages=tp.load('runtime').OwnedStages(lambda:True),scope_worker=lambda identity:f.worker,authority=f.auth,check=lambda:None)
        self.assertFalse(self.plugin.config.enabled)
        self.m=tp.load('ui');self.view=self.m.Views(self.plugin);self.user=NS(username='admin')
        self.f=f;self.bid=bid;self.stub=stub
        self.before={p.name:p.read_bytes() for p in f.local.iterdir()}
        return file

    def preview(self):
        return self.view.cancel_preview(self.bid,self.m.BundlePreview(**self.fence(),revision=self.f.worker.bundle(self.bid)['revision']),user=self.user)

    def unchanged_io(self):
        self.assertEqual(self.before,{p.name:p.read_bytes() for p in self.f.local.iterdir()})
        self.stub.StartRemoteUpload.assert_called_once()
        self.stub.RemoteReadData.assert_not_called();self.stub.RemoteHashProgress.assert_not_called()
        self.stub.DeleteFile.assert_not_called();self.stub.MoveFile.assert_not_called()

    def test_known_unknown_id_cancels_through_management_and_replays_once(self):
        file=self.uploaded();events=[]
        rows=[tc.message('original-id','read_data',offset=0,length=1,lazy_read=False),tc.message('original-id','hash_data',hash_type=1),tc.message('alien','status_changed',status=5),tc.message('original-id','status_changed',status=2)]
        call=tc.Call(rows)
        self.stub.RemoteUploadChannel.side_effect=lambda *a,**k:(events.append('channel') or call)
        def control(req,**kwargs):
            self.assertTrue(self.f.worker.bundle(self.bid)['cancel_intent'])
            with self.repo.connection() as db:
                self.assertEqual('UNKNOWN',db.execute('SELECT state FROM management_operations').fetchone()[0])
            events.append(('cancel',req.upload_id,hasattr(req,'cancel')))
        self.stub.RemoteUploadControl.side_effect=control
        p=self.preview();self.assertEqual([],p.blockers)
        body=self.apply_body(p)
        with patch.object(self.f.m.LocalSource,'hash',side_effect=AssertionError('cancel hashed local source')),patch.object(self.f.worker,'cleanup',side_effect=AssertionError('cancel invoked cleanup')):
            result=self.view.apply_cancel(self.bid,body,user=self.user)
        b=self.f.worker.bundle(self.bid)
        self.assertEqual(('APPLIED','ABANDONED','CANCELLED',True),(result.state,b['state'],b['files'][0]['state'],b['files'][0]['reader_stopped']))
        self.assertTrue(b['cancel_intent']);self.assertTrue(call.cancelled)
        self.assertEqual(['channel',('cancel','original-id',True)],events)
        with self.repo.connection() as db:
            self.assertEqual('FAILED',db.execute('SELECT state FROM plan_actions WHERE id=?',(file['action_id'],)).fetchone()[0])
            self.assertEqual(1,db.execute('SELECT count(*) FROM management_operations').fetchone()[0])
        self.assertEqual(result,self.view.apply_cancel(self.bid,body,user=self.user))
        self.stub.RemoteUploadControl.assert_called_once();self.unchanged_io()

    def test_no_terminal_empty_ack_and_not_found_stay_pending(self):
        for control_error in (None,RuntimeError('NOT_FOUND'),'channel_timeout'):
            with self.subTest(control_error=control_error):
                self.uploaded();call=tc.Call([tc.message('original-id','read_data',offset=0,length=1,lazy_read=False)])
                if control_error=='channel_timeout':
                    def timed_out():
                        yield tc.message('alien','status_changed',status=2)
                        raise TimeoutError('channel deadline')
                    call=tc.Call(timed_out())
                self.stub.RemoteUploadChannel.return_value=call
                self.stub.RemoteUploadControl.side_effect=control_error if isinstance(control_error,Exception) else None
                p=self.preview();self.assertEqual([],p.blockers)
                self.view.apply_cancel(self.bid,self.apply_body(p),user=self.user)
                b=self.f.worker.bundle(self.bid)
                self.assertEqual(('CANCEL_PENDING','UNKNOWN',False),(b['state'],b['files'][0]['state'],b['files'][0]['reader_stopped']))
                self.assertTrue(b['cancel_intent']);self.assertTrue(call.cancelled)
                self.assertEqual('original-id',b['files'][0]['upload_id']);self.unchanged_io()
                for scope in ('monitor','staging','downloader_task','downloader_data'):
                    cleanup=self.view.cleanup_preview(self.bid,self.m.CleanupPreview(**self.fence(),revision=b['revision'],scope=scope),user=self.user)
                    self.assertIn('EXTERNAL_OUTCOME_UNKNOWN',cleanup.blockers)
                    self.assertIn('CLEANUP_PERMISSION_DISABLED',cleanup.blockers)

    def test_unknown_without_id_unrelated_action_and_publication_stay_blocked(self):
        for change in ('missing_id','wrong_action','wrong_payload','wrong_receipt','wrong_targets','wrong_files','wrong_generation','unrelated','publication','unit_barrier'):
            with self.subTest(change=change):
                file=self.uploaded();b=self.f.worker.bundle(self.bid)
                with self.repo.connection(write=True) as db:
                    if change=='missing_id':b['files'][0]['upload_id']=None
                    if change=='wrong_action':b['files'][0]['action_id']='organized:0'
                    if change=='wrong_payload':db.execute("UPDATE plan_actions SET payload='{}' WHERE id=?",(file['action_id'],))
                    if change=='wrong_receipt':db.execute('DELETE FROM action_receipts WHERE action_id=?',(file['action_id'],))
                    if change=='wrong_targets':db.execute("UPDATE plan_actions SET targets='{}' WHERE id=?",(file['action_id'],))
                    if change=='wrong_files':db.execute("UPDATE plan_actions SET files='[]' WHERE id=?",(file['action_id'],))
                    if change=='wrong_generation':db.execute('UPDATE plan_actions SET task_generation=task_generation+1 WHERE id=?',(file['action_id'],))
                    if change=='unrelated':db.execute("UPDATE plan_actions SET state='UNKNOWN' WHERE id='organized:0'")
                    if change=='publication':b.update(publication_action='publish-owned',state='PUBLISH_OUTCOME_UNKNOWN')
                    if change=='unit_barrier':db.execute("UPDATE target_units SET publish_phase='PUBLISHING'")
                    self.f.worker._save(b,db)
                p=self.preview();self.assertIn('EXTERNAL_OUTCOME_UNKNOWN',p.blockers)
                with self.assertRaises(Exception) as caught:self.view.apply_cancel(self.bid,self.apply_body(p),user=self.user)
                self.assertEqual(409,caught.exception.status_code)
                self.stub.RemoteUploadControl.assert_not_called();self.unchanged_io()

    def test_shared_reference_still_blocks_exact_id(self):
        self.uploaded()
        self.f.auth.prepare('B','round',self.f.auth.plan('A')['snapshot'],now=tp.NOW)
        p=self.preview();self.assertIn('SHARED_REFERENCE',p.blockers)
        with self.assertRaises(Exception) as caught:self.view.apply_cancel(self.bid,self.apply_body(p),user=self.user)
        self.assertEqual(409,caught.exception.status_code);self.stub.RemoteUploadControl.assert_not_called()

    def test_current_fences_and_receipt_digest_are_rechecked(self):
        for change in ('generation','revision','receipt','bundle'):
            with self.subTest(change=change):
                file=self.uploaded();p=self.preview();self.assertEqual([],p.blockers)
                if change=='generation':self.plugin.generation+=1
                elif change=='revision':
                    current=self.config.view()
                    changed=self.config.preview({'dry_run':False},current['revision'],current['digest'],'admin')
                    self.config.initialize(changed['config'])
                    self.assertGreater(self.config.view()['revision'],current['revision'])
                elif change=='receipt':
                    with self.repo.connection(write=True) as db:db.execute('DELETE FROM action_receipts WHERE action_id=?',(file['action_id'],))
                else:
                    b=self.f.worker.bundle(self.bid);self.f.worker._save(b)
                with self.assertRaises(Exception) as caught:self.view.apply_cancel(self.bid,self.apply_body(p),user=self.user)
                self.assertEqual(409,caught.exception.status_code)
                self.stub.RemoteUploadControl.assert_not_called()
                self.plugin.generation=4


if __name__=='__main__':unittest.main()
