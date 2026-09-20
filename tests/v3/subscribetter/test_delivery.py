"""W07 real SQLite/files; provider doubles do not assert live acceptance."""
import copy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_planner as tp


class Cloud:
    def __init__(self):
        self.objects = {}; self.calls = []; self.results = ['MISS']; self.running = False

    def ensure_directory(self, scope, path):
        self.calls.append(('mkdir', path))
        self.objects.setdefault(path, dict(path=path, id=path, directory=True))
        return self.objects[path]

    def stat(self, scope, path):
        return copy.deepcopy(self.objects.get(path))

    def inventory(self,scope,path):
        return [copy.deepcopy(v) for p,v in self.objects.items() if p.startswith(path+'/')]

    def rapid(self, scope, path, source):
        self.calls.append(('rapid', path))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception): raise result
        if result == 'HIT':
            self.objects[path] = dict(path=path, id=path, sha1=source.sha1, size=source.size, account_ref='own')
        return {'state': result}

    def refresh(self, scope, path): self.calls.append(('refresh', path))

    def start(self, scope, path, source, *, device_id, budget=10):
        self.calls.append(('start', path)); self.running = True
        return 'upload-owned'

    def pump(self, scope, upload_id, device_id, source, *, cancel=False, budget=10):
        self.calls.append(('pump', upload_id))
        return {'state': 'CANCELLED' if cancel else 'INQUEUE', 'reader_stopped': not self.running}

    def move(self, scope, source, destination):
        self.calls.append(('move', source))
        for p, v in list(self.objects.items()):
            if p == source or p.startswith(source + '/'):
                n = destination + p[len(source):]
                self.objects[n] = dict(v, path=n); del self.objects[p]
        return {'success': True}


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = tp.load('repository'); cls.p = tp.load('planner'); cls.s = tp.load('scheduler')
        if (tp.PLUGIN / 'delivery.py').exists(): cls.m = tp.load('delivery')

    def setUp(self):
        self.assertTrue((tp.PLUGIN / 'delivery.py').exists(), 'W07 delivery missing')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.local = self.root/'organized'; self.local.mkdir()
        self.repo = self.r.Repository(self.root/'state.db'); self.auth = self.p.Authority(self.repo)
        t = self.repo.submit('intent', self.r.Target('电影','themoviedb','42'), {}, 'test',42,True)
        self.repo.complete_handoff(t['id'],t['generation'])
        self.unit = self.p.TargetUnit(self.r.Target.from_task(t)); self.key = self.unit.key
        self.s.Scheduler(self.repo).open_opportunity('round',t['id'],[self.unit],mode='ONESHOT',config=self.s.ScheduleConfig(supersession_limit=3,supersession_seconds=3600),now=tp.NOW)
        self.auth.set_revisions('policy','parse')
        self.files = [dict(index=0,path='release/movie.mkv',size=100,role='video',targets=[self.key],requires=[1]),
                      dict(index=1,path='release/movie.zh.srt',size=3,role='subtitle',targets=[self.key],requires=[])]
        snap = dict(candidate_key='candidate',infohash='a'*40,downloader='own',save_path='/test',policy_revision='policy',parse_revision='parse',
                    current={self.key:dict(state='MISSING',revision=0)},targets={self.key:dict(action='ACQUIRE',reason='MISSING',quality=[1],evidence_keys=[],evidence_source='none')},
                    torrent_files=self.files,selected_indices=[0,1],verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
        self.auth.prepare('A','round',snap,now=tp.NOW); self.auth.claim('A',self.auth.vector([self.key]),now=tp.NOW)
        for f in self.files:
            path=self.local/Path(f['path']).name; path.write_bytes(b'x'*f['size'])
            aid='organized:'+str(f['index'])
            self.auth.begin_attempt(aid,'A',self.auth.vector([self.key]),'ORGANIZE',[0,1],{'file_index':f['index']},now=tp.NOW)
            self.auth.record_result(aid,'SUCCEEDED',{'path':str(path)},now=tp.NOW)
            with self.repo.connection(write=True) as db:
                db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?)',('A',f['index'],'/download/'+str(f['index']),str(path),f['size'],hashlib.sha256(path.read_bytes()).hexdigest(),'COMPLETE',json.dumps({'action_id':aid})))
        self.rule=dict(id='r',enabled=True,local_root=str(self.local),read_roots=[str(self.local)],cloud_scope_id='cloud',staging_root='/115/staging',incoming_root='/115/incoming',consumer_roots=['/115/incoming'],excluded_local_roots=[],stable_seconds=0,scan_interval=60,rapid_misses=2,rapid_interval=60,fallback=False,fallback_gb=None,unlimited=False)
        self.cloud=Cloud()
        self.publication={self.key:dict(raw={'title':'source'},classification={'state':'complete'})}
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda plan:self.publication)

    def prepared(self): return self.worker.prepare('A','r',publication=self.publication,now=tp.NOW)['bundle_id']

    def test_manifest_index_binding_and_idempotent_hash(self):
        bid=self.prepared(); b=self.worker.bundle(bid)
        self.assertEqual([0,1],[a['file_index'] for a in b['manifest']['assets']])
        self.assertEqual('release/movie.mkv',b['manifest']['assets'][0]['relative_path'])
        self.assertEqual('movie.mkv',b['files'][0]['relative_path'])
        with patch.object(self.m.LocalSource,'hash',side_effect=AssertionError('rehashed stable content')):
            self.assertEqual(bid,self.prepared())

    def test_missing_required_asset_blocks_bundle(self):
        (self.local/'movie.zh.srt').unlink()
        with self.assertRaises((ValueError,FileNotFoundError)): self.prepared()
        with self.repo.connection() as db: self.assertEqual(0,db.execute('SELECT count(*) FROM delivery_bundles').fetchone()[0])

    def test_changed_inode_invalidates_cached_hash(self):
        bid=self.prepared(); path=self.local/'movie.mkv'; old=path.stat(); path.unlink(); path.write_bytes(b'y'*100)
        os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'CHANGED'): self.worker.reconcile(bid,now=tp.NOW)
        self.assertFalse(any(c[0]=='rapid' for c in self.cloud.calls))

    def test_rule_overlap_and_empty_fallback_are_not_unlimited(self):
        bad=dict(self.rule,incoming_root='/115/staging/incoming')
        with self.assertRaises(ValueError): self.m.validate_rules([bad])
        with self.assertRaises(ValueError): self.m.validate_rules([dict(self.rule,scan_interval=0)])
        for value,expected in [(None,False),(0,False),('0.0000001',True)]:
            self.assertEqual(expected,self.m.fallback_allowed(dict(self.rule,fallback=True,fallback_gb=value),100))
        self.assertFalse(self.m.fallback_allowed(dict(self.rule,fallback=True,fallback_gb='0.000000099'),100))

    def test_misses_only_and_retry_due_survive_restart(self):
        bid=self.prepared(); self.cloud.results=[ValueError('AUTH_FAILED'),'MISS','MISS']
        self.worker.reconcile(bid,now=tp.NOW)
        self.assertEqual(0,self.worker.bundle(bid)['files'][0]['misses'])
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        self.assertEqual(1,self.worker.bundle(bid)['files'][0]['misses'])
        calls=len(self.cloud.calls); self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=62))
        self.assertEqual(calls,len(self.cloud.calls))
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        b=self.worker.bundle(bid); self.assertEqual(2,b['files'][0]['misses']); self.assertEqual('RAPID_EXHAUSTED',b['reason'])
        self.assertFalse(any(c[0]=='start' for c in self.cloud.calls))

    def all_remote(self):
        bid=self.prepared(); self.cloud.results=['HIT']
        for n in range(4):self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61*n))
        return bid

    def test_cd2_unique_queue_absence_not_success(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        self.assertEqual(1,sum(c[0]=='start' for c in self.cloud.calls))
        self.assertEqual('upload-owned',self.worker.bundle(bid)['files'][0]['upload_id'])
        self.assertNotEqual('VERIFIED',self.worker.bundle(bid)['files'][0]['state'])

    def test_live_cd2_start_pumps_immediately_and_reconnect_never_restarts(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        with patch.object(self.cloud,'pump',return_value=dict(state='UNKNOWN',reader_stopped=True,requests=0,bytes_sent=0)) as pump:
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
            self.assertEqual(1,pump.call_count)
            f=self.worker.bundle(bid)['files'][0]
            self.assertEqual('UNKNOWN',f['state']);self.assertTrue(f['local_reader_stopped']);self.assertFalse(f['reader_stopped'])
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        self.assertEqual(1,sum(c[0]=='start' for c in self.cloud.calls))
        self.assertEqual('upload-owned',self.worker.bundle(bid)['files'][0]['upload_id'])

    def test_live_cd2_expired_authority_observes_without_opening_source(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW);self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        def observe(*args,**kwargs):
            self.assertTrue(kwargs['observe_only']);self.assertFalse(hasattr(args[3],'read'))
            return dict(state='UNKNOWN',reader_stopped=True,requests=0,bytes_sent=0)
        with patch.object(self.worker,'_valid',side_effect=ValueError('CURRENT_UNVERIFIED')),patch.object(self.worker,'_source',side_effect=AssertionError('lost authority read')),patch.object(self.cloud,'pump',side_effect=observe):
            result=self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        self.assertEqual('UNKNOWN',result['state']);self.assertEqual(1,sum(c[0]=='start' for c in self.cloud.calls))

    def test_live_cd2_exact_terminal_failure_is_durable_without_retry(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        with patch.object(self.cloud,'pump',return_value=dict(state='FATALERROR',reader_stopped=True,requests=1,bytes_sent=0)):
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        f=self.worker.bundle(bid)['files'][0]
        self.assertEqual('FAILED',f['state']);self.assertTrue(f['reader_stopped'])
        with patch.object(self.cloud,'pump',side_effect=AssertionError('terminal already known')):
            result=self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'},now=tp.NOW)
        self.assertEqual('ABANDONED',result['state']);self.assertEqual(1,sum(c[0]=='start' for c in self.cloud.calls))

    def test_live_cd2_durable_pause_resumes_same_id_after_worker_restart(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        with patch.object(self.cloud,'pump',return_value=dict(state='PAUSE',reader_stopped=True,requests=2,bytes_sent=3)):
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        restarted=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        def resumed(scope,uid,device,source,**kw):
            self.assertEqual('upload-owned',uid);self.assertTrue(kw['resume'])
            return dict(state='UNKNOWN',reader_stopped=True,requests=0,bytes_sent=0)
        with patch.object(self.cloud,'pump',side_effect=resumed):restarted.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        self.assertEqual(1,sum(c[0]=='start' for c in self.cloud.calls))

    def test_live_cd2_finish_proof_survives_remote_readback_failure(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        with patch.object(self.cloud,'pump',return_value=dict(state='FINISH',reader_stopped=True)),patch.object(self.worker,'_remote',side_effect=ValueError('P115_OBJECT_AMBIGUOUS')):
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        f=self.worker.bundle(bid)['files'][0];self.assertEqual('UNKNOWN',f['state']);self.assertEqual('FINISH',f['progress']['state'])
        with patch.object(self.cloud,'pump',side_effect=AssertionError('no terminal replay required')),patch.object(self.worker,'_remote',return_value=dict(sha1=f['sha1'],size=f['size'],p115_id='8')):
            self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=122))
        self.assertEqual('VERIFIED',self.worker.bundle(bid)['files'][0]['state'])

    def test_refresh_failure_does_not_repeat_rapid(self):
        bid=self.prepared();self.cloud.results=['HIT']
        with patch.object(self.cloud,'refresh',side_effect=[None,ValueError('REFRESH_FAILED')]):
            self.worker.reconcile(bid,now=tp.NOW)
        self.assertTrue(self.worker.bundle(bid)['files'][0]['refresh_pending'])
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        self.assertEqual(1,sum(c[0]=='rapid' and c[1].endswith('movie.mkv') for c in self.cloud.calls))
        self.assertFalse(self.worker.bundle(bid)['files'][0]['refresh_pending'])

    def test_publish_unknown_restart_does_not_move_twice(self):
        bid=self.all_remote();original=self.cloud.move
        def lost(*args): original(*args);raise TimeoutError()
        with patch.object(self.cloud,'move',side_effect=lost):self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.auth.vector([self.key])[self.key]['publish_phase'])
        self.worker.reconcile(bid,now=tp.NOW+timedelta(minutes=6))
        self.assertEqual(1,sum(c[0]=='move' for c in self.cloud.calls))
        self.assertEqual('HANDED_OFF',self.auth.vector([self.key])[self.key]['publish_phase'])
        self.assertEqual('WAIT_CONSUMER',self.worker.bundle(bid)['state'])

    def test_publish_skip_is_conflict_not_success(self):
        bid=self.all_remote();b=self.worker.bundle(bid)
        self.cloud.objects[b['incoming']]=dict(id='unrelated',path=b['incoming'],directory=True)
        result=self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        self.assertEqual('DESTINATION_CONFLICT',result['reason'])
        self.assertFalse(any(c[0]=='move' for c in self.cloud.calls))

    def test_unlisted_remote_asset_blocks_entire_bundle_publication(self):
        bid=self.all_remote();path=self.worker.bundle(bid)['staging']+'/foreign.txt'
        self.cloud.objects[path]={'id':'foreign','path':path,'size':1,'sha1':'a'*40}
        with self.assertRaisesRegex(ValueError,'BUNDLE_CONTENTS_CHANGED'):self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        self.assertFalse(any(c[0]=='move' for c in self.cloud.calls))

    def test_exclusion_revision_checked_inside_publish_transaction(self):
        bid=self.all_remote();original=self.auth.set_transfer_phase
        def race(*args,**kw):
            result=original(*args,**kw)
            self.worker.exclusions.add('deny',{'candidate_key':'candidate'},reason='test')
            return result
        with patch.object(self.auth,'set_transfer_phase',side_effect=race):
            with self.assertRaisesRegex(ValueError,'EXCLUSIONS_CHANGED'):self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        self.assertFalse(any(c[0]=='move' for c in self.cloud.calls))

    def test_late_success_after_supersede_only_old_staging(self):
        bid=self.prepared();original=self.cloud.rapid
        def stale(*args):
            snap=copy.deepcopy(self.auth.plan('A')['snapshot']);snap['candidate_key']='B';snap['targets'][self.key]['quality']=[2]
            self.auth.prepare('B','round',snap,now=tp.NOW)
            self.auth.supersede('B',self.auth.vector([self.key]),reason='QUALITY_UPGRADE',safe_isolation=True,now=tp.NOW+timedelta(seconds=1))
            return original(*args)
        self.cloud.results=['HIT']
        with patch.object(self.cloud,'rapid',side_effect=stale):self.worker.reconcile(bid,now=tp.NOW)
        self.assertEqual('B',self.auth.vector([self.key])[self.key]['owner_plan_id'])
        self.assertEqual('VERIFIED',self.worker.bundle(bid)['files'][0]['state'])
        with self.assertRaises(ValueError):self.worker.publish(bid,now=tp.NOW+timedelta(minutes=1))
        self.assertFalse(any(c[0]=='move' for c in self.cloud.calls))

    def test_review_i1_cancelled_rapid_unknown_reconciles_only_original_result(self):
        bid=self.prepared();self.cloud.results=[TimeoutError()];self.worker.reconcile(bid,now=tp.NOW)
        b=self.worker.bundle(bid);f=b['files'][0];path=b['staging']+'/'+f['relative_path']
        self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'},now=tp.NOW)
        self.assertEqual('CANCEL_PENDING',self.worker.bundle(bid)['state'])
        self.cloud.objects[path]=dict(path=path,id='old-object',sha1=f['sha1'],size=f['size'],account_ref='own')
        before=[c for c in self.cloud.calls if c[0] in ('rapid','start','move')]
        self.assertEqual('ABANDONED',self.worker.reconcile(bid,now=tp.NOW)['state'])
        self.worker.reconcile(bid,now=tp.NOW)
        self.assertEqual(before,[c for c in self.cloud.calls if c[0] in ('rapid','start','move')])
        with self.repo.connection() as db:
            rows=db.execute("SELECT evidence FROM action_receipts WHERE action_id=? AND outcome='SUCCEEDED'",(f['action_id'],)).fetchall()
        self.assertEqual(1,len(rows));self.assertEqual('old-object',json.loads(rows[0][0])['remote']['id'])

    def test_review_i1_superseded_rapid_keeps_successor_authority(self):
        bid=self.prepared();self.cloud.results=[TimeoutError()];self.worker.reconcile(bid,now=tp.NOW)
        snap=copy.deepcopy(self.auth.plan('A')['snapshot']);snap['candidate_key']='B';snap['targets'][self.key]['quality']=[2]
        self.auth.prepare('B','round',snap,now=tp.NOW);self.auth.supersede('B',self.auth.vector([self.key]),reason='QUALITY_UPGRADE',safe_isolation=True,now=tp.NOW)
        b=self.worker.bundle(bid);f=b['files'][0];path=b['staging']+'/'+f['relative_path']
        self.cloud.objects[path]=dict(path=path,id='old-object',sha1=f['sha1'],size=f['size'],account_ref='own')
        self.assertEqual('ABANDONED',self.worker.reconcile(bid,now=tp.NOW)['state'])
        self.assertEqual('B',self.auth.vector([self.key])[self.key]['owner_plan_id'])

    def test_review_i1_absent_conflicting_or_unreadable_rapid_stays_unknown(self):
        bid=self.prepared();self.cloud.results=[TimeoutError()];self.worker.reconcile(bid,now=tp.NOW)
        b=self.worker.bundle(bid);f=b['files'][0];path=b['staging']+'/'+f['relative_path']
        self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'},now=tp.NOW)
        for remote in (None,dict(path=path,id='wrong',sha1='f'*40,size=f['size'])):
            if remote:self.cloud.objects[path]=remote
            self.assertEqual('CANCEL_PENDING',self.worker.reconcile(bid,now=tp.NOW)['state'])
        with patch.object(self.cloud,'stat',side_effect=TimeoutError()):
            self.assertEqual('CANCEL_PENDING',self.worker.reconcile(bid,now=tp.NOW)['state'])
        self.assertEqual('UNKNOWN',self.worker.bundle(bid)['files'][0]['state'])

    def test_receipt_and_bundle_miss_are_one_transaction(self):
        bid=self.prepared();original=self.worker._save
        def fail(b,db=None):
            if b['files'][0]['misses']:raise RuntimeError('disk unavailable')
            return original(b,db)
        with patch.object(self.worker,'_save',side_effect=fail):
            with self.assertRaises(RuntimeError):self.worker.reconcile(bid,now=tp.NOW)
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute("SELECT count(*) FROM action_receipts WHERE outcome='FAILED'").fetchone()[0])

    def test_cancel_persists_exclusion_before_reader_rpc_and_never_assumes_empty_ack(self):
        self.rule.update(fallback=True,fallback_gb='0.001',rapid_misses=1)
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.reconcile(bid,now=tp.NOW)
        self.worker.reconcile(bid,now=tp.NOW+timedelta(seconds=61))
        original=self.cloud.pump
        def check(*a,**kw):
            self.assertTrue(self.worker.bundle(bid)['cancel_intent'])
            self.assertTrue(self.worker.exclusions.matches({'candidate_key':'candidate','targets':[self.key]}))
            return original(*a,**kw)
        with patch.object(self.cloud,'pump',side_effect=check):
            result=self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'},now=tp.NOW)
        self.assertEqual('CANCEL_PENDING',result['state'])
        self.assertTrue((self.local/'movie.mkv').exists())
        self.cloud.running=False;self.worker.reconcile(bid,now=tp.NOW)
        self.assertEqual('ABANDONED',self.worker.bundle(bid)['state'])

    def test_cleanup_permissions_are_independent_and_no_age_cleanup(self):
        bid=self.prepared()
        self.assertEqual('CLEANUP_NOT_ELIGIBLE',self.worker.cleanup(bid)['reason'])
        self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'})
        self.assertEqual('CLEANUP_PERMISSION_DISABLED',self.worker.cleanup(bid)['reason'])
        self.assertTrue((self.local/'movie.mkv').exists())

    def test_review_i2_consumed_entry_reaches_validator_without_premature_handoff(self):
        from unittest.mock import Mock
        bid=self.all_remote();original=self.cloud.move
        def consumed(*args):
            original(*args)
            self.cloud.objects.clear()
        with patch.object(self.cloud,'move',side_effect=consumed):
            self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        b=self.worker.bundle(bid);aid=b['publication_action']
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',b['state'])
        receipt=dict(action_id=aid,settled=True,evidence_ref='actual-final')
        def verify(action,manifest,consumer,**kw):
            self.assertEqual(aid,action);self.assertEqual(b['manifest'],manifest)
            self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.auth.action(aid)['state'])
            raise ValueError('FINAL_PROOF_REJECTED')
        self.worker.archive=Mock();self.worker.archive.confirm_ingest.side_effect=verify
        with self.assertRaisesRegex(ValueError,'FINAL_PROOF_REJECTED'):
            self.worker.confirm(bid,receipt)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.worker.bundle(bid)['state'])
        self.worker.reconcile(bid,now=tp.NOW+timedelta(minutes=6))
        self.assertEqual(1,sum(c[0]=='move' for c in self.cloud.calls))

    def test_review_i2_confirmed_receipt_recovers_bundle_after_commit_restart(self):
        from unittest.mock import Mock
        bid=self.all_remote();self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        aid=self.worker.bundle(bid)['publication_action']
        # Archive's idempotent result is based on its durable verified ingest.
        self.worker.archive=Mock()
        self.worker.archive.confirm_ingest.return_value=dict(accepted=False,duplicate=True,reason='ALREADY_CONFIRMED')
        result=self.worker.confirm(bid,dict(action_id=aid,settled=True,evidence_ref='same-final'))
        self.assertEqual('CONFIRMED',result['state'])

    def test_consumer_unsettled_does_not_append_handoff_or_confirm(self):
        bid=self.all_remote();self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        from unittest.mock import Mock
        self.worker.archive=Mock()
        result=self.worker.confirm(bid,{'settled':False})
        self.assertEqual('WAIT_CONSUMER',result['state']);self.worker.archive.confirm_ingest.assert_not_called()

    def test_full_scan_finds_deep_addition_and_does_not_delete_on_partial(self):
        scan=self.m.LocalReconciler(self.repo,[self.rule]);deep=self.local/'deep';deep.mkdir();(deep/'a.srt').write_text('a')
        for _ in range(20):
            report=scan.scan('r',limits={'entries':1},force=True,now=tp.NOW)
            if report['state']=='COMPLETE':break
        self.assertEqual('COMPLETE',report['state'])
        old=self.local.stat();(deep/'late.srt').write_text('late');os.utime(self.local,ns=(old.st_atime_ns,old.st_mtime_ns))
        report=scan.scan('r',limits={'entries':1},force=True,now=tp.NOW+timedelta(seconds=61))
        self.assertEqual('INCOMPLETE',report['state'])
        with self.repo.connection() as db:self.assertTrue(all(json.loads(r[0])['state']=='PRESENT' for r in db.execute('SELECT data FROM local_observations')))
        for _ in range(30):
            report=scan.scan('r',limits={'entries':1},force=True,now=tp.NOW+timedelta(seconds=61))
            if report['state']=='COMPLETE':break
        with self.repo.connection() as db:self.assertIsNotNone(db.execute("SELECT 1 FROM local_observations WHERE path LIKE '%late.srt'").fetchone())

    def test_review_i4_replaced_empty_root_preserves_observations_and_recovers(self):
        scan=self.m.LocalReconciler(self.repo,[self.rule])
        self.assertEqual('COMPLETE',scan.scan('r',force=True)['state'])
        original=self.root/'original';self.local.rename(original)
        self.assertEqual('INCOMPLETE',scan.scan('r',force=True)['state'])
        self.local.mkdir();changed=dict(self.rule,rapid_interval=61)
        scan=self.m.LocalReconciler(self.repo,[changed])
        for _ in range(2):
            self.assertEqual('SOURCE_UNVERIFIED',scan.scan('r',force=True)['state'])
            with self.repo.connection() as db:
                self.assertTrue(all(json.loads(r[0])['state']=='PRESENT' for r in db.execute('SELECT data FROM local_observations')))
        self.local.rmdir();original.rename(self.local);(self.local/'movie.zh.srt').unlink()
        self.assertEqual('COMPLETE',scan.scan('r',force=True)['state'])
        with self.repo.connection() as db:
            values={Path(r[0]).name:json.loads(r[1])['state'] for r in db.execute('SELECT path,data FROM local_observations')}
        self.assertEqual({'movie.mkv':'PRESENT','movie.zh.srt':'MISSING'},values)

    def test_review_i4_legacy_checkpoint_wrong_source_retries_then_recovers(self):
        scan=self.m.LocalReconciler(self.repo,[self.rule])
        original_source=dict(root=self.m.identity(self.local.lstat())[:3],mount='original')
        with patch.object(self.m,'local_source_identity',return_value=original_source):
            self.assertEqual('COMPLETE',scan.scan('r',force=True)['state'])
        # Legacy schema-7 scan stored root_identity, before mount proof existed.
        with self.repo.connection(write=True) as db:
            old=json.loads(db.execute("SELECT data FROM reconcile_checkpoints WHERE scope='local:r'").fetchone()[0])
            del old['source_identity']
            db.execute("UPDATE reconcile_checkpoints SET data=? WHERE scope='local:r'",(json.dumps(old),))
            observations=[tuple(r) for r in db.execute('SELECT * FROM local_observations ORDER BY path')]
        original=self.root/'original';self.local.rename(original);self.local.mkdir()
        wrong_source=dict(root=self.m.identity(self.local.lstat())[:3],mount='wrong-underlying')
        checkpoints=[]
        for interval in (61,62):
            scan=self.m.LocalReconciler(self.repo,[dict(self.rule,rapid_interval=interval)])
            with patch.object(self.m,'local_source_identity',side_effect=OSError('unavailable')):
                self.assertEqual('INCOMPLETE',scan.scan('r',force=True)['state'])
            with patch.object(self.m,'local_source_identity',return_value=wrong_source):
                checkpoints.append(scan.scan('r',force=True))
            self.assertEqual('SOURCE_UNVERIFIED',checkpoints[-1]['state'])
            with self.repo.connection() as db:
                self.assertEqual(observations,[tuple(r) for r in db.execute('SELECT * FROM local_observations ORDER BY path')])
        self.local.rmdir();original.rename(self.local);(self.local/'movie.zh.srt').unlink()
        with patch.object(self.m,'local_source_identity',return_value=original_source):
            restored=scan.scan('r',force=True)
        self.assertEqual('COMPLETE',restored['state'])
        self.assertEqual(original_source,restored['source_identity'])
        for checkpoint in checkpoints:
            self.assertNotIn('source_identity',checkpoint)
            self.assertEqual(old['root_identity'],checkpoint['root_identity'])
        with self.repo.connection() as db:
            values={Path(r[0]).name:json.loads(r[1])['state'] for r in db.execute('SELECT path,data FROM local_observations')}
        self.assertEqual({'movie.mkv':'PRESENT','movie.zh.srt':'MISSING'},values)

    def test_review_i4_mount_identity_change_preserves_confirmed_source(self):
        scan=self.m.LocalReconciler(self.repo,[self.rule])
        with patch.object(self.m,'local_source_identity',create=True,return_value={'root':[1,2,3],'mount':'original'}):
            self.assertEqual('COMPLETE',scan.scan('r',force=True)['state'])
        with patch.object(self.m,'local_source_identity',create=True,return_value={'root':[1,2,3],'mount':'underlying'}):
            self.assertEqual('SOURCE_UNVERIFIED',scan.scan('r',force=True)['state'])
        with patch.object(self.m,'local_source_identity',return_value={'root':[1,2,3],'mount':'original'}):
            self.assertEqual('COMPLETE',scan.scan('r',force=True)['state'])

    def test_reauthorized_plan_adopts_exact_old_receipt_without_copying_rows(self):
        snap=copy.deepcopy(self.auth.plan('A')['snapshot']);self.auth.prepare('B','round',snap,now=tp.NOW)
        self.auth.cancel('A',self.auth.vector([self.key]),reason='EXPLICIT_REAUTHORIZE');self.auth.claim('B',self.auth.vector([self.key]),now=tp.NOW)
        result=self.worker.prepare('B','r',source_plan_id='A',publication=self.publication,now=tp.NOW)
        self.assertEqual('A',self.worker.bundle(result['bundle_id'])['source_plan_id'])
        with self.repo.connection() as db:self.assertEqual(0,db.execute("SELECT count(*) FROM organized_assets WHERE plan_id='B'").fetchone()[0])

    def test_missing_organizer_success_receipt_blocks_adoption(self):
        with self.repo.connection(write=True) as db:db.execute('DELETE FROM action_receipts')
        with self.assertRaisesRegex(ValueError,'ORGANIZE_RECEIPT_REQUIRED'):self.prepared()

    def test_review_i3_host_prepare_validates_explicit_claim_then_bound_claim(self):
        from types import SimpleNamespace as NS
        host=tp.load('host_delivery_contract');seen=[]
        def gate(archive,publication):
            def validate(plan):
                seen.append(copy.deepcopy(publication))
                if publication!=self.publication:raise ValueError('BAD_PUBLICATION')
                return publication
            return validate
        config=dict(cloud_scopes={},libraries={},policy_bindings={},classification_revision=1,mappings=[],rules=[self.rule])
        with patch.object(host,'HostArchiveSources'),patch.object(host,'Policy'),patch.object(host,'Archive'),patch.object(host,'HostDeliveryCloud',return_value=self.cloud),patch.object(host,'PublicationGate',side_effect=gate):
            worker=host.build_delivery(NS(repository=self.repo),config)
            with self.assertRaisesRegex(ValueError,'BAD_PUBLICATION'):
                worker.prepare('A','r',publication={self.key:{'raw':{},'classification':{}}},now=tp.NOW)
            with self.repo.connection() as db:self.assertEqual(0,db.execute('SELECT count(*) FROM delivery_bundles').fetchone()[0])
            bid=worker.prepare('A','r',publication=self.publication,now=tp.NOW)['bundle_id']
            config['publication']={'unrelated':'global claim'}
            worker.reconcile(bid,now=tp.NOW)
        self.assertEqual(self.publication,seen[-1])
        self.assertGreaterEqual(len(seen),3)

    def test_actual_publication_gate_rejects_stale_or_better_current(self):
        from types import SimpleNamespace as NS
        policy=tp.load('policy').Policy({'movie':'动画电影'},1)
        classification={'state':'complete','policy_revision':1,'effective':{'category_id':'movie','category_path':[],'rule_id':'r','source':'automatic'}}
        raw={'title':'Example.2008.1080p.WEB-DL.H264.AC3','description':'','labels':[],'subtitle_description':'外挂简体中文字幕'}
        publication={self.key:{'raw':raw,'classification':classification}}
        baseline={self.key:{'state':'UNKNOWN','revision':0,'versions':[]}}
        archive=NS(policy=policy,sources=NS(classify_target=lambda key:classification),current=lambda keys:baseline)
        gate=self.m.PublicationGate(archive,publication)
        plan=copy.deepcopy(self.auth.plan('A'));plan['snapshot']['policy_revision']=policy.semantic_hash
        plan['snapshot']['targets'][self.key]['quality']=list(policy.admit(policy.normalize(raw),classification,identity_ok=True,scope_ok=True).rank)
        with self.assertRaisesRegex(ValueError,'CURRENT_UNVERIFIED'):gate(plan)
        baseline[self.key]['state']='MISSING';self.assertEqual(publication,gate(plan))
        p=tp.load('policy');baseline[self.key].update(state='PRESENT',versions=[p.Version('better',policy.normalize(dict(raw,title='Example.2008.2160p.WEB-DL.H264.AC3'),current=True))])
        with self.assertRaisesRegex(ValueError,'CURRENT_BETTER'):gate(plan)

    def test_staging_permission_cannot_enable_monitor_or_downloader_cleanup(self):
        self.rule['cleanup_staging']=True
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.all_remote();self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'})
        self.cloud.delete_file=lambda scope,path,expected:self.cloud.objects.pop(path)
        self.assertTrue(self.worker.cleanup(bid,scope='staging')['staging_cleaned'])
        self.assertTrue((self.local/'movie.mkv').exists())
        self.assertEqual('CLEANUP_PERMISSION_DISABLED',self.worker.cleanup(bid,scope='downloader_task')['reason'])

    def test_partial_bundle_never_removes_whole_downloader_task(self):
        self.rule['remove_downloader_task_enabled']=True
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.prepared();self.worker.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'})
        b=self.worker.bundle(bid);b['indices']=[1];self.worker._save(b)
        result=self.worker.cleanup(bid,scope='downloader_task')
        self.assertEqual('SHARED_DOWNLOAD_SCOPE',result['reason'])

    def test_downloader_cleanup_durable_guard_blocks_new_send(self):
        self.rule['remove_downloader_task_enabled']=True
        self.worker=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[self.rule],revalidate=lambda p:self.publication)
        bid=self.all_remote();self.worker.publish(bid,now=tp.NOW+timedelta(minutes=5))
        s=self.auth.plan('A')['snapshot']
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO managed_downloads VALUES(?,?,?,?,?,?,?,?,?,?)',(s['downloader'],s['infohash'],s['save_path'],'[]','owned','add','client','RUNNING','{}',self.s.stamp(tp.NOW)))
        from unittest.mock import Mock
        client=Mock();task={'id':'client','save_path':s['save_path'],'markers':['owned']}
        client.task.return_value=task;client.files.return_value=[{'id':f['index'],'path':f['path'],'size':f['size']} for f in self.files]
        def remove(tid):
            with self.repo.connection() as db:
                self.assertEqual('DELIVERY_CLEANUP',db.execute('SELECT state FROM managed_downloads').fetchone()[0])
                with self.assertRaisesRegex(ValueError,'DOWNLOAD_CLEANUP_UNSETTLED'):self.auth._download_not_cleaning(db,s)
            client.task.return_value=None
        client.remove.side_effect=remove
        with patch.object(tp.load('execution').ConfiguredDownloader,'named',return_value=client):
            result=self.worker.cleanup(bid,scope='downloader_task')
        self.assertTrue(result['downloader_removed']);self.assertTrue((self.local/'movie.mkv').exists())

    def test_permission_change_does_not_recreate_transfer_or_rehash(self):
        bid=self.prepared();updated=dict(self.rule,cleanup_abandoned=True)
        other=self.m.Delivery(self.repo,self.auth,None,self.cloud,rules=[updated],revalidate=lambda p:self.publication)
        other.cancel(bid,reason='USER_ABANDON',exclusion_id='deny',criteria={'candidate_key':'candidate'})
        self.assertEqual('ABANDONED',other.bundle(bid)['state'])

    def test_schema6_migration_preserves_every_old_row(self):
        with self.repo.connection(write=True) as db:
            for table in ('ai_usage','ai_requests','ai_runtime','delivery_bundles','reconcile_checkpoints','local_observations'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=6')
            names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            before={n:sorted([tuple(r) for r in db.execute('SELECT * FROM "'+n+'"')],key=repr) for n in names}
        self.r.Repository(self.repo.path)
        with self.repo.connection() as db:
            self.assertEqual(8,db.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(before,{n:sorted([tuple(r) for r in db.execute('SELECT * FROM "'+n+'"')],key=repr) for n in names})

    def test_tick_with_no_events_scans_and_advances_only_durable_bundle(self):
        bid=self.prepared();self.cloud.results=['HIT']
        result=self.worker.tick(now=tp.NOW,limits={'bundles':1,'scan_entries':10,'seconds':1})
        self.assertEqual(1,len(result['bundles']))
        self.assertEqual('VERIFIED',self.worker.bundle(bid)['files'][0]['state'])
        with self.repo.connection() as db:self.assertEqual(2,db.execute('SELECT count(*) FROM local_observations').fetchone()[0])

    @unittest.skipUnless(os.name=='posix','Linux dirfd/nofollow acceptance is a separate host gate')
    def test_safe_unlink_hardlink_and_symlink_preserve_targets_and_parent_swap(self):
        target=self.root/'actual';target.write_text('keep');link=self.local/'link';link.symlink_to(target)
        snap={'path':str(link),'entry':self.m.identity(link.lstat())};parents=self.m.parent_snapshot(link)
        self.m.safe_unlink(snap,parents);self.assertTrue(target.exists());self.assertFalse(link.exists())
        hard=self.local/'hard';os.link(target,hard);snap={'path':str(hard),'entry':self.m.identity(hard.lstat())}
        self.m.safe_unlink(snap,self.m.parent_snapshot(hard));self.assertEqual('keep',target.read_text())
        inner=self.local/'inner';inner.mkdir();file=inner/'x';file.write_text('keep');snap={'path':str(file),'entry':self.m.identity(file.lstat())};parents=self.m.parent_snapshot(file)
        inner.rename(self.local/'old');inner.symlink_to(self.local/'old',target_is_directory=True)
        with self.assertRaises((OSError,ValueError)):self.m.safe_unlink(snap,parents)
        self.assertTrue((self.local/'old'/'x').exists())


if __name__=='__main__': unittest.main()
