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
        self.executor.sample('plan')
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
                    check=event(src,target/src.name);guard.intercept(check)
                    self.assertFalse(check.event_data['cancel'])
                    shutil.copyfile(src,target/src.name);return target/src.name
            self.assertEqual('COMPLETE',self.e.Organizer(self.executor,Host()).organize('local',dest)['state'])

    def test_schema4_migration_preserves_all_existing_rows_and_authority(self):
        with self.repo.connection(write=True) as db:
            for table in ('organized_assets','managed_downloads','exclusions','candidates'):db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=4')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables}
        migrated=self.r.Repository(self.repo.path)
        with migrated.connection() as db:
            self.assertEqual(5,db.execute('PRAGMA user_version').fetchone()[0])
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
