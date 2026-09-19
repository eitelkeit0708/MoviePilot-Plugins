"""Explicit synthetic host contract, called by an authenticated local test harness.

No route or timer is registered. This validates downloader/MP mechanics only;
the synthetic identity/admission is never evidence of provider/policy acceptance.
"""
import json
from hashlib import sha256
from pathlib import Path

from .candidates import bind_files, torrent_table
from .execution import ConfiguredDownloader, HostOrganization, Organizer, StrictExecutor, digest, safe_local
from .planner import Authority, TargetUnit, encoded
from .repository import Target
from .scheduler import Scheduler, ScheduleConfig


def run_host_contract(plugin,task_id,manifest,client,phase,episode=2):
    if phase not in ('prepare','resume','sample','organize','reconcile') or client not in ('qb','transmission') or episode not in (1,2):
        raise ValueError('explicit fixture phase/client/episode required')
    if not isinstance(manifest,dict) or manifest.get('synthetic_media') is not True or manifest.get('external_downloads') is not False:
        raise ValueError('synthetic manifest required')
    case=next(c for c in manifest['cases'] if c['client']==client)
    root='/test-data/fixtures/strict-download-20260919'
    if manifest.get('root')!=root or case['torrent_path']!=f'{root}/{client}.torrent' or case['download_save_path']!=f'/test-data/downloads/{client}':
        raise ValueError('fixture paths are fixed')
    content=safe_local(root,case['torrent_path']).read_bytes()
    infohash,table=torrent_table(content)
    if infohash!=case['infohash'] or table!=[(f['path'],f['bytes']) for f in case['files']] or [f['index'] for f in case['files']]!=list(range(len(table))):
        raise ValueError('actual torrent does not match manifest')
    repo=plugin.repository;task=repo.get_task(task_id)
    if not task or task['state']!='ACTIVE' or task['media_type']!='电视剧' or (task['media_source'],task['media_id'],task['season'],task['episode_group'])!=('themoviedb','1396',1,''):
        raise ValueError('explicit active fixture subscription required')
    target=Target.from_task(task);unit=TargetUnit(target,episode)
    files=bind_files(table,target,dependencies={5:[10],10:[0]})
    selected=[f['index'] for f in files if f['targets']==[unit.key]]
    if selected!=[f['index'] for f in case['files'] if f['target']==f'S01E{episode:02}']:
        raise ValueError('manifest dependency mapping mismatch')
    name={'qb':'subscriBetter qB test','transmission':'subscriBetter TR test'}[client]
    pid=f'fixture:{task_id}:{client}:E{episode:02}:{infohash}'
    authority=Authority(repo);revision='synthetic-fixture:'+sha256(encoded(manifest).encode()).hexdigest()
    def gate(plan):
        snap=plan['snapshot']
        if snap['candidate_key']!=pid or snap['policy_revision']!=revision or snap['parse_revision']!=revision or snap['torrent_files']!=files or snap['selected_indices']!=selected or snap['downloader']!=name or snap['save_path']!=case['download_save_path'] or snap['infohash']!=infohash:
            raise ValueError('FIXTURE_PLAN_MISMATCH')
    executor=StrictExecutor(repo,ConfiguredDownloader.named,revalidate=gate)
    if phase=='prepare':
        existing=repo.setting('planner_revisions')
        if existing is not None and existing!=[revision,revision]:
            raise ValueError('fixture must not replace active product policy revisions')
        authority.set_revisions(revision,revision)
        try:
            plan=authority.plan(pid)
        except ValueError:
            opportunity=Scheduler(repo).open_opportunity('fixture-round:'+str(task_id),task_id,[TargetUnit(target,n) for n in (1,2)],mode='ONESHOT',config=ScheduleConfig(observation_enabled=False))
            vector=authority.vector([unit.key])
            snapshot=dict(candidate_key=pid,infohash=infohash,downloader=name,save_path=case['download_save_path'],policy_revision=revision,parse_revision=revision,current={unit.key:dict(state='MISSING',revision=vector[unit.key]['current_revision'])},targets={unit.key:dict(action='ACQUIRE',reason='SYNTHETIC_MECHANICAL_CONTRACT',evidence_keys=[],quality=[1],evidence_source='none')},torrent_files=files,selected_indices=selected,verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
            authority.prepare(pid,opportunity['id'],snapshot)
            authority.claim(pid,vector)
        result=executor.execute(pid,content)
    elif phase=='resume':
        result=executor.resume(pid)
    elif phase=='sample':
        result=executor.sample(pid)
    elif phase=='reconcile':
        result=executor.reconcile(pid)
    else:
        for i in selected:
            source=safe_local(case['download_save_path'],Path(case['download_save_path'])/files[i]['path'])
            if digest(source)!=case['files'][i]['sha256']:
                raise ValueError('fixture completed bytes mismatch')
        from app.sdk.media import MediaInfo
        media=MediaInfo();media.from_dict(dict(type='电视剧',media_source='themoviedb',media_id='1396',title='Breaking Bad',year='2008',season=1))
        result=Organizer(executor,HostOrganization(media,repository=repo)).organize(pid,Path('/test-data/organized')/client)
    return dict(result,plan_id=pid,infohash=infohash,client=client,synthetic_contract=True,provider_policy_acceptance=False)
