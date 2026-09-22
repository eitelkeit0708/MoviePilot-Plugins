"""W05 deterministic fake-client boundaries. Real SDK harness is separate."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from test_planner import load, PLUGIN, NOW


class Client:
    def __init__(self):
        self.exists = False
        self.calls = []
        self.state = 'PAUSED'
        self.wanted = {9, 3, 7}
        self.bad_readback = False
        self.lost_add = False
        self.stats = {9: 0, 3: 0, 7: 0}

    def task(self, infohash):
        return dict(id=infohash, infohash=infohash, save_path='/test', state=self.state) if self.exists else None

    def files(self, infohash):
        return [dict(id=i, path=p, size=n, wanted=(True if self.bad_readback else i in self.wanted), completed=self.stats[i])
                for i, p, n in [(7, 'E02.srt', 10), (9, 'E01.mkv', 100), (3, 'E02.mkv', 200)]]

    def add(self, content, infohash, save_path, marker):
        self.calls.append('add')
        self.exists = True
        if self.lost_add:
            raise TimeoutError('secret')
        return infohash

    def pause(self, task_id):
        self.calls.append('pause'); self.state = 'PAUSED'; return True

    def select_files(self, task_id, indices, wanted):
        self.calls.append('select')
        self.wanted = self.wanted | set(indices) if wanted else self.wanted-set(indices)
        return True

    def resume(self, task_id):
        self.calls.append('resume'); self.state = 'DOWNLOADING'; return True


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PLUGIN / 'execution.py').exists(), 'W05 execution missing')
        self.e, self.r, self.p, self.s = [load(x) for x in ('execution', 'repository', 'planner', 'scheduler')]
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = self.r.Repository(Path(self.tmp.name) / 'state.db')
        task = self.repo.submit('intent', self.r.Target('电视剧', 'tmdb', '42', 1), {}, 'admin', 42, True)
        self.repo.complete_handoff(task['id'], task['generation'])
        target = self.r.Target.from_task(task)
        self.keys = [self.p.TargetUnit(target, i).key for i in (1, 2)]
        self.s.Scheduler(self.repo).open_opportunity('round', task['id'], [self.p.TargetUnit(target, i) for i in (1,2)], mode='ONESHOT', config=self.s.ScheduleConfig(observation_enabled=False), now=NOW)
        self.auth = self.p.Authority(self.repo); self.auth.set_revisions('p', 'm')
        self.files = [dict(index=0,path='E01.mkv',size=100,role='video',targets=[self.keys[0]],requires=[]), dict(index=1,path='E02.mkv',size=200,role='video',targets=[self.keys[1]],requires=[]), dict(index=2,path='E02.srt',size=10,role='subtitle',targets=[self.keys[1]],requires=[])]
        self.spec = dict(candidate_key='site:1:42',infohash='a'*40,downloader='test',save_path='/test',policy_revision='p',parse_revision='m',current={self.keys[1]:dict(state='MISSING',revision=0)},targets={self.keys[1]:dict(action='ACQUIRE',reason='MISSING',evidence_keys=[],quality=[1],evidence_source='none')},torrent_files=self.files,selected_indices=[1,2],verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
        self.auth.prepare('plan', 'round', self.spec, now=NOW)
        self.auth.claim('plan', self.auth.vector([self.keys[1]]), now=NOW)
        self.client = Client()
        self.executor = self.e.StrictExecutor(self.repo, lambda name: self.client, revalidate=lambda plan:None, verify_torrent=lambda content: ('a'*40, [(f['path'],f['size']) for f in self.files]))

    def test_shuffled_indices_exact_paused_readback_then_resume(self):
        result = self.executor.execute('plan', b'torrent')
        self.assertEqual('PAUSED_VERIFIED', result['state'])
        self.assertEqual({3,7}, self.client.wanted)
        self.assertNotIn('resume', self.client.calls)
        self.assertEqual('RUNNING', self.executor.resume('plan')['state'])
        self.assertEqual(1, self.client.calls.count('add'))

    def test_ordinary_reentry_preserves_external_pause_and_nonrunning_states(self):
        self.assertEqual('PAUSED_VERIFIED', self.executor.execute('plan', b'torrent')['state'])
        self.assertEqual('RUNNING', self.executor.resume('plan')['state'])
        before_owned = self.executor._owned(self.spec)
        before_vector = self.auth.vector([self.keys[1]])
        for status in ('PAUSED', 'CHECKING', 'LIMITED', 'DISCONNECTED'):
            with self.subTest(status=status):
                self.client.state = status
                calls = list(self.client.calls)
                for resume in (True, False):
                    result = self.executor.execute('plan', b'torrent', resume=resume)
                    self.assertEqual(('WAITING_ASSETS', 'DOWNLOADER_' + status), (result['state'], result['reason']))
                    self.assertEqual(calls, self.client.calls)
                    self.assertEqual(before_owned, self.executor._owned(self.spec))
                    self.assertEqual(before_vector, self.auth.vector([self.keys[1]]))
        self.client.state = 'PAUSED'
        self.client.wanted = {3}
        calls = list(self.client.calls)
        self.assertEqual('WAITING_ASSETS', self.executor.execute('plan', b'torrent', resume=True)['state'])
        self.assertEqual(calls, self.client.calls)
        self.assertEqual(before_owned, self.executor._owned(self.spec))
        self.client.wanted = {3, 7}
        self.client.state = 'PAUSED'
        self.assertEqual('RUNNING', self.executor.resume('plan')['state'])
        self.assertEqual('DOWNLOADING', self.client.state)

    def test_entire_readback_mismatch_never_resumes(self):
        self.client.bad_readback = True
        result = self.executor.execute('plan', b'torrent')
        self.assertEqual('BLOCKED', result['state'])
        self.assertNotIn('resume', self.client.calls)
        self.assertEqual('PAUSED', self.client.state)

    def test_unmanaged_same_hash_is_never_touched(self):
        self.client.exists = True
        self.assertEqual('BLOCKED', self.executor.execute('plan', b'torrent')['state'])
        self.assertEqual([], self.client.calls)

    def test_lost_add_is_durable_unknown_without_automatic_adoption(self):
        self.client.lost_add = True
        self.assertEqual('UNKNOWN', self.executor.execute('plan', b'torrent')['state'])
        restarted = self.e.StrictExecutor(self.repo, lambda name: self.client, revalidate=self.executor.revalidate, verify_torrent=self.executor.verify_torrent)
        self.assertEqual('UNKNOWN', restarted.execute('plan', b'torrent')['state'])
        self.assertEqual(['add'], self.client.calls)

    def test_exclusion_added_after_prepare_blocks_resume(self):
        self.executor.execute('plan', b'torrent')
        self.e.Exclusions(self.repo).add('exclude', {'candidate_key':'site:1:42'}, reason='manual')
        self.assertEqual('BLOCKED', self.executor.resume('plan')['state'])
        self.assertNotIn('resume', self.client.calls)

    def test_selected_bytes_and_stale_callback_do_not_reactivate(self):
        self.executor.execute('plan', b'torrent')
        self.client.stats.update({9:100, 3:100, 7:10})
        sample = self.executor.sample('plan')
        self.assertEqual(110, sample['downloaded_bytes'])
        self.assertEqual(210, sample['total_bytes'])
        self.auth.cancel('plan',self.auth.vector([self.keys[1]]),reason='cancel')
        self.client.stats[3] = 200
        from unittest.mock import patch
        before=self.auth.progress('plan',[1,2]);calls=list(self.client.calls)
        with patch.object(self.client,'task',wraps=self.client.task) as task,patch.object(self.client,'files',wraps=self.client.files) as files:
            with self.assertRaisesRegex(ValueError,'NO_ACTIVE_SAFE_FILES'):
                self.executor.sample('plan')
            task.assert_not_called();files.assert_not_called()
        self.assertEqual(before,self.auth.progress('plan',[1,2]))
        self.assertEqual(calls,self.client.calls)
        self.assertEqual('RECONCILED',self.executor.reconcile('plan')['state'])
        self.assertEqual('CANCELLED',self.auth.plan('plan')['authorization'])

    def test_host_qb_adapter_paused_add_and_whole_files(self):
        from types import SimpleNamespace
        self.assertTrue(hasattr(self.e,'ConfiguredDownloader'),'configured downloader missing')
        calls=[]
        class Qb:
            def add_torrent(self,**kwargs):
                calls.append(kwargs);return True,['a'*40]
            def get_torrents(self,ids):
                return [dict(hash='a'*40,save_path='/test',state='stoppedDL')],False
            def get_files(self,tid,retry=1,interval=0):
                return [dict(index=4,name='Pack/E02.mkv',size=200,priority=1,progress=.5),dict(index=1,name='Pack/E01.mkv',size=100,priority=0,progress=1)]
        client=self.e.ConfiguredDownloader(SimpleNamespace(type='qbittorrent',instance=Qb()))
        self.assertEqual('a'*40,client.add(b'torrent','a'*40,'/test','owned'))
        self.assertTrue(calls[0]['is_paused'])
        self.assertFalse(calls[0]['ignore_category_check'])
        self.assertEqual('PAUSED',client.task('a'*40)['state'])
        self.assertEqual([100,100],[r['completed'] for r in client.files('a'*40)])
        self.assertEqual([True,False],[r['wanted'] for r in client.files('a'*40)])

    def test_host_tr_requires_actual_selected_and_completion(self):
        from types import SimpleNamespace
        self.assertTrue(hasattr(self.e,'ConfiguredDownloader'),'configured downloader missing')
        class TR:
            def get_files(self,tid):
                return [SimpleNamespace(id=4,name='E02.mkv',size=200,completed=12,selected=True)]
        client=self.e.ConfiguredDownloader(SimpleNamespace(type='transmission',instance=TR()))
        self.assertEqual(True,client.files('a'*40)[0]['wanted'])
        self.assertEqual(12,client.files('a'*40)[0]['completed'])
        client.instance.get_files=lambda tid:[SimpleNamespace(id=4,name='E02.mkv',size=200,progress=.5)]
        with self.assertRaises(ValueError):
            client.files('a'*40)

    def test_host_tr_namedtuple_file_ids_and_integer_rpc_task_ids(self):
        from typing import NamedTuple
        from types import SimpleNamespace
        class File(NamedTuple):
            id:int
            name:str
            size:int
            selected:bool
            completed:int
        calls=[]
        class TR:
            def get_files(self,tid):
                return [File(7,'E02.srt',10,True,10),File(3,'E02.mkv',200,False,0)]
            def stop_torrents(self,ids):calls.append(('pause',ids));return True
            def start_torrents(self,ids):calls.append(('resume',ids));return True
            def set_files(self,tid,indices):calls.append(('wanted',tid,indices));return True
            def set_unwanted_files(self,tid,indices):calls.append(('unwanted',tid,indices));return True
        client=self.e.ConfiguredDownloader(SimpleNamespace(type='transmission',instance=TR()))
        self.assertEqual([7,3],[f['id'] for f in client.files('a'*40)])
        client.pause('2');client.resume('2');client.select_files('2',[7],True);client.select_files('2',[3],False)
        self.assertEqual([('pause',2),('resume',2),('wanted',2,[7]),('unwanted',2,[3])],calls)
        client.pause('1'*40);self.assertEqual(('pause','1'*40),calls[-1])

    def test_organize_only_completed_authorized_files_with_readback(self):
        self.assertTrue(hasattr(self.e,'Organizer'),'W05 organizer missing')
        import shutil
        root=Path(self.tmp.name)
        source=root/'source';source.mkdir()
        target=root/'organized';target.mkdir()
        for f in self.files:
            (source/f['path']).write_bytes(b'x'*f['size'])
        calls=[]
        class MP:
            def history(self,snapshot,paths):
                calls.append(('history',sorted(paths)))
                return True
            def transfer(self,src,dest,item,snapshot):
                calls.append(('transfer',src.name))
                output=dest/src.name;shutil.copyfile(src,output)
                return output
        self.executor.execute('plan',b'torrent')
        self.client.stats.update({3:200,7:10})
        org=self.e.Organizer(self.executor,MP(),source_root=lambda s:source)
        result=org.organize('plan',target)
        self.assertEqual('COMPLETE',result['state'])
        self.assertEqual(['E02.mkv','E02.srt'],sorted(p.name for p in target.iterdir()))
        before=list(calls)
        self.assertEqual('COMPLETE',org.organize('plan',target)['state'])
        self.assertEqual(before,calls)

    def test_organize_adopts_existing_exact_native_transfer_only_with_receipt(self):
        import shutil
        root=Path(self.tmp.name);source=root/'source';source.mkdir();target=root/'organized';target.mkdir()
        for item in self.files:
            path=source/item['path'];path.write_bytes(b'x'*item['size'])
            if item['index'] in (1,2):shutil.copyfile(path,target/path.name)
        self.executor.execute('plan',b'torrent');self.client.stats.update({3:200,7:10})
        class Host:
            receipt=False
            def history(self,*args):return True
            def prepare_transfer(self,src,dest,item,snapshot):
                return dict(planned=dest/src.name,naming_revision='test')
            def transfer_receipt(self,src,dest,snapshot):
                assert Path(src).name==Path(dest).name
                return self.receipt
            def transfer_prepared(self,prepared):
                raise AssertionError('must not copy a proven existing target')
        host=Host();organizer=self.e.Organizer(self.executor,host,source_root=lambda _:source)
        self.assertEqual('DESTINATION_ALREADY_EXISTS',organizer.organize('plan',target)['reason'])
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute("SELECT count(*) FROM plan_actions WHERE kind='ORGANIZE' AND json_extract(payload,'$.verb') LIKE 'organize:%'").fetchone()[0])
        host.receipt=True
        (target/'E02.mkv').write_bytes(b'y'*200)
        self.assertEqual('DESTINATION_ALREADY_EXISTS',organizer.organize('plan',target)['reason'])
        shutil.copyfile(source/'E02.mkv',target/'E02.mkv')
        self.assertEqual('COMPLETE',organizer.organize('plan',target)['state'])
        with self.repo.connection() as db:
            assets=[(r['file_index'],r['state']) for r in db.execute("SELECT file_index,state FROM organized_assets WHERE plan_id='plan' ORDER BY file_index")]
        self.assertEqual([(1,'COMPLETE'),(2,'COMPLETE')],assets)
        self.assertEqual('COMPLETE',organizer.organize('plan',target)['state'])

    def test_completed_source_hash_checkpoint_recovers_expired_tick_without_skipping_guards(self):
        import shutil
        from types import SimpleNamespace
        from unittest.mock import patch
        delivery=load('delivery')
        root=Path(self.tmp.name);source=root/'source';source.mkdir();target=root/'organized';target.mkdir()
        for item in self.files:(source/item['path']).write_bytes(b'x'*item['size'])
        calls=[];clock=[0];deadline=[30];hashes=[]
        class Host:
            def history(self,*args):calls.append('history');return True
            def transfer(self,src,dest,*args):
                calls.append('transfer');output=dest/src.name;shutil.copyfile(src,output);return output
        self.executor.execute('plan',b'torrent');self.client.stats.update({3:200,7:10})
        def gate():
            if clock[0]>=deadline[0]:raise ValueError('TICK_DEADLINE')
        self.executor.dispatch_gate=gate
        org=self.e.Organizer(self.executor,Host(),source_root=lambda _:source)
        old_hash=self.e.asset_hashes;source_hash=delivery.LocalSource.hash
        def slow_old(path):
            result=old_hash(path);hashes.append(str(path));clock[0]+=31;return result
        def slow_source(src):
            result=source_hash(src);hashes.append(str(src.path));clock[0]+=31;return result
        # Exercise the POSIX cache policy on all runners; LocalSource itself is
        # real. The separate POSIX test checks actual ctime invalidation.
        with patch.object(self.e,'os',SimpleNamespace(name='posix'),create=True),patch.object(self.e,'asset_hashes',side_effect=slow_old),patch.object(delivery.LocalSource,'hash',slow_source):
            first=org.organize('plan',target)
            self.assertEqual('TICK_DEADLINE',first['reason']);self.assertEqual([],calls)
            before=list(hashes);deadline[0]=clock[0]+30
            # A fresh instance represents the next normal tick/restart.
            self.executor.revalidate=lambda _:(_ for _ in ()).throw(ValueError('POLICY_PARSE_CHANGED'))
            self.assertEqual('POLICY_PARSE_CHANGED',self.e.Organizer(self.executor,Host(),source_root=lambda _:source).organize('plan',target)['reason'])
            self.assertEqual([],calls)
            self.executor.revalidate=lambda _:None
            second=self.e.Organizer(self.executor,Host(),source_root=lambda _:source).organize('plan',target)
            self.assertEqual('COMPLETE',second['state'])
            self.assertEqual(before,hashes,'completed stable hashes must survive the expired tick')
            self.assertEqual(['history','transfer','transfer'],calls)

    def test_source_hash_checkpoint_rejects_changes_and_nonposix_never_reuses(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        delivery=load('delivery');root=Path(self.tmp.name);path=root/'asset.mkv';path.write_bytes(b'first')
        org=self.e.Organizer(self.executor,object())
        original=delivery.LocalSource.hash;hashed=[]
        def counted(src):hashed.append(1);return original(src)
        with patch.object(self.e,'os',SimpleNamespace(name='nt')),patch.object(delivery.LocalSource,'hash',counted):
            first=org._source_hashes('plan',1,path,root)
            self.assertEqual(first,org._source_hashes('plan',1,path,root))
            self.assertEqual(2,len(hashed))
        with patch.object(self.e,'os',SimpleNamespace(name='posix')):
            first=org._source_hashes('plan',1,path,root)
            replacement=root/'replacement';replacement.write_bytes(b'other');replacement.replace(path)
            self.assertNotEqual(first,org._source_hashes('plan',1,path,root))
            def changed_during_hash(src):
                result=original(src);path.write_bytes(b'changed-size');return result
            with patch.object(delivery.LocalSource,'hash',changed_during_hash),self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):
                org._source_hashes('new-plan',1,path,root)
            self.assertIsNone(self.repo.setting('organize-source-hash:'+self.e.encoded(['new-plan',1])))

    @unittest.skipUnless(__import__('os').name=='posix','POSIX ctime invalidation requires POSIX filesystem')
    def test_source_hash_checkpoint_detects_same_size_change_with_restored_mtime(self):
        import os
        import time
        root=Path(self.tmp.name);path=root/'asset.mkv';path.write_bytes(b'first')
        org=self.e.Organizer(self.executor,object());before=path.stat()
        first=org._source_hashes('plan',1,path,root)
        time.sleep(.01);path.write_bytes(b'other');os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        self.assertNotEqual(before.st_ctime_ns,path.stat().st_ctime_ns)
        self.assertNotEqual(first,org._source_hashes('plan',1,path,root))

    def test_transfer_guard_blocks_unplanned_and_stale_but_leaves_manual(self):
        self.assertTrue(hasattr(self.e,'TransferGuard'),'W05 transfer guard missing')
        from types import SimpleNamespace
        self.executor.execute('plan',b'torrent')
        guard=self.e.TransferGuard(self.repo)
        def event(path):
            return SimpleNamespace(event_data={'fileitem':{'path':path,'storage':'local'},'target_path':'/out','cancel':False})
        unknown=event('/elsewhere/manual.mkv');guard.intercept(unknown)
        self.assertFalse(unknown.event_data['cancel'])
        denied=event('/test/E01.mkv');guard.intercept(denied)
        self.assertTrue(denied.event_data['cancel'])
        shared_root_manual=event('/test/manual-unrelated.mkv');guard.intercept(shared_root_manual)
        self.assertFalse(shared_root_manual.event_data['cancel'])

    def test_shared_hash_union_survives_sibling_cancel_and_reselection(self):
        self.executor.execute('plan',b'torrent')
        other=deepcopy(self.spec);other['selected_indices']=[0]
        other['targets']={self.keys[0]:next(iter(other['targets'].values()))}
        other['current']={self.keys[0]:dict(state='MISSING',revision=0)}
        self.auth.prepare('sibling','round',other,now=NOW)
        self.auth.claim('sibling',self.auth.vector([self.keys[0]]),now=NOW)
        self.assertEqual('PAUSED_VERIFIED',self.executor.execute('sibling',b'torrent')['state'])
        self.assertEqual({9,3,7},self.client.wanted)
        self.auth.cancel('sibling',self.auth.vector([self.keys[0]]),reason='cancel')
        self.assertEqual('PAUSED_VERIFIED',self.executor.execute('plan',b'torrent')['state'])
        self.assertEqual({3,7},self.client.wanted)

    def test_review_repeat_execute_resume_does_not_pause_running_task(self):
        self.assertEqual('RUNNING',self.executor.execute('plan',b'torrent',resume=True)['state'])
        before=list(self.client.calls)
        self.assertEqual('RUNNING',self.executor.execute('plan',b'torrent',resume=True)['state'])
        self.assertEqual(before,self.client.calls)
        self.assertEqual('DOWNLOADING',self.client.state)

    def test_review_shared_excluded_sibling_prevents_client_wide_resume(self):
        self.executor.execute('plan',b'torrent')
        other=deepcopy(self.spec);other['selected_indices']=[0]
        other['targets']={self.keys[0]:next(iter(other['targets'].values()))}
        other['current']={self.keys[0]:dict(state='MISSING',revision=0)}
        self.auth.prepare('sibling','round',other,now=NOW)
        self.auth.claim('sibling',self.auth.vector([self.keys[0]]),now=NOW)
        self.executor.execute('sibling',b'torrent')
        self.e.Exclusions(self.repo).add('deny-e01',{'targets':[self.keys[0]]},reason='review')
        before=list(self.client.calls)
        self.assertEqual('BLOCKED',self.executor.resume('plan')['state'])
        self.assertEqual(before,self.client.calls)

    def _sibling(self):
        other=deepcopy(self.spec);other['selected_indices']=[0]
        other['targets']={self.keys[0]:next(iter(other['targets'].values()))}
        other['current']={self.keys[0]:dict(state='MISSING',revision=0)}
        self.auth.prepare('sibling','round',other,now=NOW)
        self.auth.claim('sibling',self.auth.vector([self.keys[0]]),now=NOW)

    def test_shared_cohort_changes_between_revalidation_and_transaction_send_zero_rpc(self):
        for change in ('cancel','exclude','new','current','task','cycle'):
            with self.subTest(change=change):
                if change!='new':self._sibling()
                self.assertEqual('PAUSED_VERIFIED',self.executor.execute('plan',b'torrent')['state'])
                original=self.executor.authority.begin_shared_attempt
                def race(*args,**kwargs):
                    if change=='cancel':self.auth.cancel('sibling',self.auth.vector([self.keys[0]]),reason='race')
                    elif change=='exclude':self.e.Exclusions(self.repo).add('race',{'targets':[self.keys[0]]},reason='race')
                    elif change=='new':self._sibling()
                    elif change=='current':self.auth.update_current(self.keys[0],{'state':'PRESENT','evidence_ref':'race'},expected_revision=0)
                    elif change=='cycle':self.executor._new_cycle(self.spec)
                    else:self.repo.set_state(self.auth.plan('sibling')['task_id'],'PAUSED','race')
                    return original(*args,**kwargs)
                self.executor.authority.begin_shared_attempt=race
                before=list(self.client.calls)
                self.assertEqual('BLOCKED',self.executor.resume('plan')['state'])
                self.assertEqual(before,self.client.calls)
                self.doCleanups();self.setUp()

    def test_resume_new_pause_cycle_and_unknown_cohort_never_replay(self):
        self.assertEqual('RUNNING',self.executor.execute('plan',b'torrent',resume=True)['state'])
        self.assertEqual('PAUSED_VERIFIED',self.executor.execute('plan',b'torrent')['state'])
        self.assertEqual('RUNNING',self.executor.resume('plan')['state'])
        self.assertEqual(2,self.client.calls.count('resume'))
        self.assertEqual(1,self.client.calls.count('pause'))
        self.client.state='PAUSED'
        self.assertEqual('RUNNING',self.executor.resume('plan')['state'])
        self.assertEqual(3,self.client.calls.count('resume'))

    def test_legacy_resume_running_readback_does_not_send_new_shared_attempt(self):
        self.executor.execute('plan',b'torrent')
        vector=self.auth.vector([self.keys[1]])
        self.auth.begin_attempt('legacy-resume','plan',vector,'RESUME',[1,2],{'verb':'resume','id':'a'*40})
        self.auth.record_result('legacy-resume','UNKNOWN',{'code':'legacy lost response'})
        self.client.state='DOWNLOADING';before=list(self.client.calls)
        self.assertEqual('RUNNING',self.executor.resume('plan')['state'])
        self.assertEqual(before,self.client.calls)
        self.executor.reconcile('plan');self.assertEqual('SUCCEEDED',self.auth.action('legacy-resume')['state'])

    def test_execute_preserves_external_pause_until_explicit_resume(self):
        self.assertEqual('RUNNING',self.executor.execute('plan',b'torrent',resume=True)['state'])
        self.client.state='PAUSED'
        self.assertEqual('WAITING_ASSETS',self.executor.execute('plan',b'torrent',resume=True)['state'])
        self.assertEqual('RUNNING',self.executor._owned(self.spec)['state'])
        self.assertEqual('RUNNING',self.executor.resume('plan')['state'])
        self.assertEqual(2,self.client.calls.count('resume'))
        self.client.state='PAUSED'
        self.assertEqual('WAITING_ASSETS',self.executor.execute('plan',b'torrent')['state'])
        restarted=self.e.StrictExecutor(self.repo,lambda name:self.client,revalidate=self.executor.revalidate,verify_torrent=self.executor.verify_torrent)
        self.assertEqual('RUNNING',restarted.resume('plan')['state'])
        self.assertEqual(3,self.client.calls.count('resume'))

    def test_returned_history_repair_without_progress_can_retry_missing_rows(self):
        source=Path(self.tmp.name)/'repair-source';source.mkdir();dest=Path(self.tmp.name)/'repair-target';dest.mkdir()
        for f in self.files:(source/f['path']).write_bytes(b'x'*f['size'])
        rows=[];batches=[]
        class Host:
            def history(inner,s,paths):
                rows.append(str(paths[1]));raise RuntimeError('first row committed; second failed')
            def history_receipt(inner,s,paths):return set(paths)==set(rows)
            def history_missing(inner,s,paths):return sorted(set(paths)-set(rows))
            def repair_history(inner,s,paths,missing):
                batches.append(list(missing))
                if len(batches)==1:raise RuntimeError('temporary local failure before any write, returned')
                rows.extend(missing);return inner.history_receipt(s,paths)
        self.executor.execute('plan',b'torrent');self.client.stats.update({3:200,7:10})
        org=self.e.Organizer(self.executor,Host(),source_root=lambda s:source)
        self.assertEqual('UNKNOWN',org.organize('plan',dest)['state'])
        self.assertEqual('UNKNOWN',org.reconcile('plan')['state'])
        self.assertEqual('RECONCILED',org.reconcile('plan')['state'])
        self.assertEqual([[str(source/'E02.srt')]]*2,batches)
        self.assertEqual(2,len(rows));self.assertEqual(2,len(set(rows)))
        org.reconcile('plan');self.assertEqual(2,len(batches))

    def test_history_repair_inflight_ambiguous_and_exhaustion_cannot_redispatch(self):
        vector=self.auth.vector([self.keys[1]]);paths=['/test/E02.mkv','/test/E02.srt']
        self.auth.begin_attempt('history-original','plan',vector,'ORGANIZE',[1,2],{'verb':'history','paths':paths})
        self.auth.record_result('history-original','UNKNOWN',{'code':'CLIENT_RESPONSE_UNKNOWN'})
        def attempt():return self.auth.begin_history_repair('history-original',vector,[paths[1]],exclusion_token=self.executor.exclusions.token())
        first=attempt();self.assertTrue(first['dispatch'])
        with self.assertRaisesRegex(ValueError,'HISTORY_REPAIR_IN_FLIGHT'):attempt()
        self.auth.record_result(first['id'],'UNKNOWN',{'code':'ambiguous transport'})
        with self.assertRaisesRegex(ValueError,'HISTORY_REPAIR_RETURN_UNPROVEN'):attempt()
        self.auth.record_result(first['id'],'UNKNOWN',{'code':'LOCAL_HISTORY_REPAIR_RETURNED'})
        second=attempt();self.assertTrue(second['dispatch']);self.assertNotEqual(first['id'],second['id'])
        self.auth.record_result(second['id'],'UNKNOWN',{'code':'LOCAL_HISTORY_REPAIR_RETURNED'})
        third=attempt();self.assertTrue(third['dispatch'])
        self.auth.record_result(third['id'],'UNKNOWN',{'code':'LOCAL_HISTORY_REPAIR_RETURNED'})
        with self.assertRaisesRegex(ValueError,'HISTORY_REPAIR_EXHAUSTED'):attempt()
        with self.repo.connection() as db:self.assertEqual(4,db.execute('SELECT COUNT(*) FROM plan_actions').fetchone()[0])
        from types import SimpleNamespace
        host=SimpleNamespace(history_receipt=lambda *a:False,history_missing=lambda *a:[paths[1]],repair_history=lambda *a:self.fail('exhausted repair dispatched'))
        result=self.e.Organizer(self.executor,host).reconcile('plan')
        self.assertEqual(('BLOCKED','HISTORY_REPAIR_EXHAUSTED'),(result['state'],result['reason']))

    def test_unknown_shared_resume_receipt_settles_only_original_cohort(self):
        self._sibling();self.executor.execute('plan',b'torrent')
        def lost(tid):
            self.client.calls.append('resume');self.client.state='DOWNLOADING';raise TimeoutError()
        self.client.resume=lost
        self.assertEqual('UNKNOWN',self.executor.resume('plan')['state'])
        with self.repo.connection() as db:
            actions=[dict(r) for r in db.execute("SELECT * FROM plan_actions WHERE kind='RESUME'")]
        self.assertEqual({'plan','sibling'},{a['plan_id'] for a in actions})
        self.auth.cancel('sibling',self.auth.vector([self.keys[0]]),reason='after send')
        before=list(self.client.calls)
        self.assertEqual('BLOCKED',self.executor.execute('plan',b'torrent')['state'])
        self.assertEqual(before,self.client.calls)
        self.assertEqual('RECONCILED',self.executor.reconcile('plan')['state'])
        self.assertTrue(all(self.auth.action(a['id'])['state']=='SUCCEEDED' for a in actions))
        self.assertEqual(1,self.client.calls.count('resume'))

    def test_copy_receipt_committed_before_asset_projection_recovers_without_copy(self):
        import shutil,json
        source=Path(self.tmp.name)/'source';source.mkdir();target=Path(self.tmp.name)/'target';target.mkdir()
        for f in self.files:(source/f['path']).write_bytes(b'x'*f['size'])
        calls=[]
        class Host:
            def history(inner,*args):return True
            def history_receipt(inner,*args):return True
            def transfer_receipt(inner,*args):return True
            def transfer(inner,src,dest,item,snapshot):
                calls.append(item['index']);out=dest/src.name
                with self.repo.connection(write=True) as db:db.execute('UPDATE organized_assets SET destination=? WHERE source=?',(str(out),str(src)))
                shutil.copyfile(src,out);return out
        class Crash(BaseException):pass
        original=self.executor.authority.record_result
        def crash(action,outcome,evidence,**kwargs):
            original(action,outcome,evidence,**kwargs)
            if json.loads(self.auth.action(action)['payload']).get('verb')=='organize:1':raise Crash()
        self.executor.execute('plan',b'torrent');self.client.stats.update({3:200,7:10})
        org=self.e.Organizer(self.executor,Host(),source_root=lambda s:source)
        self.executor.authority.record_result=crash
        with self.assertRaises(Crash):org.organize('plan',target)
        self.executor.authority.record_result=original
        self.assertEqual('RECONCILED',org.reconcile('plan')['state'])
        self.assertEqual('COMPLETE',org.organize('plan',target)['state'])
        self.assertEqual([1,2],calls)

    def test_historical_shared_font_and_license_keep_all_consumers_in_common_video_directory(self):
        from types import ModuleType,SimpleNamespace
        from unittest.mock import patch
        import sys,shutil
        # Frozen pre-D07 plans remain readable by downstream organization.
        files=[dict(index=0,path='Pack/Show.S01E01.mkv',size=1,role='video',targets=[self.keys[0]],requires=[2]),
               dict(index=1,path='Pack/Show.S01E02.mkv',size=1,role='video',targets=[self.keys[1]],requires=[2]),
               dict(index=2,path='Pack/Fonts/shared.ttf',size=1,role='attachment',targets=self.keys,requires=[3]),
               dict(index=3,path='Pack/LICENSE.txt',size=1,role='other',targets=self.keys,requires=[])]
        self.assertEqual(set(self.keys),set(files[2]['targets']))
        root=Path(self.tmp.name);source=root/'font-source';source.mkdir();dest=root/'font-dest';dest.mkdir()
        calls=[]
        class Chain:
            def plan_transfer(inner,**kw):return SimpleNamespace(final_target_path=Path(kw['target_path'])/kw['fileitem'].name)
            def transfer(inner,**kw):
                calls.append(kw);out=Path(kw['target_path'])/kw['fileitem'].name;out.parent.mkdir(exist_ok=True)
                shutil.copyfile(kw['fileitem'].path,out);return SimpleNamespace(success=True,target_item=SimpleNamespace(path=str(out)))
        modules={}
        for name,values in {'app.chain.transfer':{'TransferChain':Chain},'app.sdk.media':{'MetaInfoPath':lambda p:SimpleNamespace()},'app.schemas.file':{'FileItem':SimpleNamespace},'app.schemas.system':{'TransferDirectoryConf':SimpleNamespace}}.items():
            module=ModuleType(name);module.__dict__.update(values);modules[name]=module
        host=self.e.HostOrganization(None);host.transfer_receipt=lambda *a:True
        host.video_outputs={0:dest/'E01.mkv',1:dest/'E02.mkv'}
        for path in host.video_outputs.values():path.write_bytes(b'v')
        with patch.dict(sys.modules,modules):
            for item in files[2:]:
                src=source/Path(item['path']).name;src.write_bytes(b'x')
                out=host.transfer(src,dest,item,dict(torrent_files=files,save_path=str(source),selected_indices=[0,1,2,3]))
                self.assertEqual(b'x',out.read_bytes())
                self.assertEqual(set(self.keys),set(item['targets']))
        self.assertEqual([dest/'Fonts',dest],[kw['target_path'] for kw in calls])
        self.assertTrue(all(not kw['target_directory'].renaming for kw in calls))
        separate=dest/'separate';separate.mkdir();(separate/'E02.mkv').write_bytes(b'v')
        host.video_outputs[1]=separate/'E02.mkv'
        with patch.dict(sys.modules,modules),self.assertRaisesRegex(ValueError,'SHARED_DEPENDENCY_DIRECTORY_CONFLICT'):
            host.transfer(source/'shared.ttf',dest,files[2],dict(torrent_files=files,save_path=str(source)))
        self.assertEqual(2,len(calls))

    def test_text_subtitle_languages_and_tracks_keep_exact_public_names(self):
        from types import ModuleType,SimpleNamespace
        from unittest.mock import patch
        import sys,shutil
        names=['Show.S01E02.en.srt','Show.S01E02.zh-Hans.srt','Show.S01E02.zh-Hant.srt',
               'Show.S01E02.en.forced.srt','Show.S01E02.en.SDH.srt','Show.S01E02.en.commentary.srt']
        files=load('candidates').bind_files([('Pack/Show.S01E02.1080p.mkv',1)]+[('Pack/'+name,1) for name in names],self.r.Target('电视剧','tmdb','42',1))
        root=Path(self.tmp.name);source=root/'tracks';source.mkdir();dest=root/'sub-dest';dest.mkdir()
        video=dest/'Organized.S01E02.1080p.mkv';video.write_bytes(b'v');calls=[]
        class Chain:
            def plan_transfer(inner,**kw):
                name=video.stem+'.'+kw['fileitem'].extension if kw['target_directory'].renaming else kw['fileitem'].name
                return SimpleNamespace(final_target_path=Path(kw['target_path'])/name)
            def transfer(inner,**kw):
                out=inner.plan_transfer(**kw).final_target_path
                self.assertFalse(out.exists());shutil.copyfile(kw['fileitem'].path,out);calls.append(kw)
                return SimpleNamespace(success=True,target_item=SimpleNamespace(path=str(out)))
        modules={}
        for name,values in {'app.chain.transfer':{'TransferChain':Chain},'app.sdk.media':{'MetaInfoPath':lambda p:SimpleNamespace()},'app.schemas.file':{'FileItem':SimpleNamespace},'app.schemas.system':{'TransferDirectoryConf':SimpleNamespace}}.items():
            module=ModuleType(name);module.__dict__.update(values);modules[name]=module
        host=self.e.HostOrganization(None);host.transfer_receipt=lambda *a:True;host.video_outputs={0:video};outputs=[]
        with patch.dict(sys.modules,modules):
            for n,item in enumerate(files[1:]):
                src=source/item['path'];src.parent.mkdir(parents=True,exist_ok=True);src.write_bytes(bytes([n]))
                output=host.transfer(src,dest,item,dict(torrent_files=files,save_path=str(source)))
                self.assertEqual(src.read_bytes(),output.read_bytes());outputs.append(output)
            long_item=dict(files[1],path='Pack/'+('long'*70)+'.en.srt')
            with self.assertRaisesRegex(ValueError,'TRANSFER_NAME_TOO_LONG'):
                host.prepare_transfer(source/files[1]['path'],dest,long_item,dict(torrent_files=[files[0],long_item],save_path=str(source)))
        self.assertEqual(len(files)-1,len(set(outputs)))
        for item,output,call in zip(files[1:],outputs,calls):
            original=Path(item['path']).name
            self.assertIn(Path(original).stem,output.stem)
            self.assertTrue(output.name.startswith(video.stem+'.'))
            self.assertFalse(call['target_directory'].renaming)
            self.assertEqual('never',call['target_directory'].overwrite_mode)
            self.assertEqual(str(source/item['path']),call['fileitem'].path)

    def test_legacy_subtitle_preflight_settles_only_proven_not_sent_and_preserves_completed(self):
        from types import ModuleType,SimpleNamespace
        from unittest.mock import patch
        import sys,shutil,json
        self.auth.cancel('plan',self.auth.vector([self.keys[1]]),reason='fixture')
        self.files[2]['path']='E02.zh-Hans.srt'
        self.files.append(dict(index=3,path='E02.en.srt',size=12,role='subtitle',targets=[self.keys[1]],requires=[]))
        spec=deepcopy(self.spec);spec.update(torrent_files=self.files,selected_indices=[1,2,3])
        self.auth.prepare('recovery','round',spec,now=NOW);self.auth.claim('recovery',self.auth.vector([self.keys[1]]),now=NOW)
        self.client.files=lambda h:[dict(id=f['index'],path=f['path'],size=f['size'],wanted=f['index'] in self.client.wanted,completed=f['size']) for f in self.files]
        self.assertEqual('PAUSED_VERIFIED',self.executor.execute('recovery',b'torrent')['state'])
        source=Path(self.tmp.name)/'legacy-source';source.mkdir();dest=Path(self.tmp.name)/'legacy-target';dest.mkdir()
        for f in self.files:(source/f['path']).write_bytes(bytes([f['index']])*f['size'])
        video=dest/'Renamed.E02.mkv';shutil.copyfile(source/'E02.mkv',video)
        english=dest/'Renamed.E02.srt';shutil.copyfile(source/'E02.en.srt',english)
        vector=self.auth.vector([self.keys[1]])
        with self.repo.connection(write=True) as db:
            for i,out,state in ((1,video,'COMPLETE'),(2,None,'AUTHORIZED'),(3,english,'COMPLETE')):
                src=source/self.files[i]['path'];ev=dict(vector=vector,target_root=str(dest),indices=[1,2,3],source_mtime_ns=src.stat().st_mtime_ns,exclusions_token=self.executor.exclusions.token())
                db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?)',('recovery',i,str(src),str(out) if out else None,self.files[i]['size'],self.e.digest(src),state,json.dumps(ev)))
        src=source/self.files[2]['path']
        self.executor._mutation(self.auth.plan('recovery'),[1,2,3],vector,'history','ORGANIZE',lambda:True,dict(paths=[str(source/self.files[i]['path']) for i in [1,2,3]],content_sha1=[self.e.asset_hashes(source/self.files[i]['path'])[1] for i in [1,2,3]]))
        old=self.auth.begin_attempt('legacy-subtitle','recovery',vector,'ORGANIZE',[1,2,3],dict(verb='organize:2',file_index=2,source=str(src),target_root=str(dest),sha256=self.e.digest(src)))
        self.assertTrue(old['dispatch']);copies=[];history={}
        class Chain:
            def plan_transfer(inner,**kw):
                name=video.stem+'.'+kw['fileitem'].extension if kw['target_directory'].renaming else kw['fileitem'].name
                return SimpleNamespace(final_target_path=Path(kw['target_path'])/name)
            def transfer(inner,**kw):
                out=inner.plan_transfer(**kw).final_target_path;self.assertFalse(out.exists())
                shutil.copyfile(kw['fileitem'].path,out);copies.append(kw['fileitem'].path)
                history[kw['fileitem'].path]=SimpleNamespace(src=kw['fileitem'].path,dest=str(out),dest_storage='local',status=True,media_source='tmdb',media_id='42')
                return SimpleNamespace(success=True,target_item=SimpleNamespace(path=str(out)))
        class Hist:
            def get_by_src(inner,src,storage=None):return history.get(src)
            def get_success_by_src(inner,src,storage=None):return history.get(src)
        modules={}
        for name,values in {'app.chain.transfer':{'TransferChain':Chain},'app.sdk.media':{'MetaInfoPath':lambda p:SimpleNamespace()},'app.schemas.file':{'FileItem':SimpleNamespace},'app.schemas.system':{'TransferDirectoryConf':SimpleNamespace},'app.db.oper.transferhistory':{'TransferHistoryOper':Hist}}.items():
            module=ModuleType(name);module.__dict__.update(values);modules[name]=module
        host=self.e.HostOrganization(None,repository=self.repo);host.history=lambda *a:True;host.video_outputs={1:video}
        org=self.e.Organizer(self.executor,host,source_root=lambda s:source)
        with patch.dict(sys.modules,modules):
            proposed=host.prepare_transfer(src,dest,self.files[2],spec)['planned'];proposed.write_bytes(b'unrelated')
            with self.repo.connection() as db:before=db.execute('SELECT COUNT(*) FROM plan_actions').fetchone()[0]
            preflight=org.organize('recovery',dest)
            self.assertEqual('DESTINATION_ALREADY_EXISTS',preflight['reason'])
            with self.repo.connection() as db:self.assertEqual(before,db.execute('SELECT COUNT(*) FROM plan_actions').fetchone()[0])
            proposed.unlink()
            self.assertEqual('BLOCKED',org.organize('recovery',dest)['state']);self.assertEqual([],copies)
            self.auth.record_result('legacy-subtitle','UNKNOWN',{'code':'CLIENT_RESPONSE_UNKNOWN'})
            with self.repo.connection(write=True) as db:db.execute("UPDATE organized_assets SET destination=? WHERE plan_id='recovery' AND file_index=2",(str(dest/'possibly-sent.srt'),))
            self.assertEqual('BLOCKED',org.organize('recovery',dest)['state']);self.assertEqual([],copies)
            # Restore the independent NOT_SENT fixture: production never clears this field.
            with self.repo.connection(write=True) as db:db.execute("UPDATE organized_assets SET destination=NULL WHERE plan_id='recovery' AND file_index=2")
            history[str(src)]=SimpleNamespace(src=str(src))
            self.assertEqual('BLOCKED',org.organize('recovery',dest)['state']);self.assertEqual([],copies)
            history.clear();settle=self.executor.authority.settle_legacy_preflight
            def race(*args,**kw):
                self.e.Exclusions(self.repo).add('preflight-race',{'targets':[self.keys[1]]},reason='race')
                return settle(*args,**kw)
            self.executor.authority.settle_legacy_preflight=race
            self.assertEqual('BLOCKED',org.organize('recovery',dest)['state']);self.assertEqual([],copies)
            self.assertEqual('UNKNOWN',self.auth.action('legacy-subtitle')['state'])
            self.executor.authority.settle_legacy_preflight=settle;self.e.Exclusions(self.repo).revoke('preflight-race')
            result=org.organize('recovery',dest);self.assertEqual('COMPLETE',result['state'],result)
            self.assertEqual('COMPLETE',org.organize('recovery',dest)['state'])
        self.assertEqual([str(src)],copies)
        self.assertEqual('FAILED',self.auth.action('legacy-subtitle')['state'])
        self.assertEqual(self.e.digest(source/'E02.en.srt'),self.e.digest(english))
        self.assertEqual(self.e.digest(source/'E02.mkv'),self.e.digest(video))

    def test_partial_public_history_compensates_only_missing_rows_without_replay(self):
        from types import ModuleType,SimpleNamespace
        from unittest.mock import patch
        import sys,json
        source=Path(self.tmp.name)/'history-source';source.mkdir()
        dest=Path(self.tmp.name)/'history-target';dest.mkdir()
        for f in self.files:(source/f['path']).write_bytes(b'x'*f['size'])
        rows=[];writes=[];headers=[]
        class Oper:
            def get_by_hash(inner,h):return headers[-1] if headers else None
            def add(inner,**kw):headers.append(SimpleNamespace(**kw))
            def get_files_by_hash(inner,h,state=1):return list(rows)
            def add_files(inner,items):
                for item in items:
                    writes.append(item['fullpath']);rows.append(SimpleNamespace(**item))
                    if len(writes)==1:raise RuntimeError('committed first file, second write failed')
        modules={}
        for name,values in {'app.db.oper.downloadhistory':{'DownloadHistoryOper':Oper},'app.sdk.media':{'resolve_media_identity':lambda **kw:(SimpleNamespace(value='tmdb'),'42')}}.items():
            module=ModuleType(name);module.__dict__.update(values);modules[name]=module
        host=self.e.HostOrganization(SimpleNamespace(title='fixture',year=2026),repository=self.repo)
        self.executor.execute('plan',b'torrent');self.client.stats.update({3:200,7:10})
        org=self.e.Organizer(self.executor,host,source_root=lambda s:source)
        with patch.dict(sys.modules,modules):
            self.assertEqual('UNKNOWN',org.organize('plan',dest)['state'])
            with self.repo.connection() as db:original=db.execute("SELECT id FROM plan_actions WHERE kind='ORGANIZE'").fetchone()[0]
            rows[0].downloader='unrelated'
            org.reconcile('plan');self.assertEqual(1,len(writes));rows[0].downloader='test'
            headers[-1].media_id='43'
            org.reconcile('plan');self.assertEqual(1,len(writes));headers[-1].media_id='42'
            begin=self.executor.authority.begin_history_repair
            def race(*args,**kw):
                self.e.Exclusions(self.repo).add('history-race',{'targets':[self.keys[1]]},reason='race')
                return begin(*args,**kw)
            self.executor.authority.begin_history_repair=race
            org.reconcile('plan');self.assertEqual(1,len(writes))
            self.executor.authority.begin_history_repair=begin;self.e.Exclusions(self.repo).revoke('history-race')
            self.assertEqual('RECONCILED',org.reconcile('plan')['state'])
            self.assertEqual('SUCCEEDED',self.auth.action(original)['state'])
            self.assertEqual(2,len(writes));self.assertEqual(2,len(set(writes)))
            org.reconcile('plan');self.assertEqual(2,len(writes))
            with self.repo.connection() as db:
                repairs=[json.loads(r[0]) for r in db.execute("SELECT payload FROM plan_actions WHERE kind='ORGANIZE'") if json.loads(r[0]).get('verb')=='history-repair']
            self.assertEqual([[str(source/'E02.srt')]],[p['missing_paths'] for p in repairs])
            self.assertEqual(1,len(headers))

    def test_reconcile_lost_add_needs_marker_exact_identity_and_full_table(self):
        self.assertTrue(hasattr(self.executor,'reconcile'),'durable exact reconciliation missing')
        self.client.lost_add=True;self.executor.execute('plan',b'torrent')
        self.assertEqual('UNKNOWN',self.executor.reconcile('plan')['state'])
        marker=self.executor._owned(self.spec)['marker'];original=self.client.task
        self.client.task=lambda h:dict(original(h),markers=[marker])
        self.assertEqual('RECONCILED',self.executor.reconcile('plan')['state'])
        self.assertEqual('PAUSED_VERIFIED',self.executor.execute('plan',b'torrent')['state'])
        self.assertEqual(1,self.client.calls.count('add'))

    def test_lost_resume_readback_settles_without_second_rpc(self):
        self.executor.execute('plan',b'torrent')
        def lost(tid):
            self.client.calls.append('resume');self.client.state='DOWNLOADING';raise TimeoutError()
        self.client.resume=lost
        self.assertEqual('UNKNOWN',self.executor.resume('plan')['state'])
        self.assertEqual('RECONCILED',self.executor.reconcile('plan')['state'])
        self.assertEqual('RUNNING',self.executor.resume('plan')['state'])
        self.assertEqual(1,self.client.calls.count('resume'))

    def test_organizer_real_guard_admits_current_dispatch_only(self):
        import shutil
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(dir=PLUGIN.parents[1]) as directory:
            source=Path(directory)/'source';source.mkdir();dest=Path(directory)/'dest';dest.mkdir()
            layout=source.as_posix()
            if len(layout)>2 and layout[1]==':':layout=layout[2:]
            self.auth.cancel('plan',self.auth.vector([self.keys[1]]),reason='new local fixture')
            spec=deepcopy(self.spec);spec['save_path']=layout
            self.auth.prepare('local','round',spec,now=NOW);self.auth.claim('local',self.auth.vector([self.keys[1]]),now=NOW)
            original=self.client.task;self.client.task=lambda h:dict(original(h),save_path=layout) if self.client.exists else None
            self.assertEqual('PAUSED_VERIFIED',self.executor.execute('local',b'torrent')['state'])
            for f in self.files:(source/f['path']).write_bytes(b'x'*f['size'])
            self.client.stats.update({3:200,7:10});guard=self.e.TransferGuard(self.repo)
            def event(src,target):return SimpleNamespace(event_data=dict(fileitem=dict(path=str(src),storage='local'),target_path=str(target),target_storage='local',cancel=False))
            before=event(source/'E02.mkv',dest/'E02.mkv');guard.intercept(before);self.assertTrue(before.event_data['cancel'])
            class Host:
                def history(inner,*args):return True
                def transfer(inner,src,target,item,snapshot):
                    output=target/src.name
                    for directory in (False,True):
                        if directory:output.mkdir()
                        else:output.write_bytes(b'foreign')
                        occupied=event(src,output);guard.intercept(occupied)
                        self.assertTrue(occupied.event_data['cancel'])
                        if directory:output.rmdir()
                        else:
                            self.assertEqual(b'foreign',output.read_bytes());output.unlink()
                    outside=event(src,target.parent/src.name);guard.intercept(outside)
                    self.assertTrue(outside.event_data['cancel'])
                    # Model both link types at the filesystem boundary, including a
                    # broken link whose exists() is false. No Windows symlink privilege.
                    for existing in (False,True):
                        if existing:output.write_bytes(b'foreign')
                        with patch.object(Path,'is_symlink',lambda p:p==output):
                            linked=event(src,output);guard.intercept(linked)
                            self.assertTrue(linked.event_data['cancel'])
                        if existing:output.unlink()
                    check=event(src,target/src.name);guard.intercept(check)
                    self.assertFalse(check.event_data['cancel'])
                    shutil.copyfile(src,target/src.name);return target/src.name
            self.assertEqual('COMPLETE',self.e.Organizer(self.executor,Host()).organize('local',dest)['state'])

    def test_schema4_migration_preserves_all_existing_rows_and_authority(self):
        with self.repo.connection(write=True) as db:
            for table in ('management_operations','management_previews','candidate_decisions','archive_scan_baselines','migration_history','migration_receipts','discovery_targets','discovery_records','discovery_sources','ai_usage','ai_requests','ai_runtime','delivery_bundles','local_observations','reconcile_checkpoints','archive_assets','archive_sources','archive_scan_items','archive_scans','archive_locations','archive_contents','archive_versions','archive_targets'):db.execute('DROP TABLE '+table)
            for table in ('organized_assets','managed_downloads','exclusions','candidates'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=4')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables}
        migrated=self.r.Repository(self.repo.path)
        with migrated.connection() as db:
            self.assertEqual(12,db.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(before,{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables})
        self.assertEqual('ACTIVE',self.p.Authority(migrated).plan('plan')['authorization'])

    def test_mutated_torrent_or_stopped_generation_has_zero_external_calls(self):
        self.executor.verify_torrent=lambda content:('b'*40,[])
        self.assertEqual('BLOCKED',self.executor.execute('plan',b'changed')['state'])
        self.assertEqual([],self.client.calls)
        self.repo.set_state(self.auth.plan('plan')['task_id'],'PAUSED','admin')
        self.assertEqual('BLOCKED',self.executor.resume('plan')['state'])
        self.assertEqual([],self.client.calls)


if __name__ == '__main__':
    unittest.main()
