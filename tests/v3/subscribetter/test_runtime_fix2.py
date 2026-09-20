"""Reachable mixed-subtitle and per-call deadline regressions for W10A2 Fix2."""
import json,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_planner import load

def cleanup_deadline(slow='task',scope='downloader_task'):
 import test_delivery as d, test_planner as p
 from datetime import timedelta
 from types import SimpleNamespace
 d.DeliveryTests.setUpClass();f=d.DeliveryTests('test_downloader_cleanup_durable_guard_blocks_new_send');f.setUp()
 try:
  f.rule['remove_downloader_task_enabled']=True
  f.rule['delete_downloader_data_enabled']=True
  f.worker=f.m.Delivery(f.repo,f.auth,None,f.cloud,rules=[f.rule],revalidate=lambda plan:f.publication)
  bid=f.all_remote();f.worker.publish(bid,now=p.NOW+timedelta(minutes=5));s=f.auth.plan('A')['snapshot']
  with f.repo.connection(write=True) as db:db.execute('INSERT INTO managed_downloads VALUES(?,?,?,?,?,?,?,?,?,?)',(s['downloader'],s['infohash'],s['save_path'],'[]','owned','add','client','RUNNING','{}',f.s.stamp(p.NOW)))
  Runtime=load('runtime').Runtime
  calls=[];removed=[];began=time.monotonic();deadline=began+.1
  def task(ih):
   calls.append(dict(stage='task',elapsed=round(time.monotonic()-began,3)))
   if removed:return None
   if slow=='task':time.sleep(.15)
   if scope=='downloader_data':return None
   return dict(id='client',save_path=s['save_path'],markers=['owned'])
  def files(ih):
   calls.append(dict(stage='files',elapsed=round(time.monotonic()-began,3)))
   if slow=='files':time.sleep(.15)
   return [dict(id=x['index'],path=x['path'],size=x['size']) for x in f.files]
  def remove(tid):
   calls.append(dict(stage='REMOVE',elapsed=round(time.monotonic()-began,3)));removed.append(tid)
   if slow=='remove':time.sleep(.15)
  f.worker.dispatch_gate=lambda:Runtime.checkpoint(deadline)
  f.repo.setting('delivery-maintenance',dict(rule='',bundle='',scope=2 if scope=='downloader_task' else 3))
  with patch.object(load('execution').ConfiguredDownloader,'named',return_value=SimpleNamespace(task=task,files=files,remove=remove)),patch.object(f.m.LocalReconciler,'scan',return_value={}):
   result=f.worker.local_maintenance(entries=10,deadline=deadline)
  return dict(budget_seconds=.1,calls=calls,result=result)
 finally:f.doCleanups()

def recognition_deadline(slow='parse',direct=False):
 import test_runtime as t
 from types import SimpleNamespace
 f=t.CommonAdmissionTests('test_manual_native_discovery_converge_and_freeze_real_config_separate_from_restore');f.setUp()
 try:
  cm=load('candidates');calls=[]
  def parse(*args,**kw):
   calls.append('parse')
   if slow=='parse':time.sleep(.15)
   return SimpleNamespace(status='OK',meta=object(),record=lambda:{})
  def recognize(*args):calls.append('PROVIDER_AFTER_DEADLINE');return object()
  service=cm.CandidateService(f.repo,SimpleNamespace(recognize=recognize,identity=lambda media:('themoviedb','42')))
  if slow=='assist':
   def assist(title,description,correction,**kw):calls.append('assist');time.sleep(.15);return correction,None
   service.ai=SimpleNamespace(assist=assist)
  record=service.observe(dict(site=1,torrent_id='x',title='Fixture',description='',labels=[]))
  meta=SimpleNamespace(parse=parse,corrector=object())
  pipeline=cm.CandidatePipeline(service,meta,f.runtime.policy,lambda keys:{},lambda name:object(),current=f.runtime.check)
  f.runtime.deadline=time.monotonic()+.1
  if direct:service.deadline=f.runtime.deadline
  try:
   if direct:service.recognize(record['candidate_key'],f.target,meta)
   else:pipeline.evaluate(record['candidate_key'],f.target,[f.p.TargetUnit(f.target).key],downloader='qb',save_path='/downloads')
  except ValueError as error:reason=str(error)
  return dict(reason=reason,calls=calls)
 finally:f.doCleanups()

def late_assets_chain(bad_readback=False):
 import test_runtime as t, test_archive as a
 from types import SimpleNamespace
 from datetime import timedelta
 f=t.CommonAdmissionTests('test_manual_native_discovery_converge_and_freeze_real_config_separate_from_restore');f.setUp()
 a.ArchiveTests.setUpClass();g=a.ArchiveTests('test_confirm_all_assets_atomically_then_duplicate_never_writes');g.setUp()
 try:
  # Real SQLite archive publication/confirmation, fixture SDK boundary only.
  g.repo=f.repo;g.archive=g.m.Archive(g.repo,g.policy,g.sources,mappings=[g.mapping])
  g.publication(video_requires=[]);g.archive.confirm_ingest('pub',g.manifest,g.consumer)
  cm=load('candidates');am=load('runtime_assets');snapshot=g.archive.authority.plan('plan')['snapshot'];key=g.key
  source_dir=g.root/'source';source_dir.mkdir();(source_dir/'movie.mkv').write_bytes(b'x'*100);(source_dir/'movie.srt').write_bytes(b'x'*5);(source_dir/'movie.late.srt').write_bytes(b'late subtitle')
  raw=dict(site=1,torrent_id='42',title='2160p REMUX 中文字幕',description='',labels=[])
  class Meta:
   corrector=SimpleNamespace(revision='parse')
   def parse(self,*args,**kw):return SimpleNamespace(status='OK',meta=object(),record=lambda:{})
  adapter=SimpleNamespace(sites=lambda:[dict(id=1)],page_size=lambda *a:None,search=lambda *a:[raw],acquire=lambda raw:b'fixture',recognize=lambda *a:object(),identity=lambda media:('themoviedb','42'),classify=lambda media:g.sources.classify_target(key))
  service=cm.CandidateService(f.repo,adapter)
  # Original publication fixture used key 'release'; supply exact persisted key
  # through the service observe boundary without changing any frozen plan.
  service.observe(raw)
  realkey=next(iter(service.runtime));stored=service.records()[0]
  with f.repo.connection(write=True) as db:
   data=db.execute('SELECT data FROM candidates WHERE candidate_key=?',(realkey,)).fetchone()[0]
   db.execute('INSERT INTO candidates VALUES(?,?,?,?)',('release',data,'now','now'))
  service.runtime['release']=raw
  service.refresh=lambda key,budget:service.runtime.update({key:raw})
  f.plugin.config.destination_templates[0].save_path='/download';f.plugin.config.destination_templates[0].downloader='test'
  f.plugin.meta_service=Meta();f.plugin.candidates=service
  f.plugin.delivery_worker=SimpleNamespace(rules={'rule':{'enabled':True,'stable_seconds':1}},archive=g.archive)
  runtime=f.m.Runtime(f.plugin,provider=f.provider,clients=lambda name:object())
  evaluated=[];original_evaluate=runtime.evaluate
  def capture(*args):
   result=original_evaluate(*args);evaluated.append(result);return result
  runtime.evaluate=capture
  row=f.repo.get_task(g.task['id']);scope=runtime.scope(f.target);template=runtime.destination(scope);effective=runtime.effective(template,'ONESHOT')
  saved=dict(task_id=row['id'],task_generation=row['generation'],opportunity_id='o',scope=scope,effective=effective,config_digest=f.m.digest(effective),configuration_revision=1,created_at=f.r.utcnow(),planner_mode='episode')
  f.repo.setting('runtime-input:o',saved)
  def projected_path(value):return source_dir if str(value)=='/download' else Path(value)
  now=am.instant();out=[]
  with patch.object(am,'Path',side_effect=projected_path),patch.dict(runtime.pipeline.evaluate.__func__.__globals__,{'torrent_table':lambda content:(snapshot['infohash'],[(x['path'],x['size']) for x in snapshot['torrent_files']])}):
   with patch.object(am,'instant',return_value=now):out.append(runtime.late_assets(time.monotonic()+5))
   f.repo.setting('runtime-subtitle-cursor','')
   with patch.object(am,'instant',return_value=now+timedelta(seconds=2)):out.append(runtime.late_assets(time.monotonic()+5))
  with f.repo.connection() as db:opps=[dict(row) for row in db.execute('SELECT id,mode,state,scope FROM opportunities')]
  candidate=runtime.pipeline.rounds['release']['candidate'];proof=g.archive.candidate_evidence(candidate,[key])
  if out[-1]['state']=='SUBTITLE_QUEUED':
   # The improvement was observed under the completed source opportunity.
   # The real next tick evaluates again under the newly admitted opportunity;
   # its immutable decision receipt must belong to that exact opportunity.
   with patch.dict(runtime.pipeline.evaluate.__func__.__globals__,{'torrent_table':lambda content:(snapshot['infohash'],[(x['path'],x['size']) for x in snapshot['torrent_files']])}):
    fresh=runtime.evaluate(f.repo.setting('runtime-input:'+out[-1]['opportunity_id']),'release')
   snap=fresh['plans'][0];auth=runtime.authority
   runtime.scheduler.observe(out[-1]['opportunity_id'],key,'release',snap['targets'][key]['quality'],eligible=True)
   auth.prepare('late-sidecar',out[-1]['opportunity_id'],snap);auth.claim('late-sidecar',auth.vector([key]),now=f.s.instant()+timedelta(seconds=100))
   with f.repo.connection(write=True) as db:
    db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,client_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',('test',snapshot['infohash'],'/download',json.dumps([[x['path'],x['size']] for x in snapshot['torrent_files']]),'owned','original-add','RUNNING','owned',f.r.utcnow()))
   em=load('execution');calls=[];wanted={10,11};state=['COMPLETED']
   def pause(tid):calls.append(('pause',));state[0]='PAUSED';return True
   def select(tid,ids,enabled):
    calls.append(('select',list(ids),enabled))
    if not bad_readback:
     if enabled:wanted.update(ids)
     else:wanted.difference_update(ids)
    return True
   def forbidden(*a):calls.append(('FORBIDDEN',));raise AssertionError('no video add/resume')
   client=SimpleNamespace(task=lambda ih:dict(id='owned',infohash=ih,save_path='/download',state=state[0]),files=lambda ih:[dict(id=x['index']+10,path=x['path'],size=x['size'],wanted=x['index']+10 in wanted,completed=x['size']) for x in snapshot['torrent_files']],pause=pause,select_files=select,add=forbidden,resume=forbidden)
   runtime.pipeline.clients=lambda name:client;executor=runtime.pipeline.executor();executor.verify_torrent=lambda body:(snapshot['infohash'],[(x['path'],x['size']) for x in snapshot['torrent_files']])
   with patch.object(em,'Path',side_effect=projected_path),patch.dict(runtime.pipeline.revalidate.__func__.__globals__,{'Path':projected_path}):
    try:execution=executor.execute('late-sidecar',b'fixture')
    except ValueError as error:execution=dict(error=str(error))
    if execution['state']=='RUNNING':
     import shutil
     output=source_dir/'organized';output.mkdir()
     def transfer(source,destination,item,snapshot):calls.append(('transfer',source.name));return shutil.copyfile(source,destination/source.name)
     host=SimpleNamespace(history=lambda *a:True,transfer=transfer)
     execution.update(organization=em.Organizer(executor,host).organize('late-sidecar',output),outputs=sorted(p.name for p in output.iterdir()))
    execution.update(calls=calls,wanted=sorted(wanted))
  else:execution=None
  return dict(stages=out,opportunities=opps,source_authorization=runtime.authority.plan('plan')['authorization'],selected_indices=[x['selected_indices'] for y in evaluated for x in y['plans']],same_video=proof['same_video_verified'],execution=execution)
 finally:g.doCleanups();f.doCleanups()

class RuntimeFix2Tests(unittest.TestCase):
 def test_late_sidecar_with_original_torrent_subtitle_reaches_organizer(self):
  result=late_assets_chain()
  self.assertEqual(['WAITING_ASSETS','SUBTITLE_QUEUED'],[x['state'] for x in result['stages']])
  self.assertEqual([[1,2],[1,2]],result['selected_indices']);self.assertEqual('COMPLETED',result['source_authorization'])
  actual=result['execution'];self.assertEqual('RUNNING',actual['state'],actual)
  self.assertEqual('COMPLETE',actual['organization']['state']);self.assertEqual(['movie.late.srt','movie.srt'],actual['outputs'])
  self.assertEqual([11],actual['wanted']);self.assertNotIn(('FORBIDDEN',),actual['calls'])
  self.assertTrue(all(set(c[1])<={10,11} for c in actual['calls'] if c[0]=='select'))
 def test_cleanup_deadline_blocks_files_and_remove_after_slow_task(self):
  result=cleanup_deadline();self.assertEqual(['task'],[c['stage'] for c in result['calls']])
  self.assertEqual('TICK_DEADLINE',result['result']['cleanup'][0]['reason'])
 def test_recognition_deadline_blocks_provider_after_slow_parse(self):
  result=recognition_deadline();self.assertEqual(['parse'],result['calls']);self.assertEqual('TICK_DEADLINE',result['reason'])
 def test_mixed_sidecar_wanted_mismatch_still_blocks_organization(self):
  actual=late_assets_chain(bad_readback=True)['execution']
  self.assertEqual('BLOCKED',actual['state']);self.assertEqual('WANTED_READBACK_MISMATCH',actual['reason'])
  self.assertFalse(any(c[0] in ('transfer','FORBIDDEN') for c in actual['calls']))
 def test_cleanup_slow_files_suppresses_remove_and_issued_slow_remove_stays_unknown(self):
  result=cleanup_deadline('files');self.assertEqual(['task','files'],[c['stage'] for c in result['calls']])
  self.assertEqual('TICK_DEADLINE',result['result']['cleanup'][0]['reason'])
  result=cleanup_deadline('remove');self.assertEqual(['task','files','REMOVE'],[c['stage'] for c in result['calls']])
  self.assertEqual('DOWNLOADER_REMOVE_UNKNOWN',result['result']['cleanup'][0]['reason'])
 def test_recognition_checks_after_assist_and_direct_service_deadline(self):
  result=recognition_deadline('assist');self.assertEqual(['parse','assist'],result['calls']);self.assertEqual('TICK_DEADLINE',result['reason'])
  result=recognition_deadline(direct=True);self.assertEqual(['parse'],result['calls']);self.assertEqual('TICK_DEADLINE',result['reason'])
 def test_data_cleanup_stops_after_slow_absent_task_read(self):
  result=cleanup_deadline(scope='downloader_data');self.assertEqual(['task'],[c['stage'] for c in result['calls']])
  self.assertEqual('TICK_DEADLINE',result['result']['cleanup'][0]['reason'])

if __name__=='__main__':unittest.main()
