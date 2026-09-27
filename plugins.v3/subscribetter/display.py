"""Read-only per-target facts, using the same SQLite snapshot as the page."""
import json
from .policy import quality_facts


def current_quality(current,policy=None):
    if not isinstance(current,dict) or current.get('state')!='PRESENT':return []
    result=[]
    for version in current.get('versions',[])[:20]:
        if version.get('reliable') is not True:continue
        raw=version.get('raw',{})
        # Normalization is the existing archive policy, never a UI filename parser.
        quality=quality_facts(policy.normalize(raw,current=True)) if policy else dict(raw.get('technical',{}),basis={k:'measured' for k in ('resolution','picture','audio')})
        result.append(dict(version_id=version.get('version_id'),quality=quality))
    return result


def processing(db,row):
    if not row['owner_plan_id'] or row['authorization']!='ACTIVE' or row['target_state']!='ACTIVE' or row['target_generation']!=row['generation'] or row['plan_task_generation']!=row['task_generation']:return None
    snapshot=json.loads(row['plan_snapshot'] or '{}');key=row['target_key'];plan=row['owner_plan_id']
    target=snapshot.get('targets',{}).get(key,{})
    table={f['index']:f for f in snapshot.get('torrent_files',[])+[a['file'] for a in snapshot.get('local_assets',[])]}
    indices={i for i in snapshot.get('selected_indices',[]) if i in table and key in table[i]['targets']}
    pending=list(indices)
    while pending:
        for i in table[pending.pop()].get('requires',[]):
            if i in table and i not in indices and i in snapshot.get('selected_indices',[]):indices.add(i);pending.append(i)
    files=[table[i] for i in sorted(indices)];videos=[f for f in files if f['role']=='video']
    stats={};times={};states={}
    # Each sample contains selected-file counters. Never use its torrent aggregate.
    for r in db.execute("SELECT sample FROM plan_progress WHERE plan_id=? ORDER BY json_extract(sample,'$.sampled_at') DESC,files",(plan,)):
        sample=json.loads(r['sample'])
        for index in indices:
            if index not in stats and str(index) in sample.get('files',{}):
                stats[index]=sample['files'][str(index)];times[index]=sample.get('sampled_at');states[index]=sample.get('status')
        if len(stats)==len(indices):break
    total=sum(f['size'] for f in files) if files and all(type(f.get('size')) is int for f in files) else None
    downloaded=sum(stats[i]['downloaded_bytes'] for i in indices) if indices and all(stats.get(i,{}).get('downloaded_bytes') is not None for i in indices) else None
    speed=sum(stats[i]['speed'] for i in indices) if indices and all(stats.get(i,{}).get('speed') is not None for i in indices) else None
    transfer=[];bundles=[]
    for r in db.execute('SELECT * FROM delivery_bundles WHERE plan_id=? ORDER BY id',(plan,)):
        b=json.loads(r['data']);owner=b.get('vector',{}).get(key,{})
        if owner.get('generation')!=row['generation'] or owner.get('owner_plan_id')!=plan or r['state'] in ('CANCELLED','ABANDONED','CLEANED'):continue
        relevant=[f for f in b.get('files',[]) if f.get('file_index') in indices]
        if not relevant:continue
        bundles.append(dict(id=r['id'],revision=r['revision'],state=r['state'],reason=b.get('reason'),due=r['due'],needs_reconcile=any(f.get('state')=='UNKNOWN' for f in relevant)))
        for f in relevant:
            transfer.append(dict(file_index=f['file_index'],name=table[f['file_index']]['path'].rsplit('/',1)[-1],role=table[f['file_index']]['role'],
                state=f.get('state'),misses=f.get('misses'),miss_limit=b.get('rapid_miss_limit'),due=f.get('due'),
                local_bytes_sent=f.get('progress',{}).get('bytes_sent'),remote_verified=f.get('state')=='VERIFIED'))
    attachments=[f for f in files if f['role']!='video'];organized={r['file_index']:r['state'] for r in db.execute('SELECT file_index,state FROM organized_assets WHERE plan_id=?',(plan,))}
    phase=row['target_phase'] or row['plan_phase'] or 'UNKNOWN'
    unknown=row['publish_phase'] in ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','UNKNOWN') or any(b['state'] in ('UNKNOWN','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN') for b in bundles) or any(f['state']=='UNKNOWN' for f in transfer)
    consumer=any(b['state']=='WAIT_CONSUMER' for b in bundles)
    if unknown:phase='PUBLISH_OUTCOME_UNKNOWN' if row['publish_phase']!='NOT_SENT' else 'UNKNOWN'
    elif consumer:phase='WAIT_CONSUMER'
    elif any(f['state'] in ('UPLOADING','CD2_UPLOADING') for f in transfer):phase='UPLOADING'
    elif any(f['state']=='PENDING' and (f['misses'] or 0)>0 for f in transfer):phase='RAPID_WAIT'
    due=min((f['due'] for f in transfer if f['due'] and not f['remote_verified']),default=None) if not unknown and not consumer else None
    return dict(plan_id=plan,candidate_key=snapshot.get('candidate_key'),candidate_title=snapshot.get('title'),phase=phase,state=row['target_state'],action=target.get('action'),reason='CONSUMER_SETTLEMENT_REQUIRED' if consumer else next((b['reason'] for b in bundles if b['reason']),target.get('reason')),
        quality=target.get('quality_facts') if len(videos)<=1 else None,
        change=dict(target['change'],baseline_revision=snapshot.get('current',{}).get(key,{}).get('revision'),
                    baseline_current=snapshot.get('current',{}).get(key,{}).get('revision')==row['current_revision']) if len(videos)<=1 and target.get('change') else None,
        files=[f['path'].rsplit('/',1)[-1] for f in videos[:20]],started_at=row['plan_created_at'],
        reconcile=[dict(component='delivery',object_id=b['id'],revision=str(b['revision'])) for b in bundles if b['needs_reconcile'] or b['state'] in ('UNKNOWN','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN')][:20],
        download=dict(total_bytes=total,downloaded_bytes=downloaded,speed=speed,sampled_at=min((t for t in times.values() if t),default=None),shared_file=any(len(f['targets'])>1 for f in files),file_count=len(files)),
        transfer_files=transfer[:20],transfer_file_count=len(transfer),
        attachments=dict(required=len(attachments),local_ready=sum(organized.get(f['index'])=='COMPLETE' for f in attachments),remote_verified=sum(any(t['file_index']==f['index'] and t['remote_verified'] for t in transfer) for f in attachments)),
        next_step='RECONCILE' if unknown else 'WAIT_CONSUMER' if consumer else 'WAIT_ASSETS' if phase=='WAITING_ASSETS' else 'CHECK_TRANSFER' if transfer else 'CHECK_DOWNLOAD',next_at=due)
