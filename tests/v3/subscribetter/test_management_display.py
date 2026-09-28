"""SQLite display contracts. Also exports the real DTO for the mounted Vue check."""
import json
import unittest
import test_management as management
from test_planner import load, NOW


class DisplayTests(unittest.TestCase):
    setUpClass=management.ManagementTests.__dict__['setUpClass']
    setUp=management.ManagementTests.setUp

    def populate(self):
        self.views=load('ui').Views(self.plugin)
        task=self.repo.submit('display',self.r.Target('电视剧','themoviedb','42',1),{'name':'展示契约'},'test')
        self.task_id=task['id'];self.task_generation=task['generation'];self.key=json.dumps(['电视剧','themoviedb','42',1,'',7],ensure_ascii=False,separators=(',',':'))
        evaluation={'reason':'READY_FOR_OBSERVATION_AND_CLAIM','decisions':{self.key:{'status':'ALLOW','reason':'QUALITY_UPGRADE','comparisons':[{'dimension':'picture','order':1}]}},'plans':[{'large':'x'*10000}]}
        load('evidence').append(self.repo,'candidate',[self.key],evaluation,task_id=self.task_id,observed={'title':'契约测试资源'})
        with self.repo.connection(write=True) as db:
            db.execute("INSERT INTO opportunities(id,task_id,scope,mode,state,config,created_at,updated_at) VALUES('round',?,'[]','CONTINUOUS','ACTIVE','{}',?,?)",(self.task_id,NOW.isoformat(),NOW.isoformat()))
            for i in range(7):
                # Deliberate timestamp tie for the last two records.
                db.execute("INSERT INTO plans(id,opportunity_id,task_id,snapshot,authorization,transfer_phase,created_at) VALUES(?,'round',?,'{}','ACTIVE','DOWNLOADING',?)",('p'+str(i),self.task_id,'2026-09-26T00:00:0'+str(min(i,5))+'+00:00'))
            db.execute('UPDATE plans SET task_generation=?',(self.task_generation,))
        return evaluation

    def test_bounded_list_and_complete_detail(self):
        evaluation=self.populate()
        page=self.views.decisions(task_id=self.task_id,user=None)
        row=page.items[0]
        self.assertNotIn('evaluation',row.evidence)
        self.assertEqual('QUALITY_UPGRADE',row.summary['outcomes'][0]['reason'])
        self.assertEqual(['picture'],row.summary['outcomes'][0]['dimensions'])
        self.assertLess(len(row.model_dump_json()),2000)
        self.assertEqual(evaluation,self.views.decision(row.id,user=None).evidence['evaluation'])
        with self.repo.connection(write=True) as db:
            evaluation['decisions']={str(i):{'status':'DEFER','reason':'UNKNOWN','comparisons':[]} for i in range(30)}
            db.execute('UPDATE candidate_decisions SET data=?',(json.dumps({'evaluation':evaluation}),))
        summary=self.views.decisions(task_id=self.task_id,user=None).items[0].summary
        self.assertEqual((30,8),(summary['total'],len(summary['outcomes'])))

    def test_newest_before_pagination_and_stable_ties_preserve_default(self):
        self.populate()
        oldest=self.views.task_plans(self.task_id,limit=5,user=None)
        newest=self.views.task_plans(self.task_id,limit=5,sort='newest',user=None)
        remaining=self.views.task_plans(self.task_id,limit=5,offset=5,sort='newest',user=None)
        self.assertEqual(['p0','p1','p2','p3','p4'],[r.id for r in oldest.items])
        self.assertEqual(['p6','p5','p4','p3','p2','p1','p0'],[r.id for r in newest.items+remaining.items])

    def populate_processing(self,size=100):
        from test_planner import PlanSelectionTests
        PlanSelectionTests.setUpClass();selection=PlanSelectionTests();selection.setUp()
        candidate=selection.candidate(quality='2160p Dolby Vision DDP')
        for file in candidate['torrent_files']:file['size']=size
        previous=selection.policy.normalize({'title':'Fictional 1080p WEB-DL -HHWEB 中文字幕','technical':{'resolution':1080,'picture':0,'audio':0}},current=True)
        selection.current[selection.keys[0]]={'state':'PRESENT','revision':1,'versions':[selection.p.Version('old',previous)]}
        output=selection.planner.evaluate(candidate,selection.current,selection.keys)
        snapshot=output['plans'][0]
        self.assertEqual(2,snapshot['targets'][selection.keys[0]]['quality_facts']['picture'])
        self.populate();key=selection.keys[0];sibling=selection.keys[1]
        from types import SimpleNamespace
        self.plugin.runtime=SimpleNamespace(delivery=SimpleNamespace(archive=SimpleNamespace(policy=selection.policy)))
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE candidate_decisions SET data=json_set(data,'$.evaluation',json(?))",(json.dumps(output),))
            db.execute('UPDATE plans SET snapshot=? WHERE id=?',(json.dumps(snapshot),'p6'))
            db.execute('INSERT INTO target_units(target_key,task_id,identity,owner_plan_id,generation) VALUES(?,?,?,\'p6\',2)',(key,self.task_id,key))
            db.execute("INSERT INTO plan_targets(plan_id,target_key,generation,state,action,transfer_phase) VALUES('p6',?,2,'ACTIVE','ACQUIRE','RAPID_WAIT')",(key,))
            current={'state':'PRESENT','versions':[{'version_id':'old','reliable':True,'raw':{'title':'Example 1080p WEB-DL','description':'','labels':[],'subtitle_description':'','technical':{'resolution':1080,'picture':0,'audio':0}}}]}
            db.execute('UPDATE target_units SET current_facts=?,current_revision=1',(json.dumps(current),))
            db.execute("UPDATE tasks SET state='ACTIVE' WHERE id=?",(self.task_id,))
            sample={'files':{'0':{'downloaded_bytes':size//4,'speed':size//20},'1':{'downloaded_bytes':size,'speed':size}},'sampled_at':NOW.isoformat(),'status':'DOWNLOADING','downloaded_bytes':125}
            db.execute("INSERT INTO plan_progress VALUES('p6','[0,1]',?)",(json.dumps(sample),))
            bundle={'vector':{key:{'generation':2,'owner_plan_id':'p6'}},'reason':'QUALITY_UPGRADE','rapid_miss_limit':6,
                    'files':[{'file_index':0,'state':'PENDING','misses':2,'due':'2099-01-01T00:00:00+00:00'},{'file_index':1,'state':'VERIFIED','misses':99}], 'manifest':{'assets':[]}}
            db.execute("INSERT INTO delivery_bundles VALUES('b','p6','r','WAITING','2099-01-01T00:00:00+00:00',0,?)",(json.dumps(bundle),))
        return key

    def test_target_specs_and_progress_are_scoped_and_generation_fenced(self):
        self.populate_processing()
        with self.repo.connection() as db:before=list(db.iterdump())
        unit=self.views.task(self.task_id,user=None).units.items[0]
        self.assertEqual(2160,unit.processing['quality']['resolution'])
        self.assertEqual(25,unit.processing['download']['downloaded_bytes'])
        self.assertEqual(100,unit.processing['download']['total_bytes'])
        self.assertEqual(2,unit.processing['transfer_files'][0]['misses'])
        self.assertEqual(6,unit.processing['transfer_files'][0]['miss_limit'])
        self.assertEqual(1,len(unit.processing['transfer_files']))
        self.assertEqual('quality',unit.processing['change']['kind'])
        self.assertEqual(1,unit.processing['change']['baseline_revision'])
        self.assertTrue(unit.processing['change']['baseline_current'])
        changes=unit.processing['change']['versions'][0]['changes']
        self.assertEqual(['resolution','picture','audio'],[r['dimension'] for r in changes if r['order']==1])
        summary=self.views.task(self.task_id,user=None).task.progress
        self.assertEqual([{'phase':'RAPID_WAIT','count':1}],summary['stages'])
        self.assertEqual(1,summary['stage_sample_count'])
        with self.repo.connection() as db:self.assertEqual(before,list(db.iterdump()))
        with self.repo.connection(write=True) as db:db.execute('UPDATE target_units SET current_revision=2')
        self.assertFalse(self.views.task(self.task_id,user=None).units.items[0].processing['change']['baseline_current'])
        with self.repo.connection(write=True) as db:db.execute('UPDATE target_units SET generation=3')
        self.assertIsNone(self.views.task(self.task_id,user=None).units.items[0].processing)
        self.assertEqual([],self.views.task(self.task_id,user=None).task.progress['stages'])

        with self.repo.connection(write=True) as db:
            db.execute('UPDATE target_units SET generation=2')
            db.execute("UPDATE plans SET authorization='SUPERSEDED' WHERE id='p6'")
        self.assertIsNone(self.views.task(self.task_id,user=None).units.items[0].processing)

    def test_unknown_progress_publication_shared_file_and_multiple_versions(self):
        self.populate_processing()
        read=lambda:self.views.task(self.task_id,user=None).units.items[0]
        with self.repo.connection(write=True) as db:
            sample=json.loads(db.execute('SELECT sample FROM plan_progress').fetchone()[0]);sample['files']['0']['downloaded_bytes']=None
            db.execute('UPDATE plan_progress SET sample=?',(json.dumps(sample),))
            db.execute("UPDATE target_units SET publish_phase='PUBLISH_OUTCOME_UNKNOWN'")
        p=read().processing
        self.assertIsNone(p['download']['downloaded_bytes']);self.assertIsNone(p['next_at']);self.assertEqual('RECONCILE',p['next_step'])
        with self.repo.connection(write=True) as db:
            s=json.loads(db.execute("SELECT snapshot FROM plans WHERE id='p6'").fetchone()[0])
            s['torrent_files'][0]['targets'].append('another-target')
            db.execute("UPDATE plans SET snapshot=? WHERE id='p6'",(json.dumps(s),))
        self.assertTrue(read().processing['download']['shared_file'])
        key=read().target_key
        with self.repo.connection(write=True) as db:
            s['torrent_files'][1]['targets'].append(key)
            db.execute("UPDATE plans SET snapshot=? WHERE id='p6'",(json.dumps(s),))
        self.assertIsNone(read().processing['quality'])
        with self.repo.connection(write=True) as db:db.execute("UPDATE plans SET task_generation=task_generation+1 WHERE id='p6'")
        self.assertIsNone(read().processing)

    def test_policy_fallback_tier_does_not_become_a_known_technical_spec(self):
        p=load('policy');policy=p.Policy({'tv':'欧美剧'},1)
        facts=policy.normalize({'title':'Example 2160p WEB-DL -HHWEB 中文字幕','description':'','labels':[],'subtitle_description':''})
        summary=p.quality_facts(facts)
        self.assertEqual(0,facts.audio);self.assertIsNone(summary['audio']);self.assertIsNone(summary['picture'])

    def test_ignored_legacy_attachments_do_not_count_as_required_assets(self):
        self.populate_processing()
        with self.repo.connection(write=True) as db:
            snapshot=json.loads(db.execute("SELECT snapshot FROM plans WHERE id='p6'").fetchone()[0])
            snapshot['torrent_files'].append({'index':99,'path':'fonts/readme.ttf','size':50,'role':'other','targets':[snapshot['selected_indices'] and next(iter(snapshot['targets']))],'requires':[]})
            snapshot['selected_indices'].append(99)
            db.execute("UPDATE plans SET snapshot=? WHERE id='p6'",(json.dumps(snapshot),))
        processing=self.views.task(self.task_id,user=None).units.items[0].processing
        self.assertEqual(1,processing['download']['file_count'])
        self.assertEqual(0,processing['attachments']['required'])

    def test_rt14_legacy_dependencies_and_image_subtitles_stay_out_of_display_totals(self):
        self.populate_processing(size=100)
        with self.repo.connection(write=True) as db:
            snapshot=json.loads(db.execute("SELECT snapshot FROM plans WHERE id='p6'").fetchone()[0])
            key=next(iter(snapshot['targets']));video=next(file for file in snapshot['torrent_files'] if file['role']=='video')
            video['requires']=[98]
            snapshot['torrent_files'] += [
                {'index':98,'path':'fonts/effect.ttf','size':50,'role':'other','targets':[key],'requires':[]},
                {'index':99,'path':'subtitles/legacy.idx','size':25,'role':'subtitle','targets':[key],'requires':[]},
            ]
            snapshot['selected_indices'] += [98,99]
            db.execute("UPDATE plans SET snapshot=? WHERE id='p6'",(json.dumps(snapshot),))
        value=self.views.task(self.task_id,user=None).units.items[0].processing
        self.assertEqual(1,value['download']['file_count'])
        self.assertEqual(100,value['download']['total_bytes'])
        self.assertEqual(0,value['attachments']['required'])

    def test_unit_reports_known_version_count(self):
        self.populate_processing()
        unit=self.views.task(self.task_id,user=None).units.items[0]
        self.assertEqual(0,unit.version_count)

    def test_rt12_attention_filter_runs_before_episode_pagination(self):
        self.populate()
        with self.repo.connection(write=True) as db:
            rows=[]
            for episode in range(1,51):
                key=json.dumps(['电视剧','themoviedb','42',1,'',episode],ensure_ascii=False,separators=(',',':'))
                rows.append((key,self.task_id,key,'PUBLISH_OUTCOME_UNKNOWN' if episode==40 else 'NOT_SENT'))
            db.executemany('INSERT INTO target_units(target_key,task_id,identity,publish_phase) VALUES(?,?,?,?)',rows)
        page=self.views.task(self.task_id,activity='attention',limit=25,user=None).units
        self.assertEqual(1,page.total)
        self.assertEqual(40,json.loads(page.items[0].target_key)[5])
        self.assertIsNone(page.next_offset)

    def test_rt13_episode_pages_are_stable_and_filter_reset_restores_all_units(self):
        self.populate()
        with self.repo.connection(write=True) as db:
            rows=[]
            for episode in range(1,51):
                key=json.dumps(['电视剧','themoviedb','42',1,'',episode],ensure_ascii=False,separators=(',',':'))
                rows.append((key,self.task_id,key,'PUBLISH_OUTCOME_UNKNOWN' if episode in (20,40) else 'NOT_SENT'))
            db.executemany('INSERT INTO target_units(target_key,task_id,identity,publish_phase) VALUES(?,?,?,?)',rows)
        first=self.views.task(self.task_id,activity='all',limit=25,offset=0,user=None).units
        second=self.views.task(self.task_id,activity='all',limit=25,offset=25,user=None).units
        attention=self.views.task(self.task_id,activity='attention',limit=25,user=None).units
        self.assertEqual(list(range(1,26)),[json.loads(row.target_key)[5] for row in first.items])
        self.assertEqual(list(range(26,51)),[json.loads(row.target_key)[5] for row in second.items])
        self.assertEqual([20,40],[json.loads(row.target_key)[5] for row in attention.items])
        self.assertEqual(50,first.total)

    def test_delivery_projection_hides_ignored_assets_and_keeps_legacy_unknown_files(self):
        self.populate_processing()
        with self.repo.connection(write=True) as db:
            data=json.loads(db.execute("SELECT data FROM delivery_bundles WHERE id='b'").fetchone()[0])
            data['files']=[
                {'file_index':0,'snapshot':{'path':'Example.mkv'},'state':'PENDING'},
                {'file_index':1,'snapshot':{'path':'Example.srt'},'state':'VERIFIED'},
                {'file_index':2,'snapshot':{'path':'Example.idx'},'state':'PENDING'},
                {'file_index':3,'snapshot':{'path':'Example.sub'},'state':'PENDING'},
                {'file_index':4,'snapshot':{'path':'font.ttf'},'state':'PENDING'},
            ]
            data['manifest']['assets']=[
                {'file_index':0,'role':'video','required':True},
                {'file_index':1,'role':'subtitle','required':True},
                {'file_index':2,'role':'subtitle','required':False},
                {'file_index':3,'role':'subtitle','required':False},
                {'file_index':4,'role':'other','required':False},
            ]
            db.execute("UPDATE delivery_bundles SET data=? WHERE id='b'",(json.dumps(data),))
        detail=self.views.bundle('b',user=None)
        self.assertEqual(2,detail.files.total)
        self.assertEqual('video',detail.files.items[0].data['role'])
        self.assertEqual('subtitle',detail.files.items[1].data['role'])
        self.assertEqual(2,detail.bundle.file_count)
        with self.repo.connection(write=True) as db:
            data.pop('manifest')
            db.execute("UPDATE delivery_bundles SET data=? WHERE id='b'",(json.dumps(data),))
        legacy=self.views.bundle('b',user=None)
        self.assertEqual(5,legacy.files.total)
        self.assertEqual(5,legacy.bundle.file_count)

    def test_rt15_hidden_unknown_attachment_remains_an_explicit_reconcile_barrier(self):
        self.populate_processing()
        with self.repo.connection(write=True) as db:
            data=json.loads(db.execute("SELECT data FROM delivery_bundles WHERE id='b'").fetchone()[0])
            data['files']=[
                {'file_index':0,'snapshot':{'path':'Example.mkv'},'state':'VERIFIED'},
                {'file_index':9,'snapshot':{'path':'font.ttf'},'state':'UNKNOWN'},
            ]
            data['manifest']['assets']=[
                {'file_index':0,'role':'video','required':True},
                {'file_index':9,'role':'other','required':False},
            ]
            db.execute("UPDATE delivery_bundles SET state='WAITING',data=? WHERE id='b'",(json.dumps(data),))
        detail=self.views.bundle('b',user=None)
        self.assertEqual(1,detail.files.total)
        self.assertEqual(1,detail.bundle.hidden_unsettled_count)


if __name__=='__main__':
    import sys
    if '--fixture' in sys.argv:
        DisplayTests.setUpClass();test=DisplayTests();test.setUp()
        try:
            test.populate_processing(size=2147483648);page=test.views.decisions(task_id=test.task_id,user=None)
            unit=lambda:test.views.task(test.task_id,user=None).model_dump(mode='json')
            scenes={'rapid':unit()}
            with test.repo.connection(write=True) as db:
                db.execute("UPDATE delivery_bundles SET state='CANCELLED'")
                db.execute("UPDATE plan_targets SET transfer_phase='DOWNLOADING'")
            scenes['downloading']=unit()
            with test.repo.connection(write=True) as db:db.execute("UPDATE plan_targets SET transfer_phase='WAITING_ASSETS'")
            scenes['assets']=unit()
            with test.repo.connection(write=True) as db:db.execute("UPDATE target_units SET publish_phase='PUBLISH_OUTCOME_UNKNOWN'")
            scenes['unknown']=unit()
            with test.repo.connection(write=True) as db:
                db.execute("UPDATE plans SET authorization='SUPERSEDED' WHERE id='p6'")
                db.execute("UPDATE target_units SET generation=3")
            scenes['superseded']=unit()
            value={'list':page.model_dump(mode='json'),'detail':test.views.decision(page.items[0].id,user=None).model_dump(mode='json'),'scenes':scenes}
            text=json.dumps(value,ensure_ascii=False,indent=2)
            if '--output' in sys.argv:
                from pathlib import Path
                Path(sys.argv[sys.argv.index('--output')+1]).write_text(text,encoding='utf-8')
            else:print(text)

        finally:test.doCleanups()
    else:unittest.main()
