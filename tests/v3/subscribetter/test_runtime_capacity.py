"""Synthetic SQLite capacity evidence; no Emby/NAS/network acceptance implied."""
import json
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from test_planner import load


class CapacityTests(unittest.TestCase):
    def test_fifty_thousand_bounded_finalize_atomic_watermark(self):
        r=load('repository');p=load('policy');s=load('scheduler');a=load('archive')
        started=time.monotonic();count=50000;batch=1000;finalize=[];restarts=[]
        with tempfile.TemporaryDirectory() as directory:
            repo=r.Repository(Path(directory)/'capacity.sqlite3')
            witness=r.Target('电视剧','themoviedb','1',1)
            task=repo.submit('capacity-witness',witness,{},'fixture',1,True)
            repo.complete_handoff(task['id'],task['generation'])
            witness_units=[a.TargetUnit(witness,e) for e in (1,50,100)]
            s.Scheduler(repo).open_opportunity('capacity-witness',task['id'],witness_units,mode='CONTINUOUS',config=s.ScheduleConfig())
            mapping=dict(id='synthetic',revision='1',emby_service='fake',library_id='selected',cloud_scope_id='fake-cloud',
                local_strm_prefix=directory,emby_prefix='/emby',playback_prefix='/playback',cd2_prefix='/cloud',max_strm_bytes=1024)
            calls=[]
            def page(service,library,start,limit):
                calls.append([start,limit])
                items=[]
                for index in range(start,min(count+500,start+limit)):
                    if index<500:items.append(dict(Id='series-'+str(index+1),Type='Series',ProviderIds={'Tmdb':str(index+1)}))
                    else:
                        n=index-500
                        items.append(dict(Id=str(n).zfill(6),Type='Episode',SeriesId='series-'+str(n//100+1),
                            ParentIndexNumber=1,IndexNumber=n%100+1,ProviderIds={'Tmdb':str(n+100000)}))
                return dict(Items=items,TotalRecordCount=count+500)
            def build():
                archive=a.Archive(repo,p.Policy({'tv':'欧美剧'},1),SimpleNamespace(emby_page=page),mappings=[mapping])
                def resolve(service,library,item,**kwargs):
                    key=a.TargetUnit(r.Target('电视剧','themoviedb',item['SeriesId'].split('-')[1],1),item['IndexNumber']).key
                    loc=dict(cloud_scope_id='fake-cloud',path='/cloud/'+item['Id']+'.mkv',
                        sha1=hashlib.sha1(item['Id'].encode()).hexdigest(),size=100)
                    return [dict(version_id=a.digest([key,loc]),target_key=key,service=service,library=library,
                        video=loc,raw={'title':'Fixture 2160p WEB-DL'},reliable=True,observed_at=r.utcnow(),
                        assets=[dict(file_index=-1,location=loc,role='video',required=True)],source_evidence=[])]
                archive.resolve_item=resolve
                return archive
            archive=build();scan_id=None;iterations=0
            while True:
                result=archive.reconcile('fake','selected',scan_id=scan_id,limits=dict(pages=1,page_size=batch,items=batch))
                scan_id=result['scan_id'];iterations+=1
                self.assertNotEqual('ERROR',result['status'],result)
                if 'finalize_processed' in result:
                    finalize.append(result['finalize_processed']);self.assertLessEqual(result['finalize_processed'],batch)
                if result['status']=='COMPLETE':break
                with repo.connection() as db:self.assertEqual(0,db.execute('SELECT COUNT(*) FROM archive_versions').fetchone()[0])
                if iterations%7==0:
                    restarts.append(dict(phase=result['phase'],start=result['start'],finalize_after=result.get('finalize_after')))
                    repo=r.Repository(repo.path);archive=build()
                self.assertLess(iterations,200)
            table_counts={}
            with repo.connection() as db:
                self.assertEqual(count,db.execute('SELECT COUNT(*) FROM archive_versions WHERE active=1').fetchone()[0])
                self.assertEqual(count,db.execute("SELECT COUNT(*) FROM archive_targets WHERE state='PRESENT'").fetchone()[0])
                self.assertEqual('COMPLETE',db.execute('SELECT state FROM archive_scans WHERE id=?',(scan_id,)).fetchone()[0])
                self.assertEqual([],db.execute('PRAGMA foreign_key_check').fetchall())
                for table in ('archive_contents','archive_locations','archive_assets'):
                    table_counts[table]=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                    self.assertEqual(count,table_counts[table])
                for unit in witness_units:
                    row=db.execute('SELECT current_revision,current_facts FROM target_units WHERE target_key=?',(unit.key,)).fetchone()
                    self.assertEqual(1,row['current_revision']);self.assertEqual('PRESENT',json.loads(row['current_facts'])['state'])
                    self.assertEqual(1,len(json.loads(row['current_facts'])['versions']))
            peak=None
            try:
                import resource
                peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            except ImportError:pass
            file_bytes={suffix:(Path(str(repo.path)+suffix).stat().st_size if Path(str(repo.path)+suffix).exists() else 0) for suffix in ('','-wal','-shm')}
            measurements=dict(environment='offline synthetic 500 series x 100 Episode observations / temporary SQLite; resolve stub, no external parsing',count=count,batch=batch,
                collection_calls=len(calls),finalize_processed=finalize,restarts=restarts,final_sql_seconds=result['final_sql_seconds'],
                total_seconds=time.monotonic()-started,peak_rss_platform_units=peak,sqlite_file_bytes=file_bytes,
                table_counts=table_counts,managed_witness_count=len(witness_units),old_visible_until_complete=True)
            print('W10A2_CAPACITY_JSON='+json.dumps(measurements,sort_keys=True))
