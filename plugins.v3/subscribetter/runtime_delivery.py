"""Server-derived consumer evidence over an immutable bundle and selected scope."""
from pathlib import PurePosixPath
import json
import time
from .archive import content,digest,item_projection,units,within
from .planner import encoded
from .repository import Target,utcnow
from .scheduler import instant,parse


def observe_consumer(worker,bundle,provider,*,entries=100,pages=2,seconds=5):
    archive=worker.archive;manifest=bundle['manifest'];keys=set(manifest['publication'])
    targets={encoded(json.loads(k)[:5]):Target(*json.loads(k)[:5]) for k in keys}
    scopes={k:provider(t) for k,t in targets.items()}
    selected=sorted({(r['emby_service'],str(r['library_id']),k) for r in archive.mappings.rules for k,t in targets.items()
        if r.get('media_source','themoviedb')==t.media_source and r.get('episode_group','')==t.episode_group})
    if not selected:raise ValueError('SELECTED_LIBRARY_SCOPE_REQUIRED')
    checkpoint='runtime-consumer:'+bundle['id'];identity=digest([manifest,archive.mappings.revision,scopes])
    state=archive.repository.setting(checkpoint)
    if not state or state['identity']!=identity or (instant()-parse(state['started_at'])).total_seconds()>300:
        state=dict(identity=identity,started_at=utcnow(),phase='EMBY',cursor=0,start=0,total=None,items=[],matches=[],directories={},assets={})
    deadline=time.monotonic()+seconds
    def save():archive.repository.setting(checkpoint,state)
    if state['phase']=='EMBY':
        for _ in range(pages):
            if state['cursor']>=len(selected):state['phase']='MATCH';state['cursor']=0;break
            service,library,targetkey=selected[state['cursor']]
            page=archive.sources.emby_target_page(service,library,targets[targetkey],state['start'],entries)
            rows=page['Items'];total=page['TotalRecordCount']
            if type(total)is not int or total<0 or len(rows)>entries or (state['total']is not None and state['total']!=total):raise ValueError('CONSUMER_SCAN_CHANGED')
            if state['start']+len(rows)>total or (not rows and state['start']<total):raise ValueError('CONSUMER_SCAN_INCOMPLETE')
            prior={(x['service'],x['library'],x['item']['Id']) for x in state['items']}
            for item in rows:
                if (service,library,item['Id']) in prior:raise ValueError('CONSUMER_DUPLICATE_ITEM')
                state['items'].append(dict(service=service,library=library,target=targetkey,item=item_projection(item),series=page.get('Series')))
                if len(state['items'])>10000:raise ValueError('CONSUMER_ITEM_LIMIT')
            state['total']=total;state['start']+=len(rows)
            if state['start']==total:state.update(cursor=state['cursor']+1,start=0,total=None)
            save()
            if time.monotonic()>=deadline:break
        save();return None
    if state['phase']=='MATCH':
        end=min(len(state['items']),state['cursor']+entries)
        while state['cursor']<end:
            row=state['items'][state['cursor']];item=row['item'];series=row['series'];scope=scopes[row['target']]
            actual=units(item,archive.mappings.scoped(row['service'],row['library']),{series['Id']:series} if series else {},scope=scope)
            affected=keys&set(actual)
            if affected:
                for source in item.get('MediaSources',[]):
                    source_path=source.get('Path');item_path=source_path if str(source_path).lower().endswith('.strm') else item.get('Path')
                    rule,path,_=archive.mappings.resolve(row['service'],row['library'],item_path,source_path)
                    remote=archive.sources.cloud_stat(rule['cloud_scope_id'],path,refresh=True)
                    expected=[a for a in manifest['assets'] if a['role']=='video' and affected&set(a['targets']) and content(a['content'])==content(remote)]
                    sidecar_only=not any(a['role']=='video' and affected&set(a['targets']) for a in manifest['assets'])
                    if sidecar_only:
                        with archive.repository.connection() as db:
                            existing=[json.loads(r[0]) for r in db.execute('SELECT data FROM archive_versions WHERE target_key IN (SELECT value FROM json_each(?)) AND active=1',(encoded(sorted(affected)),))]
                        sidecar_only=all(any(o['target_key']==key and o['video']['cloud_scope_id']==rule['cloud_scope_id'] and o['video']['path']==path and content(o['video'])==content(remote) for o in existing) for key in affected)
                    if expected or sidecar_only:
                        state['matches'].append(dict(service=row['service'],library_id=row['library'],item_id=item['Id'],keys=sorted(affected),
                            scope=rule['cloud_scope_id'],path=path,indices=[a['file_index'] for a in expected]))
                        directory=encoded([rule['cloud_scope_id'],str(PurePosixPath(path).parent)])
                        state['directories'].setdefault(directory,dict(rows=None,cursor=0))
            state['cursor']+=1;save()
            if time.monotonic()>=deadline:return None
        if state['cursor']<len(state['items']):return None
        if set().union(*(set(m['keys']) for m in state['matches']))!=keys:raise ValueError('CONSUMER_VIDEO_NOT_ASSOCIATED')
        state['phase']='ASSETS';save();return None
    if state['phase']=='ASSETS':
        for name,directory in state['directories'].items():
            scope,path=json.loads(name)
            if directory['rows'] is None:
                directory['rows']=worker.cloud.inventory(scope,path,limit=10000,seconds=seconds)
                save()
            count=0
            while directory['cursor']<len(directory['rows']) and count<entries:
                item=directory['rows'][directory['cursor']];directory['cursor']+=1;count+=1
                if not item['directory']:
                    observed=archive.sources.cloud_stat(scope,item['path'],refresh=True)
                    for asset in manifest['assets']:
                        if content(observed)!=content(asset['content']):continue
                        videos=[m for m in state['matches'] if set(asset['targets'])<=set(m['keys']) and m['scope']==scope and within(item['path'],str(PurePosixPath(m['path']).parent))]
                        if asset['role']=='video':videos=[m for m in videos if m['path']==item['path'] and asset['file_index'] in m['indices']]
                        if videos:
                            proof=dict(file_index=asset['file_index'],cloud_scope_id=scope,path=item['path'])
                            old=state['assets'].get(str(asset['file_index']))
                            if old and old!=proof:raise ValueError('CONSUMER_CONTENT_AMBIGUOUS')
                            state['assets'][str(asset['file_index'])]=proof
                save()
                if time.monotonic()>=deadline:return None
            if directory['cursor']<len(directory['rows']):return None
        if set(map(int,state['assets']))!={a['file_index'] for a in manifest['assets']}:raise ValueError('CONSUMER_ASSETS_INCOMPLETE')
        refs=[dict(service=m['service'],library_id=m['library_id'],item_id=m['item_id']) for m in state['matches']]
        refs=list({encoded(r):r for r in refs}.values())
        receipt=dict(action_id=bundle['publication_action'],settled=True,assets=list(state['assets'].values()),emby=refs)
        receipt['evidence_ref']='consumer:'+digest([bundle['id'],receipt])
        # No caller-supplied proof reaches this branch. The accepted archive
        # path re-reads every reference/asset and commits receipts atomically.
        return receipt
    raise ValueError('CONSUMER_CHECKPOINT_INVALID')
