"""Quality regressions: ordinary I/O fencing and surviving target execution."""
import json,time,threading,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import contextmanager
from unittest.mock import patch
from test_planner import load

def fixture():
 import test_runtime as t
 f=t.CommonAdmissionTests('test_manual_native_discovery_converge_and_freeze_real_config_separate_from_restore');f.setUp()
 sources=NS(scopes={'scope':{'allowed_prefixes':['/safe']}})
 f.plugin.delivery_worker.archive.sources=sources
 f.runtime=f.m.Runtime(f.plugin,provider=f.provider,clients=lambda name:object())
 f.runtime.deadline=time.monotonic()+10
 return f,sources

def retire_start():
 f,sources=fixture();calls=[]
 try:
  cloud=load('delivery_cloud').HostDeliveryCloud(sources)
  channel=NS(cancel=lambda:calls.append('channel_cancel'))
  def subscribe(*a,**kw):
   calls.append('RemoteUploadChannel');f.runtime.stages.retire();f.plugin.generation+=1
   return channel
  def start(req,**kw):calls.append('StartRemoteUpload_AFTER_RETIRE');return NS(upload_id='fixture-upload')
  pb=NS(RemoteUploadChannelRequest=lambda **kw:NS(**kw),StartRemoteUploadRequest=lambda **kw:NS(**kw))
  sources._client=lambda *a:(NS(stub=NS(RemoteUploadChannel=subscribe,StartRemoteUpload=start)),pb,[])
  source=NS(check=lambda:None,size=1,md5='0'*32,sha1='0'*40)
  with f.runtime.stages.lease():
   f.runtime.check()
   try:result=cloud.start('scope','/safe/movie',source,device_id='fixture-device',budget=1)
   except ValueError as error:result=str(error)
  return dict(result=result,calls=calls,retired=f.runtime.stages.retired)
 finally:f.doCleanups()

def disabled_body():
 f,sources=fixture();calls=[]
 try:
  cloud=load('delivery_cloud').HostDeliveryCloud(sources)
  class Channel:
   def __iter__(self):
    calls.append('CHANNEL_WAIT');f.plugin._ordinary_work_active=lambda:False
    yield NS(upload_id='fixture-upload',WhichOneof=lambda _: 'read_data',read_data=NS(offset=0,length=1,lazy_read=False))
   def cancel(self):calls.append('channel_cancel')
  def body(req,**kw):calls.append('RemoteReadData_AFTER_DISABLE');return NS(success=True,bytes_received=1,is_last_chunk=True)
  pb=NS(RemoteUploadChannelRequest=lambda **kw:NS(**kw),RemoteReadDataUpload=lambda **kw:NS(**kw))
  sources._client=lambda *a:(NS(stub=NS(RemoteUploadChannel=lambda *a,**kw:Channel(),RemoteReadData=body)),pb,[])
  source=NS(size=1,read=lambda *a:b'x',stop=threading.Event())
  with f.runtime.stages.lease():
   f.runtime.check()
   try:result=cloud.pump('scope','fixture-upload','fixture-device',source,budget=5)
   except ValueError as error:result=str(error)
  return dict(result=result,calls=calls)
 finally:f.doCleanups()

def retired_search():
 f,sources=fixture();calls=[]
 try:
  c=load('candidates')
  def size(*args):calls.append('page_size');f.runtime.stages.retire();return 1
  def search(*args):calls.append('search_AFTER_RETIRE');return []
  service=c.CandidateService(f.repo,NS(sites=lambda:[dict(id=1)],page_size=size,search=search))
  f.plugin.candidates=service;f.runtime=f.m.Runtime(f.plugin,provider=f.provider,clients=lambda name:object())
  service.deadline=time.monotonic()+10
  with f.runtime.stages.lease():
   f.runtime.check()
   try:service.search([1],['Fixture'],c.SearchBudget(keywords=1,pages=2,concurrency=1,results=10,requests=2,interval=0))
   except ValueError:pass
  return dict(calls=calls)
 finally:f.doCleanups()

@contextmanager
def partial_fixture(*,supersede=True):
 import test_planner as t
 t.AuthorityTests.setUpClass();f=t.AuthorityTests('test_partial_supersession_preserves_sibling_and_blocks_old_whole_batch');f.setUp()
 t.PlanSelectionTests.setUpClass();q=t.PlanSelectionTests('test_pack_cannot_degrade_but_episode_mode_selects_only_valuable_files');q.setUp()
 try:
  q.keys=f.keys;q.current={k:dict(state='MISSING',revision=0,versions=[]) for k in f.keys}
  f.auth.set_revisions(q.policy.semantic_hash,'parse-1')
  candidate=q.candidate(quality='1080p');old=q.planner.evaluate(candidate,q.current,f.keys)['plans'][0]
  f.auth.prepare('A','round',old,now=t.NOW);f.auth.claim('A',f.auth.vector(f.keys),now=t.NOW)
  newer=q.candidate(key='B',quality='2160p');newer['save_path']='/isolated-b'
  new=q.planner.evaluate(newer,q.current,[f.keys[1]])['plans'][0]
  f.auth.prepare('B','round',new,now=t.NOW)
  if supersede:f.auth.supersede('B',f.auth.vector([f.keys[1]]),reason='better',safe_isolation=True,now=t.NOW)
  # A fresh independent archive observation changes only superseded E08.
  if supersede:f.auth.update_current(f.keys[1],dict(state='PRESENT',evidence_ref='new-independent-scan'),expected_revision=0)
  if supersede:q.current[f.keys[1]]=dict(state='PRESENT',revision=1,versions=[q.p.Version('independent-new-video',q.facts('2160p',True))])
  service=NS(repository=f.repo,adapter=NS(classify=lambda media:q.classification))
  pipeline=load('candidates').CandidatePipeline(service,NS(corrector=NS(revision='parse-1')),q.policy,lambda keys:{k:q.current[k] for k in keys},lambda _:None)
  def refresh(*args):pipeline.rounds['A']=dict(candidate=candidate,media=object(),acquired=time.monotonic(),mode='episode')
  runtime=object.__new__(load('runtime').Runtime);runtime.repository=f.repo;runtime.pipeline=pipeline;runtime.verify_input=lambda saved:None
  runtime.candidates=NS(refresh=refresh);runtime.budget=lambda saved:None
  runtime.evaluate=lambda saved,key:pipeline._evaluate(candidate,f.keys,'episode')
  plan=f.auth.plan('A');refresh()
  pipeline.revalidate(plan)  # Surviving E07 is still safe and exactly unchanged.
  with f.repo.connection(write=True) as db:
   db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,client_id,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
    ('test',candidate['infohash'],'/test',json.dumps([[x['path'],x['size']] for x in candidate['torrent_files']]),'fixture-marker','fixture-add','RUNNING','fixture-id',t.NOW.isoformat()))
  # queuedUP is a real qB state: all wanted bytes are done, seeding is queued.
  em=load('execution')
  qb=em.ConfiguredDownloader(NS(type='qbittorrent',instance=NS(get_torrents=lambda **kw:([dict(hash=candidate['infohash'],save_path='/test',state='queuedUP',tags='fixture-marker')],None))))
  with f.repo.connection(write=True) as db:db.execute('UPDATE managed_downloads SET client_id=?',(candidate['infohash'],))
  client=NS(task=qb.task,
   files=lambda ih:[dict(id=i,path=x['path'],size=x['size'],wanted=i==0,completed=100 if i==0 else 0) for i,x in enumerate(candidate['torrent_files'])])
  executor=em.StrictExecutor(f.repo,lambda _:client,revalidate=pipeline.revalidate,verify_torrent=lambda body:(candidate['infohash'],[(x['path'],x['size']) for x in candidate['torrent_files']]))
  yield locals()
 finally:f.doCleanups();q.doCleanups()

class QualityTests(unittest.TestCase):
 def test_retirement_after_channel_prevents_upload_start(self):
  result=retire_start();self.assertNotIn('StartRemoteUpload_AFTER_RETIRE',result['calls']);self.assertIn('channel_cancel',result['calls'])
 def test_disable_during_channel_wait_prevents_body(self):
  result=disabled_body();self.assertNotIn('RemoteReadData_AFTER_DISABLE',result['calls']);self.assertIn('channel_cancel',result['calls'])
 def test_retirement_after_page_size_prevents_search(self):
  self.assertEqual(['page_size'],retired_search()['calls'])
 def test_partial_cold_recovery_matches_warm_surviving_scope(self):
  with partial_fixture() as d:
   d['pipeline'].rounds.clear();d['runtime'].recover(d['plan'],{})
 def test_queued_up_partial_sample_is_complete_and_records_only_owned_scope(self):
  with partial_fixture() as d:
   result=d['executor'].sample('A');self.assertEqual('COMPLETED',result['status']);self.assertEqual([d['f'].keys[0]],result['scope']);self.assertEqual(['0'],list(result['files']))
 def test_expired_partial_runtime_reaches_organization(self):
  with partial_fixture() as d:
   r=d['runtime'];p=d['pipeline'];calls=[]
   p.rounds['A']['acquired']=time.monotonic()-301;p.executor=lambda:d['executor'];p.execute=lambda *a,**kw:d['executor'].execute('A',b'fixture',resume=True)
   p.organize=lambda *a:calls.append('organize') or dict(state='WAITING');r.delivery=NS(rules={'rule':{'local_root':'/unused'}})
   result=r.advance(d['plan'],dict(effective=dict(template=dict(organized_rule='rule'))))
   self.assertEqual(['organize'],calls);self.assertEqual('WAITING',result['state'])
 def test_explicit_cost_subset_is_not_full_active_progress(self):
  with partial_fixture(supersede=False) as d:
   result=d['executor'].sample('A',targets=[d['f'].keys[0]])
   self.assertEqual([d['f'].keys[0]],result['scope']);self.assertEqual('COMPLETED',result['status'])
   self.assertEqual('QUEUED',d['executor'].sample('A')['status'])
   self.assertEqual([d['f'].keys[0]],d['f'].auth.progress('A',[0])['scope'])
 def test_surviving_changed_resource_or_current_rejected_after_cold_refresh(self):
  for change in ('current','hash','layout'):
   with self.subTest(change=change),partial_fixture() as d:
    if change=='current':d['q'].current[d['f'].keys[0]]=dict(state='PRESENT',revision=1,versions=[d['q'].p.Version('changed',d['q'].facts('2160p',True))])
    elif change=='hash':d['candidate']['infohash']='f'*40
    else:d['candidate']['save_path']='/changed'
    d['pipeline'].rounds.clear()
    with self.assertRaisesRegex(ValueError,'COLD_PLAN_CHANGED'):d['runtime'].recover(d['plan'],{})
 def test_remaining_incomplete_or_unknown_bytes_do_not_complete(self):
  for downloaded in (99,None):
   with self.subTest(downloaded=downloaded),partial_fixture() as d:
    files=d['client'].files
    d['client'].files=lambda ih:[dict(x,completed=downloaded if i==0 else x['completed']) for i,x in enumerate(files(ih))]
    self.assertEqual('QUEUED',d['executor'].sample('A')['status'])
    with self.assertRaisesRegex(ValueError,'EXACT_SAFE_PROGRESS_SCOPE_REQUIRED'):d['executor'].sample('A',targets=[d['f'].keys[1]])
 def test_disabled_safety_reads_cancel_and_observation_cannot_start_body(self):
  f,sources=fixture();self.addCleanup(f.doCleanups);calls=[]
  class Channel:
   def __iter__(self):
    yield NS(upload_id='issued',WhichOneof=lambda _:'read_data',read_data=NS(offset=0,length=1,lazy_read=False))
    yield NS(upload_id='issued',WhichOneof=lambda _:'status_changed',status_changed=NS(status=2))
   def cancel(self):calls.append('cancel-channel')
  pb=NS(RemoteUploadChannelRequest=lambda **kw:NS(**kw),RemoteUploadControlRequest=lambda **kw:NS(**kw),CancelRemoteUpload=lambda:NS())
  sources._client=lambda *a:(NS(stub=NS(RemoteUploadChannel=lambda *a,**kw:Channel(),RemoteUploadControl=lambda *a,**kw:calls.append('cancel-issued'))),pb,[])
  cloud=load('delivery_cloud').HostDeliveryCloud(sources);f.plugin._ordinary_work_active=lambda:False
  with self.assertRaises(ValueError):sources.checkpoint()
  with f.runtime.safety_reads():
   sources.checkpoint()
   result=cloud.pump('scope','issued','device',NS(stop=threading.Event()),cancel=True,observe_only=True)
   self.assertEqual('CANCELLED',result['state']);self.assertEqual(0,result['bytes_sent'])
   with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):cloud.start('scope','/safe/media',NS(check=lambda:None),device_id='unused')
  self.assertIn('cancel-issued',calls)
  with self.assertRaises(ValueError):sources.checkpoint()
 def test_provider_retirement_between_calls_blocks_next_ordinary_stage(self):
  f,sources=fixture();self.addCleanup(f.doCleanups);calls=[]
  def recognize(*args):calls.append('recognize');f.plugin._ordinary_work_active=lambda:False;return NS(type='电影')
  provider=f.m.ScopeProvider(recognize=recognize,identity=lambda media:('themoviedb','42'),classify=lambda media:calls.append('classify'))
  r=f.m.Runtime(f.plugin,provider=provider,clients=lambda name:object())
  with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):r.scope(f.target,fresh=True)
  self.assertEqual(['recognize'],calls)
 def test_rapid_disabled_response_is_unknown_not_miss_and_range_rechecks_gate(self):
  for phase in ('result','range'):
   with self.subTest(phase=phase):
    f,sources=fixture();self.addCleanup(f.doCleanups);cloud=load('delivery_cloud').HostDeliveryCloud(sources)
    def range_hash(*a,**kw):f.plugin._ordinary_work_active=lambda:False;return 'a'*40
    source=NS(check=lambda:None,sha1='a'*40,size=1,range_hash=range_hash)
    def upload(**kw):
     if phase=='range':kw['read_range_bytes_or_hash']('0-0')
     f.plugin._ordinary_work_active=lambda:False
     return dict(state=True,status=1,reuse=False)
    cloud._p115=lambda scope:NS(upload_file_init=upload);cloud._parent=lambda *a:'1'
    with self.assertRaisesRegex(ValueError,'STALE_OR_DISABLED_RUNTIME'):cloud.rapid('scope','/safe/movie',source)
 def test_partial_runtime_organizer_and_delivery_keep_only_surviving_publication(self):
  import tempfile,shutil,copy
  with partial_fixture() as d,tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
   root=Path(tmp);source=root/'source';output=root/'output';source.mkdir();output.mkdir()
   for item in d['candidate']['torrent_files']:(source/item['path']).write_bytes(b'x'*item['size'])
   def local_path(value):
    text=str(value)
    return source/Path(text).relative_to('/test') if text=='/test' or text.startswith('/test/') else Path(value)
   em=d['em'];dm=load('delivery');f=d['f'];q=d['q'];r=d['runtime'];p=d['pipeline']
   archive=NS(policy=q.policy,sources=NS(classify_target=lambda key:q.classification),current=lambda keys:{k:q.current[k] for k in keys})
   rule=dict(id='rule',enabled=True,stable_seconds=0,scan_interval=60,rapid_misses=2,rapid_interval=60,fallback=False,fallback_gb=None,unlimited=False,local_root=str(output),read_roots=[str(output)],cloud_scope_id='cloud',staging_root='/115/staging',incoming_root='/115/incoming',consumer_roots=['/115/incoming'])
   r.delivery=dm.Delivery(f.repo,f.auth,archive,NS(),rules=[rule],publication_validator=lambda plan,pub:dm.PublicationGate(archive,pub)(plan))
   r.authority=f.auth;r.repository=f.repo;r.persist_scope=lambda bid:None
   p.executor=lambda:d['executor'];p.execute=lambda *a,**kw:d['executor'].execute('A',b'fixture',resume=True)
   host=NS(history=lambda *a:True,transfer=lambda src,dest,item,snap:shutil.copyfile(src,dest/src.name))
   p.organize=lambda pid,dest:em.Organizer(d['executor'],host).organize(pid,dest)
   before=copy.deepcopy(f.auth.plan('A')['snapshot'])
   with patch.object(em,'Path',side_effect=local_path):result=r.advance(d['plan'],dict(effective=dict(template=dict(organized_rule='rule'))))
   bundle=r.delivery.bundle(result['bundle_id']);self.assertEqual([0],bundle['indices']);self.assertEqual([f.keys[0]],list(bundle['manifest']['publication']))
   self.assertEqual([d['candidate']['torrent_files'][0]['path']],sorted(x.name for x in output.iterdir()))
   self.assertEqual(before,f.auth.plan('A')['snapshot'])
   for keys in ([f.keys[1]],['unapproved']):
    with self.assertRaises(ValueError):dm.PublicationGate(archive,{key:{} for key in keys})(f.auth.plan('A'))
   unsafe=copy.deepcopy(f.auth.plan('A'));unsafe['snapshot']['torrent_files'][0]['requires']=[1]
   with self.assertRaisesRegex(ValueError,'dependency'):dm.PublicationGate(archive,bundle['manifest']['publication'])(unsafe)

if __name__=='__main__':unittest.main()
