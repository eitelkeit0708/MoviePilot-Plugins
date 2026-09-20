"""Full-asset authority and physical download references in mixed episode plans."""
import json,unittest
from pathlib import Path
from test_planner import load

def mixed_target_cohort(change=None):
 import test_planner as p
 from datetime import timedelta
 from types import SimpleNamespace
 p.AuthorityTests.setUpClass();f=p.AuthorityTests('test_atomic_claim_before_callbacks_two_connections');f.setUp()
 p.PlanSelectionTests.setUpClass();g=p.PlanSelectionTests('test_unknown_or_provider_error_never_becomes_acquire');g.setUp()
 try:
  import tempfile
  local=tempfile.TemporaryDirectory(dir=Path.cwd());f.addCleanup(local.cleanup)
  g.keys=f.keys;root=Path(local.name);candidate=g.candidate();candidate['save_path']=root.as_posix()[2:] if root.drive else root.as_posix()
  candidate['torrent_files'].append(dict(index=2,path='E7.original.srt',size=3,role='subtitle',targets=[f.keys[0]],requires=[]))
  for item in candidate['torrent_files']:(root/item['path']).write_bytes(b'x'*item['size'])
  missing={k:dict(state='MISSING',revision=0,versions=[]) for k in f.keys}
  initial=g.planner.evaluate(candidate,missing,f.keys)['plans'][0]
  f.auth.set_revisions(g.policy.semantic_hash,'parse-1');f.auth.prepare('original','round',initial)
  for key in f.keys:f.auth.update_current(key,dict(state='PRESENT',evidence_ref='fixture'),expected_revision=0)
  current={k:dict(state='PRESENT',revision=1,versions=[g.p.Version('version:'+str(i),g.facts('2160p',True))],sidecar_missing=True) for i,k in enumerate(f.keys)}
  em=load('execution');now=f.s.instant();assets=[]
  for i,key in enumerate(f.keys,3):
   path=root/('E'+str(i+4)+'.late.srt');path.write_bytes(b'late')
   strong,sha=em.asset_hashes(path)
   assets.append(dict(file=dict(index=i,path=path.name,size=4,role='subtitle',targets=[key],requires=[]),sha256=strong,sha1=sha,mtime_ns=path.stat().st_mtime_ns,stable_since=(now-timedelta(seconds=2)).isoformat(),observed_at=now.isoformat()))
  candidate.update(source_plan='original',local_assets=assets,same_video_verified={k:True for k in f.keys})
  planned=g.planner.evaluate(candidate,current,f.keys);snapshot=planned['plans'][0]
  f.auth.prepare('mixed','round',snapshot);f.auth.claim('mixed',f.auth.vector(f.keys))
  with f.repo.connection(write=True) as db:
   db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,client_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(candidate['downloader'],candidate['infohash'],candidate['save_path'],json.dumps([[x['path'],x['size']] for x in candidate['torrent_files']]),'owned','original','RUNNING','owned',f.r.utcnow()))
  calls=[];state=['COMPLETED'];wanted={0,1,2}
  def pause(tid):calls.append('pause');state[0]='PAUSED';return True
  def select(tid,ids,enabled):
   calls.append(['select',ids,enabled])
   if enabled:wanted.update(i-10 for i in ids)
   else:wanted.difference_update(i-10 for i in ids)
   return None if change=='unknown' else True
  client=SimpleNamespace(task=lambda ih:dict(id='owned',infohash=ih,save_path=candidate['save_path'],state=state[0]),files=lambda ih:[dict(id=x['index']+10,path=x['path'],size=x['size'],wanted=x['index'] in wanted,completed=x['size']) for x in candidate['torrent_files']],pause=pause,select_files=select)
  def revalidate(plan):
   assert plan['snapshot']==g.planner.evaluate(candidate,current,f.keys)['plans'][0]
   return candidate
  executor=em.StrictExecutor(f.repo,lambda name:client,revalidate=revalidate,verify_torrent=lambda content:(candidate['infohash'],[(x['path'],x['size']) for x in candidate['torrent_files']]))
  begin=executor.authority.begin_shared_attempt;changes=[]
  def changing_begin(*args,**kw):
   if change:changes.append(change)
   if change=='current':f.auth.update_current(f.keys[1],dict(state='PRESENT',evidence_ref='changed'),expected_revision=1)
   elif change in ('pause','generation'):
    with f.repo.connection(write=True) as db:
     if change=='pause':db.execute("UPDATE tasks SET state='PAUSED' WHERE id=?",(f.task_id,))
     else:db.execute('UPDATE target_units SET generation=generation+1 WHERE target_key=?',(f.keys[1],))
   return begin(*args,**kw)
  executor.authority.begin_shared_attempt=changing_begin
  result=executor.execute('mixed',b'fixture')
  if change=='unknown':
   original=dict(result);before=list(calls)
   recovery=executor.reconcile('mixed');result=executor.execute('mixed',b'fixture')
   result.update(original=original,recovery=recovery,no_replay=calls==before)
  if result['state']=='RUNNING':
   import shutil
   output=root/'organized';output.mkdir()
   host=SimpleNamespace(history=lambda *a:True,transfer=lambda source,destination,item,snapshot:shutil.copyfile(source,destination/source.name))
   result.update(organization=em.Organizer(executor,host).organize('mixed',output),outputs=sorted(p.name for p in output.iterdir()),wanted=sorted(wanted))
  refs=f.auth.download_references(candidate['downloader'],candidate['infohash'],candidate['save_path'])
  return dict(selected=snapshot['selected_indices'],plan_targets=list(snapshot['targets']),physical_reference_indices=refs[0]['indices'],physical_reference_targets=list(refs[0]['vector']),actions={k:v['action'] for k,v in snapshot['targets'].items()},execution=result,calls=calls,changes=changes)
 finally:f.doCleanups();g.doCleanups()


class MixedTargetTests(unittest.TestCase):
 def test_mixed_targets_select_only_real_subtitle_and_organize_all_subtitles(self):
  result=mixed_target_cohort();self.assertEqual([2,3,4],result['selected'])
  self.assertEqual(2,len(result['plan_targets']));self.assertEqual([2],result['physical_reference_indices']);self.assertEqual(1,len(result['physical_reference_targets']))
  actual=result['execution'];self.assertEqual('RUNNING',actual['state'],actual)
  self.assertEqual('COMPLETE',actual['organization']['state']);self.assertEqual(['E7.late.srt','E7.original.srt','E8.late.srt'],actual['outputs']);self.assertEqual([2],actual['wanted'])
  self.assertEqual('pause',result['calls'][0]);self.assertTrue(all(set(c[1])<={10,11,12} for c in result['calls'] if isinstance(c,list)))
 def test_local_only_target_current_change_before_transaction_blocks_all_rpc(self):
  result=mixed_target_cohort('current');self.assertEqual(['current'],result['changes']);self.assertEqual('BLOCKED',result['execution']['state']);self.assertEqual([],result['calls'])
 def test_task_pause_before_transaction_blocks_all_rpc(self):
  result=mixed_target_cohort('pause');self.assertEqual(['pause'],result['changes']);self.assertEqual('BLOCKED',result['execution']['state']);self.assertEqual([],result['calls'])
 def test_local_only_target_generation_change_before_transaction_blocks_all_rpc(self):
  result=mixed_target_cohort('generation');self.assertEqual(['generation'],result['changes']);self.assertEqual('BLOCKED',result['execution']['state']);self.assertEqual([],result['calls'])
 def test_unknown_physical_selection_settles_by_exact_readback_without_replay(self):
  result=mixed_target_cohort('unknown')['execution']
  self.assertEqual('SELECTION_OUTCOME_UNKNOWN',result['original']['reason']);self.assertTrue(result['no_replay'])
  self.assertEqual('RUNNING',result['state']);self.assertEqual('COMPLETE',result['organization']['state'])

if __name__=='__main__':unittest.main()
