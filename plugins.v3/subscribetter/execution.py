"""Durable strict downloader execution. Every mutation consumes W04 authority."""
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
from threading import RLock

from .candidates import torrent_table, value
from .planner import Authority, encoded, validate_files
from .repository import utcnow
from .scheduler import parse, instant

# ponytail: one host-process mutation lock; per-download locks if throughput matters.
MUTATION_LOCK = RLock()


class ConfiguredDownloader:
    """Only the instance already provided by the public DownloaderHelper service."""
    def __init__(self,service):
        if service is None or service.type not in ('qbittorrent','transmission') or service.instance is None:
            raise ValueError('DOWNLOADER_CAPABILITY_MISSING')
        self.kind,self.instance=service.type,service.instance

    @classmethod
    def named(cls,name):
        from app.sdk.services import DownloaderHelper
        if not isinstance(name,str) or not name:
            raise ValueError('NAMED_DOWNLOADER_REQUIRED')
        return cls(DownloaderHelper().get_service(name=name))

    def task(self,infohash):
        rows,error=self.instance.get_torrents(ids=infohash)
        if error:
            raise RuntimeError('DOWNLOADER_UNAVAILABLE')
        if not rows:
            return None
        if len(rows)!=1:
            raise ValueError('AMBIGUOUS_TASK_IDENTITY')
        row=rows[0]
        if self.kind=='qbittorrent':
            actual=value(row,'hash');tid=actual;path=value(row,'save_path');state=str(value(row,'state','')).lower()
            states={'stoppeddl':'PAUSED','stoppedup':'PAUSED','pauseddl':'PAUSED','pausedup':'PAUSED','downloading':'DOWNLOADING','forceddl':'DOWNLOADING','stalleddl':'DOWNLOADING','queueddl':'QUEUED','queuedup':'QUEUED','uploading':'COMPLETED','stalledup':'COMPLETED','forcedup':'COMPLETED','error':'FAILED','missingfiles':'FAILED'}
            status='CHECKING' if state.startswith('checking') else states.get(state,'DISCONNECTED')
            total=value(row,'total_size');done=value(row,'downloaded');speed=value(row,'dlspeed')
        else:
            actual=value(row,'hash_string',value(row,'hashString'));tid=str(value(row,'id'));path=value(row,'download_dir',value(row,'downloadDir'));state=str(value(row,'status','')).lower()
            status={'stopped':'PAUSED','check_pending':'CHECKING','checking':'CHECKING','download_pending':'QUEUED','downloading':'DOWNLOADING','seed_pending':'QUEUED','seeding':'COMPLETED'}.get(state,'DISCONNECTED')
            total=value(row,'total_size');done=value(row,'downloaded_ever');speed=value(row,'rate_download')
        if str(actual).lower()!=infohash.lower():
            raise ValueError('TASK_HASH_MISMATCH')
        labels=value(row,'tags','').split(',') if self.kind=='qbittorrent' else value(row,'labels',[])
        return dict(id=str(tid),infohash=str(actual).lower(),save_path=path,state=status,total_bytes=total,downloaded_bytes=done,speed=speed,markers=[str(x).strip() for x in labels or []])

    def files(self,infohash):
        rows=self.instance.get_files(infohash)
        if rows is None:
            raise ValueError('FILE_TABLE_UNAVAILABLE')
        if isinstance(rows,dict):
            rows=[dict(r,id=i) if isinstance(r,dict) else r for i,r in rows.items()]
        result=[]
        for row in rows:
            size=value(row,'size');index=value(row,'index',value(row,'id'));name=value(row,'name')
            if self.kind=='qbittorrent':
                priority=value(row,'priority');progress=value(row,'progress')
                if type(priority)is not int or priority not in (0,1,6,7):
                    raise ValueError('WANTED_CAPABILITY_MISSING')
                wanted=priority!=0
                complete=int(size*progress) if type(size)is int and type(progress)in (int,float) and math.isfinite(progress) and 0<=progress<=1 else None
            else:
                wanted=value(row,'selected');complete=value(row,'completed')
                if type(wanted)is not bool:
                    raise ValueError('TR_SELECTED_CAPABILITY_MISSING')
            if type(index)is not int or not isinstance(name,str) or type(size)is not int or size<0 or (complete is not None and (type(complete)is not int or not 0<=complete<=size)):
                raise ValueError('FILE_TABLE_INVALID')
            result.append(dict(id=index,path=name,size=size,wanted=wanted,completed=complete))
        return result

    def add(self,content,infohash,save_path,marker):
        if self.kind=='qbittorrent':
            success,ids=self.instance.add_torrent(content=content,is_paused=True,download_dir=save_path,tag=marker,category=None,ignore_category_check=False,content_layout='Original')
            if not success or len(ids)!=1 or ids[0].lower()!=infohash:
                return None
            return ids[0]
        result=self.instance.add_torrent(content=content,is_paused=True,download_dir=save_path,labels=[marker])
        if result is None or str(value(result,'hash_string',value(result,'hashString'))).lower()!=infohash:
            return None
        return str(value(result,'id'))

    def pause(self,task_id):
        return self.instance.stop_torrents(ids=task_id)

    def resume(self,task_id):
        return self.instance.start_torrents(ids=task_id)

    def select_files(self,task_id,indices,wanted):
        if not indices or type(wanted)is not bool:
            raise ValueError('EXACT_SELECTION_REQUIRED')
        if self.kind=='qbittorrent':
            return self.instance.set_files(torrent_hash=task_id,file_ids=indices,priority=int(wanted))
        return self.instance.set_files(task_id,indices) if wanted else self.instance.set_unwanted_files(task_id,indices)


class Exclusions:
    def __init__(self, repository):
        self.repository=repository

    def add(self, exclusion_id, criteria, *, reason, expires_at=None):
        if not exclusion_id or not reason or not isinstance(criteria,dict) or not criteria or set(criteria)-{'candidate_key','infohash','targets','content_sha1','condition'}:
            raise ValueError('explicit exclusion identity/scope required')
        if expires_at is not None:
            parse(expires_at)
        if criteria.get('condition'):
            from .policy import import_predicates
            import_predicates({'exclusion':criteria['condition']})
        encoded(criteria)
        with MUTATION_LOCK, self.repository.connection(write=True) as db:
            db.execute('INSERT INTO exclusions VALUES(?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET criteria=excluded.criteria,reason=excluded.reason,expires_at=excluded.expires_at,active=1',(exclusion_id,encoded(criteria),reason,expires_at))

    def revoke(self, exclusion_id):
        with MUTATION_LOCK, self.repository.connection(write=True) as db:
            db.execute('UPDATE exclusions SET active=0 WHERE id=?',(exclusion_id,))

    def token(self):
        with self.repository.connection() as db:
            rows=[tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]
        return sha256(encoded(rows).encode()).hexdigest()

    def matches(self, snapshot, *, facts=None):
        with self.repository.connection() as db:
            rows=db.execute('SELECT * FROM exclusions WHERE active=1').fetchall()
        for row in rows:
            if row['expires_at'] and parse(row['expires_at'])<=instant():
                continue
            c=json.loads(row['criteria'])
            if c.get('candidate_key') and c['candidate_key']!=snapshot.get('candidate_key'):
                continue
            if c.get('infohash') and c['infohash'].lower()!=snapshot.get('infohash','').lower():
                continue
            if c.get('targets') and not set(c['targets'])&set(snapshot.get('targets',())):
                continue
            if c.get('content_sha1') and c['content_sha1'] not in snapshot.get('content_sha1',[]):
                continue
            if c.get('condition'):
                # Reuse W02's bounded data-only evaluator, including its unknown semantics.
                from .policy import import_predicates, _Evaluator, _DEFAULT_RULES
                rules={**_DEFAULT_RULES,**import_predicates({'exclusion':c['condition']})}
                try:
                    if not _Evaluator(rules,facts or {}).evaluate(c['condition']):
                        continue
                except Exception:
                    return True  # Unknown required deny evidence cannot authorize execution.
            return True
        return False


class StrictExecutor:
    def __init__(self, repository, client_factory, *, revalidate, verify_torrent=torrent_table):
        if not callable(revalidate):
            raise ValueError('CURRENT_CANDIDATE_REVALIDATOR_REQUIRED')
        self.repository=repository
        self.authority=Authority(repository)
        self.clients=client_factory
        self.verify_torrent=verify_torrent
        self.revalidate=revalidate
        self.exclusions=Exclusions(repository)

    def _plan(self, plan_id):
        plan=self.authority.plan(plan_id)
        snapshot=plan['snapshot']
        if [f['index'] for f in snapshot['torrent_files']]!=list(range(len(snapshot['torrent_files']))):
            raise ValueError('CANONICAL_TORRENT_INDICES_REQUIRED')
        active={t['target_key']:t for t in plan['targets'] if t['state']=='ACTIVE'}
        indices=[i for i in snapshot['selected_indices'] if set(snapshot['torrent_files'][i]['targets'])<=set(active)]
        if not indices:
            raise ValueError('NO_ACTIVE_SAFE_FILES')
        keys={k for i in indices for k in snapshot['torrent_files'][i]['targets']}
        vector=self.authority.vector(sorted(keys))
        task=self.repository.get_task(plan['task_id'])
        if task['state'] not in ('ACTIVE','PASSIVE') or task['generation']!=plan['task_generation'] or any(v['owner_plan_id']!=plan_id or v['publish_phase']!='NOT_SENT' or v['current_revision']!=snapshot['current'][k]['revision'] for k,v in vector.items()):
            raise ValueError('CURRENT_AUTHORITY_REQUIRED')
        self._fresh_candidate(plan,vector)
        return plan,snapshot,indices,vector

    def _fresh_candidate(self,plan,vector,content_sha1=None):
        candidate=self.revalidate(plan)
        facts=candidate.get('facts',{}) if isinstance(candidate,dict) else {}
        for key in vector:
            raw=value(facts.get(key),'raw',{})
            snapshot=dict(plan['snapshot'],targets=[key],content_sha1=content_sha1 or (candidate.get('content_sha1',[]) if isinstance(candidate,dict) else []))
            if self.exclusions.matches(snapshot,facts=raw):
                raise ValueError('EXCLUDED')

    def _owned(self,s):
        with self.repository.connection() as db:
            row=db.execute('SELECT * FROM managed_downloads WHERE downloader=? AND infohash=?',(s['downloader'],s['infohash'])).fetchone()
            return dict(row) if row else None

    def _save(self,s,**changes):
        if not changes or set(changes)-{'client_id','state','evidence'}:
            raise ValueError('invalid execution update')
        with self.repository.connection(write=True) as db:
            db.execute('UPDATE managed_downloads SET '+','.join(k+'=?' for k in changes)+',updated_at=? WHERE downloader=? AND infohash=?',(*[encoded(v) if k=='evidence' else v for k,v in changes.items()],utcnow(),s['downloader'],s['infohash']))

    def _mutation(self,plan,indices,vector,verb,kind,fn,payload):
        s=plan['snapshot']
        self._fresh_candidate(plan,vector,payload.get('content_sha1'))
        action_id='exec:'+sha256(encoded([plan['id'],verb,payload,vector]).encode()).hexdigest()
        action=self.authority.begin_attempt(action_id,plan['id'],vector,kind,indices,dict(verb=verb,**payload))
        if not action['dispatch']:
            return action['state']=='SUCCEEDED',action_id,None
        try:
            result=fn()
        except Exception:
            self.authority.record_result(action_id,'UNKNOWN',{'code':'CLIENT_RESPONSE_UNKNOWN'})
            return False,action_id,None
        outcome='SUCCEEDED' if result is not None and result is not False else 'UNKNOWN'
        self.authority.record_result(action_id,outcome,{'accepted':outcome=='SUCCEEDED'})
        return outcome=='SUCCEEDED',action_id,result

    @staticmethod
    def _table(s,rows):
        if not isinstance(rows,list) or len(rows)!=len(s['torrent_files']):
            raise ValueError('FULL_FILE_TABLE_REQUIRED')
        actual={}
        for row in rows:
            key=(row.get('path'),row.get('size'))
            if key in actual or type(row.get('id')) is not int or type(row.get('wanted')) is not bool:
                raise ValueError('FILE_SELECTION_CAPABILITY_MISSING')
            actual[key]=row
        if len({r['id'] for r in rows})!=len(rows):
            raise ValueError('DUPLICATE_CLIENT_INDEX')
        mapping={}
        for file in s['torrent_files']:
            row=actual.get((file['path'],file['size']))
            if row is None:
                raise ValueError('FILE_PATH_SIZE_MISMATCH')
            mapping[file['index']]=row
        return mapping

    @staticmethod
    def _task(s,task):
        if not task or task.get('infohash','').lower()!=s['infohash'] or task.get('save_path')!=s['save_path'] or not task.get('id'):
            raise ValueError('TASK_IDENTITY_LAYOUT_MISMATCH')
        return task

    def _selection(self,s,client):
        mapping=self._table(s,client.files(s['infohash']))
        union=set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))
        if not union:
            raise ValueError('EMPTY_SELECTION')
        wanted={mapping[i]['id'] for i in union}
        if {row['id'] for row in mapping.values() if row['wanted']}!=wanted:
            raise ValueError('WANTED_READBACK_MISMATCH')
        return mapping,union

    def execute(self,plan_id,content,*,resume=False):
        with MUTATION_LOCK:
            try:
                plan,s,indices,vector=self._plan(plan_id)
                infohash,table=self.verify_torrent(content)
                if infohash.lower()!=s['infohash'] or table!=[(f['path'],f['size']) for f in s['torrent_files']]:
                    raise ValueError('TORRENT_CHANGED')
                validate_files(s['torrent_files'],indices)
                client=self.clients(s['downloader'])
                owned=self._owned(s)
                task=client.task(s['infohash'])
                if owned is None:
                    if task is not None:
                        raise ValueError('UNMANAGED_SAME_HASH')
                    marker='subscribetter:'+sha256((s['downloader']+s['infohash']).encode()).hexdigest()[:24]
                    with self.repository.connection(write=True) as db:
                        db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',(s['downloader'],s['infohash'],s['save_path'],encoded(table),marker,plan_id,'ADD_INTENT',utcnow()))
                    ok,action,result=self._mutation(plan,indices,vector,'add','ADD',lambda:client.add(content,s['infohash'],s['save_path'],marker),{'infohash':s['infohash'],'save_path':s['save_path'],'marker':marker})
                    if not ok or not result:
                        self._save(s,state='UNKNOWN',evidence={'action_id':action,'code':'ADD_OUTCOME_UNKNOWN'})
                        return {'state':'UNKNOWN','reason':'ADD_OUTCOME_UNKNOWN'}
                    self._save(s,client_id=str(result),state='ADDED',evidence={'action_id':action})
                    owned=self._owned(s)
                    task=client.task(s['infohash'])
                elif owned['state'] in ('ADD_INTENT','UNKNOWN') or not owned['client_id']:
                    # No response cannot establish new ownership; never adopt a coincident manual task.
                    return {'state':'UNKNOWN','reason':'ADD_OWNERSHIP_UNCONFIRMED'}
                if owned['save_path']!=s['save_path'] or json.loads(owned['file_table'])!=[list(x) for x in table]:
                    raise ValueError('SHARED_LAYOUT_CONFLICT')
                task=self._task(s,task)
                if str(task['id'])!=owned['client_id']:
                    raise ValueError('CLIENT_TASK_ID_CHANGED')
                if task['state']!='PAUSED':
                    ok,_,_=self._mutation(plan,indices,vector,'pause','SET_WANTED',lambda:client.pause(task['id']),{'id':task['id']})
                    if not ok or self._task(s,client.task(s['infohash']))['state']!='PAUSED':
                        raise ValueError('PAUSE_NOT_CONFIRMED')
                mapping=self._table(s,client.files(s['infohash']))
                union=set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))
                if not set(indices)<=union:
                    raise ValueError('SELECTION_AUTHORITY_CHANGED')
                wanted=sorted(mapping[i]['id'] for i in union)
                unwanted=sorted(r['id'] for i,r in mapping.items() if i not in union)
                if {r['id'] for r in mapping.values() if r['wanted']}!=set(wanted):
                    with self.repository.connection() as db:
                        registered={r[0] for r in db.execute('SELECT target_key FROM target_units')}
                    shared_vector=self.authority.vector(sorted({k for f in s['torrent_files'] for k in f['targets']} & registered))
                    for ids,enabled in ((unwanted,False),(wanted,True)):
                        if not ids:continue
                        if set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))!=union:
                            raise ValueError('SHARED_SELECTION_CHANGED')
                        ok,_,_=self._mutation(plan,indices,vector,'select:'+str(enabled),'SET_WANTED',lambda:client.select_files(task['id'],ids,enabled),{'id':task['id'],'indices':ids,'wanted':enabled,'shared_authority':shared_vector})
                        if not ok:
                            raise ValueError('SELECTION_OUTCOME_UNKNOWN')
                self._selection(s,client)
                if self._task(s,client.task(s['infohash']))['state']!='PAUSED':
                    raise ValueError('TASK_NOT_PAUSED')
                self._save(s,state='PAUSED_VERIFIED',evidence={'wanted':wanted,'unwanted':unwanted})
                return self.resume(plan_id) if resume else {'state':'PAUSED_VERIFIED','infohash':s['infohash'],'wanted':wanted}
            except (ValueError,RuntimeError) as error:
                return {'state':'BLOCKED','reason':str(error) if str(error).isupper() else 'EXECUTION_CHECK_FAILED'}
            except Exception:
                return {'state':'BLOCKED','reason':'CLIENT_UNAVAILABLE'}

    def resume(self,plan_id):
        with MUTATION_LOCK:
            try:
                plan,s,indices,vector=self._plan(plan_id)
                owned=self._owned(s)
                if not owned or not owned['client_id'] or owned['state'] not in ('PAUSED_VERIFIED','RUNNING'):
                    raise ValueError('PAUSED_SELECTION_NOT_VERIFIED')
                client=self.clients(s['downloader'])
                task=self._task(s,client.task(s['infohash']))
                if str(task['id'])!=owned['client_id']:
                    raise ValueError('CLIENT_TASK_ID_CHANGED')
                self._selection(s,client)
                ok,_,_=self._mutation(plan,indices,vector,'resume','RESUME',lambda:client.resume(task['id']),{'id':task['id']})
                state=self._task(s,client.task(s['infohash']))['state']
                if not ok or state not in ('DOWNLOADING','QUEUED','COMPLETED'):
                    self._save(s,state='PAUSED_VERIFIED',evidence={'code':'RESUME_NOT_ACCEPTED','actual_state':state})
                    return {'state':'UNKNOWN','reason':'RESUME_NOT_ACCEPTED','actual_state':state}
                self._save(s,state='RUNNING',evidence={'actual_state':state})
                self.authority.set_transfer_phase(plan_id,vector,'DOWNLOADING')
                return {'state':'RUNNING','actual_state':state,'infohash':s['infohash']}
            except Exception:
                return {'state':'BLOCKED','reason':'RESUME_CHECK_FAILED'}

    def sample(self,plan_id):
        plan=self.authority.plan(plan_id);s=plan['snapshot']
        owned=self._owned(s)
        if not owned or not owned['client_id']:
            raise ValueError('DOWNLOAD_OWNERSHIP_UNCONFIRMED')
        client=self.clients(s['downloader'])
        task=self._task(s,client.task(s['infohash']))
        if str(task['id'])!=owned['client_id']:
            raise ValueError('CLIENT_TASK_ID_CHANGED')
        mapping=self._table(s,client.files(s['infohash']))
        stats={i:{'downloaded_bytes':mapping[i].get('completed'),'speed':None} for i in s['selected_indices']}
        complete=all(stats[i]['downloaded_bytes']==s['torrent_files'][i]['size'] for i in stats)
        status='COMPLETED' if complete and task['state'] not in ('CHECKING','DISCONNECTED','FAILED') else task['state']
        return self.authority.record_progress(plan_id,s['selected_indices'],stats,torrent=task,status=status)

    def reconcile(self,plan_id):
        """Read only at the client: settle known intents, never guess or retransmit."""
        with MUTATION_LOCK:
            plan=self.authority.plan(plan_id);s=plan['snapshot'];owned=self._owned(s)
            if not owned:return {'state':'UNKNOWN','reason':'NO_OWNERSHIP_INTENT'}
            client=self.clients(s['downloader']);task=self._task(s,client.task(s['infohash']))
            mapping=self._table(s,client.files(s['infohash']))
            if not owned['client_id']:
                if owned['marker'] not in task.get('markers',[]) or task['state']!='PAUSED':
                    return {'state':'UNKNOWN','reason':'ADD_IDENTITY_UNPROVEN'}
                with self.repository.connection() as db:
                    adds=db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind='ADD' AND state IN ('IN_FLIGHT','UNKNOWN')",(owned['add_action'],)).fetchall()
                if len(adds)!=1 or json.loads(adds[0]['payload']).get('marker')!=owned['marker']:
                    return {'state':'UNKNOWN','reason':'ADD_INTENT_UNPROVEN'}
                self.authority.record_result(adds[0]['id'],'SUCCEEDED',{'evidence':'exact marker/hash/layout/full table paused readback','client_id':task['id']})
                self._save(s,client_id=task['id'],state='ADDED',evidence={'reconciled_add':adds[0]['id']})
            elif owned['client_id']!=task['id']:
                raise ValueError('CLIENT_TASK_ID_CHANGED')
            with self.repository.connection() as db:
                pending=db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind IN ('SET_WANTED','RESUME') AND state IN ('IN_FLIGHT','UNKNOWN')",(plan_id,)).fetchall()
            actual={r['id']:r for r in mapping.values()};remaining=[]
            for action in pending:
                payload=json.loads(action['payload']);verb=payload.get('verb');matched=False
                if verb=='pause':matched=task['state']=='PAUSED'
                elif verb.startswith('select:'):
                    matched=task['state']=='PAUSED' and all(i in actual and actual[i]['wanted']==payload['wanted'] for i in payload['indices'])
                elif verb=='resume':
                    try:self._selection(s,client);matched=task['state'] in ('DOWNLOADING','QUEUED','COMPLETED')
                    except ValueError:matched=False
                if matched:self.authority.record_result(action['id'],'SUCCEEDED',{'evidence':'exact task and whole file selection readback','actual_state':task['state']})
                else:remaining.append(action['id'])
            return {'state':'UNKNOWN' if remaining else 'RECONCILED','unresolved_actions':remaining}


def safe_local(root, path, *, exists=True):
    """Reject symlinks in every component, including the configured root itself."""
    if '..' in Path(root).parts or '..' in Path(path).parts:
        raise ValueError('PATH_TRAVERSAL_NOT_ALLOWED')
    root,path=Path(root).absolute(),Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('PATH_OUTSIDE_APPROVED_ROOT')
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('SYMLINK_NOT_ALLOWED')
    if exists and not path.is_file():
        raise ValueError('ASSET_NOT_AVAILABLE')
    return path


def digest(path):
    with Path(path).open('rb') as stream:
        from hashlib import file_digest
        return file_digest(stream,'sha256').hexdigest()


def asset_hashes(path):
    from hashlib import sha1
    strong,content=sha256(),sha1()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            strong.update(block);content.update(block)
    return strong.hexdigest(),content.hexdigest()


class Organizer:
    """Single exact file dispatches and local receipt verification, never a tree scan."""
    def __init__(self, executor, host, *, source_root=lambda s:Path(s['save_path'])):
        self.executor,self.host,self.source_root=executor,host,source_root
        self.repository=executor.repository

    def organize(self,plan_id,target_root):
        with MUTATION_LOCK:
            try:
                plan,s,indices,vector=self.executor._plan(plan_id)
                target=Path(target_root).absolute()
                safe_local(target,target,exists=False)
                client=self.executor.clients(s['downloader'])
                task=self.executor._task(s,client.task(s['infohash']))
                mapping,_=self.executor._selection(s,client)
                if task['state'] in ('CHECKING','FAILED','DISCONNECTED') or any(mapping[i].get('completed')!=s['torrent_files'][i]['size'] for i in indices):
                    return {'state':'WAITING_ASSETS'}
                paths={i:safe_local(self.source_root(s),Path(self.source_root(s))/s['torrent_files'][i]['path']) for i in indices}
                asset_digests={i:asset_hashes(p) for i,p in paths.items()}
                hashes={i:v[0] for i,v in asset_digests.items()}
                content_sha1=[v[1] for v in asset_digests.values()]
                if any(paths[i].stat().st_size!=s['torrent_files'][i]['size'] for i in indices):
                    raise ValueError('COMPLETED_ASSET_SIZE_MISMATCH')
                self.executor.sample(plan_id)
                ok,_,_=self.executor._mutation(plan,indices,vector,'history','ORGANIZE',lambda:self.host.history(s,paths),{'paths':[str(paths[i]) for i in indices],'content_sha1':content_sha1})
                if not ok:
                    return {'state':'UNKNOWN','reason':'HISTORY_OUTCOME_UNKNOWN'}
                outputs=[]
                for index in sorted(indices,key=lambda i:(s['torrent_files'][i]['role']!='video',i)):
                    with self.repository.connection() as db:
                        row=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(plan_id,index)).fetchone()
                    if row and row['state']=='COMPLETE':
                        output=safe_local(target,row['destination'])
                        if output.stat().st_size!=row['size'] or digest(output)!=row['sha256']:
                            raise ValueError('ORGANIZED_ASSET_CHANGED')
                        outputs.append(str(output));continue
                    item=s['torrent_files'][index];src=paths[index]
                    evidence=dict(vector=vector,target_root=str(target),indices=indices,source_mtime_ns=src.stat().st_mtime_ns,exclusions_token=self.executor.exclusions.token())
                    with self.repository.connection(write=True) as db:
                        db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plan_id,file_index) DO UPDATE SET evidence=excluded.evidence',
                            (plan_id,index,str(src),None,item['size'],hashes[index],'AUTHORIZED',encoded(evidence)))
                    ok,action,result=self.executor._mutation(plan,indices,vector,'organize:'+str(index),'ORGANIZE',lambda:self.host.transfer(src,target,item,s),{'file_index':index,'source':str(src),'target_root':str(target),'sha256':hashes[index],'content_sha1':content_sha1})
                    if not ok or result is None:
                        return {'state':'UNKNOWN','reason':'ORGANIZE_OUTCOME_UNKNOWN','action_id':action}
                    output=safe_local(target,result)
                    if output.stat().st_size!=item['size'] or digest(output)!=hashes[index]:
                        raise ValueError('ORGANIZED_ASSET_READBACK_MISMATCH')
                    with self.repository.connection(write=True) as db:
                        db.execute("UPDATE organized_assets SET destination=?,state='COMPLETE',evidence=? WHERE plan_id=? AND file_index=?",(str(output),encoded(dict(evidence,action_id=action)),plan_id,index))
                    outputs.append(str(output))
                self.executor.authority.set_transfer_phase(plan_id,vector,'WAITING_ASSETS')
                return {'state':'COMPLETE','files':outputs,'final_ingest_confirmed':False}
            except (ValueError,RuntimeError) as error:
                return {'state':'BLOCKED','reason':str(error) if str(error).isupper() else 'ORGANIZE_CHECK_FAILED'}
            except Exception:
                return {'state':'UNKNOWN','reason':'ORGANIZE_RESPONSE_UNKNOWN'}

    def reconcile(self,plan_id):
        """Reconcile an uncertain copy from persisted destination + public history."""
        plan=self.executor.authority.plan(plan_id);s=plan['snapshot'];settled=[]
        with self.repository.connection() as db:
            rows=db.execute("SELECT * FROM organized_assets WHERE plan_id=? AND state='AUTHORIZED'",(plan_id,)).fetchall()
            actions=db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind='ORGANIZE' AND state IN ('UNKNOWN','IN_FLIGHT')",(plan_id,)).fetchall()
        for action in actions:
            payload=json.loads(action['payload'])
            if payload.get('verb')=='history' and self.host.history_receipt(s,payload['paths']):
                self.executor.authority.record_result(action['id'],'SUCCEEDED',{'evidence':'public exact download history readback'});settled.append(action['id'])
        for row in rows:
            if not row['destination']:continue
            evidence=json.loads(row['evidence'])
            try:
                destination=safe_local(evidence['target_root'],row['destination'])
                if destination.stat().st_size!=row['size'] or digest(destination)!=row['sha256'] or not self.host.transfer_receipt(row['source'],str(destination),s):continue
                matched=[a for a in actions if json.loads(a['payload']).get('verb')=='organize:'+str(row['file_index']) and json.loads(a['payload']).get('sha256')==row['sha256']]
                if len(matched)!=1:continue
                self.executor.authority.record_result(matched[0]['id'],'SUCCEEDED',{'evidence':'public exact transfer history and destination content readback'})
                with self.repository.connection(write=True) as db:
                    db.execute("UPDATE organized_assets SET state='COMPLETE',evidence=? WHERE plan_id=? AND file_index=?",(encoded(dict(evidence,action_id=matched[0]['id'])),plan_id,row['file_index']))
                settled.append(matched[0]['id'])
            except (OSError,ValueError):continue
        return {'state':'RECONCILED' if settled else 'UNKNOWN','settled_actions':settled}


class TransferGuard:
    """Synchronous admission for managed roots; ordinary manual paths pass through."""
    def __init__(self,repository):
        self.repository=repository
        self.roots=set()

    def required(self):
        try:
            with self.repository.connection() as db:
                for row in db.execute('SELECT save_path,file_table FROM managed_downloads'):
                    paths=[PurePosixPath(f[0]) for f in json.loads(row['file_table'])]
                    if paths and len({p.parts[0] for p in paths})==1 and all(len(p.parts)>1 for p in paths):
                        self.roots.add((str(Path(row['save_path'])/paths[0].parts[0]),True))
                    else:
                        self.roots.update((str(Path(row['save_path'])/p),False) for p in paths)
        except Exception:
            pass
        return bool(self.roots)

    def intercept(self,event):
        data=event.event_data;item=value(data,'fileitem');raw=value(item,'path')
        if value(item,'storage')!='local' or not isinstance(raw,str):
            return
        path=Path(raw).absolute()
        self.required()
        if not any(path.is_relative_to(Path(root).absolute()) if directory else path==Path(root).absolute() for root,directory in self.roots):return
        allowed=False
        try:
            with self.repository.connection() as db:
                rows=db.execute("SELECT * FROM organized_assets WHERE source=? AND state='AUTHORIZED'",(str(path),)).fetchall()
            for row in rows:
                evidence=json.loads(row['evidence']);a=Authority(self.repository);p=a.plan(row['plan_id'])
                task=self.repository.get_task(p['task_id'])
                if task['state'] not in ('ACTIVE','PASSIVE') or task['generation']!=p['task_generation']:
                    continue
                if a.vector(list(evidence['vector']))!=evidence['vector'] or any(v['owner_plan_id']!=p['id'] or v['publish_phase']!='NOT_SENT' for v in evidence['vector'].values()):
                    continue
                if self.repository.setting('planner_revisions')!=[p['snapshot']['policy_revision'],p['snapshot']['parse_revision']]:
                    continue
                if Exclusions(self.repository).token()!=evidence.get('exclusions_token'):
                    continue
                destination=value(data,'target_path')
                if value(data,'target_storage','local')!='local' or not destination:
                    continue
                safe_local(evidence['target_root'],destination,exists=False)
                if row['destination'] is not None and Path(destination)!=Path(row['destination']):
                    continue
                safe_local(p['snapshot']['save_path'],path)
                stat=path.stat()
                if stat.st_size!=row['size'] or stat.st_mtime_ns!=evidence.get('source_mtime_ns'):
                    continue
                # Authorization rows alone are not permission: the matching durable
                # single-file attempt must currently be inside its dispatch.
                with self.repository.connection() as db:
                    actions=db.execute("SELECT payload,targets FROM plan_actions WHERE plan_id=? AND kind='ORGANIZE' AND state='IN_FLIGHT'",(p['id'],)).fetchall()
                if any(json.loads(r['payload']).get('file_index')==row['file_index'] and json.loads(r['targets'])==evidence['vector'] for r in actions):
                    allowed=True;break
        except Exception:
            allowed=False
        if not allowed:
            if isinstance(data,dict):
                data.update(cancel=True,source='subscribetter',reason='MANAGED_ASSET_NOT_AUTHORIZED')
            else:
                data.cancel=True;data.source='subscribetter';data.reason='MANAGED_ASSET_NOT_AUTHORIZED'


class HostOrganization:
    """Public history and one-file transfer APIs with pre-recognized media only."""
    def __init__(self,media,*,meta_factory=None,repository=None):
        self.media=media
        self.meta_factory=meta_factory
        self.repository=repository
        self.video_outputs={}

    def history(self,snapshot,paths):
        from app.db.oper.downloadhistory import DownloadHistoryOper
        from app.sdk.media import resolve_media_identity
        oper=DownloadHistoryOper();s=snapshot
        source,mid=resolve_media_identity(media=self.media)
        identities=[json.loads(k) for k in s['targets']]
        if source is None or any((i[1],i[2])!=(source.value,str(mid)) for i in identities):
            raise ValueError('ORGANIZE_MEDIA_IDENTITY_MISMATCH')
        row=oper.get_by_hash(s['infohash'])
        if row is not None and (row.downloader!=s['downloader'] or row.path!=s['save_path'] or row.media_source!=source.value or str(row.media_id)!=str(mid)):
            raise ValueError('HISTORY_IDENTITY_CONFLICT')
        scope=sha256(encoded(sorted(s['targets'])).encode()).hexdigest()
        if row is None or not isinstance(value(row,'note'),dict) or row.note.get('strict_scope')!=scope:
            oper.add(path=s['save_path'],type=identities[0][0],title=self.media.title,year=str(self.media.year or ''),media_source=source.value,media_id=str(mid),seasons=','.join(sorted({f'S{i[3]:02}' for i in identities if i[3] is not None})),episodes=','.join(sorted({f'E{i[5]:02}' for i in identities if i[5] is not None})),episode_group=identities[0][4],downloader=s['downloader'],download_hash=s['infohash'],torrent_name=PurePosixPath(s['torrent_files'][0]['path']).parts[0],username='subscriBetter',note={'strict_plan':True,'strict_scope':scope,'candidate_key':s['candidate_key']})
        before=oper.get_files_by_hash(s['infohash'],state=1)
        expected={str(p) for p in paths.values()}
        if self.repository is not None:
            with self.repository.connection() as db:
                previous=db.execute("SELECT a.payload,p.snapshot FROM plan_actions a JOIN plans p ON p.id=a.plan_id WHERE a.kind='ORGANIZE' AND a.state='SUCCEEDED'").fetchall()
            for previous_row in previous:
                old=json.loads(previous_row['snapshot']);payload=json.loads(previous_row['payload'])
                if (old['infohash'],old['downloader'],old['save_path'])==(s['infohash'],s['downloader'],s['save_path']) and payload.get('verb')=='history':expected.update(payload['paths'])
        if any(value(r,'downloader')!=s['downloader'] or value(r,'fullpath') not in expected for r in before):
            raise ValueError('HISTORY_FILES_CONFLICT')
        missing=expected-{value(r,'fullpath') for r in before}
        oper.add_files([dict(downloader=s['downloader'],download_hash=s['infohash'],fullpath=str(paths[i]),savepath=s['save_path'],filepath=s['torrent_files'][i]['path'],torrentname=PurePosixPath(s['torrent_files'][i]['path']).parts[0],state=1) for i in paths if str(paths[i]) in missing])
        row=oper.get_by_hash(s['infohash'])
        return row is not None and row.downloader==s['downloader'] and {value(r,'fullpath') for r in oper.get_files_by_hash(s['infohash'],state=1)}==expected

    def history_receipt(self,snapshot,paths):
        from app.db.oper.downloadhistory import DownloadHistoryOper
        oper=DownloadHistoryOper();row=oper.get_by_hash(snapshot['infohash'])
        identity=json.loads(next(iter(snapshot['targets'])))
        if row is None or row.downloader!=snapshot['downloader'] or row.path!=snapshot['save_path'] or (row.media_source,str(row.media_id))!=(identity[1],identity[2]):return False
        files=oper.get_files_by_hash(snapshot['infohash'],state=1)
        expected=set(paths)
        if self.repository is not None:
            with self.repository.connection() as db:
                previous=db.execute("SELECT a.payload,p.snapshot FROM plan_actions a JOIN plans p ON p.id=a.plan_id WHERE a.kind='ORGANIZE' AND a.state='SUCCEEDED'").fetchall()
            for item in previous:
                old=json.loads(item['snapshot']);payload=json.loads(item['payload'])
                if (old['infohash'],old['downloader'],old['save_path'])==(snapshot['infohash'],snapshot['downloader'],snapshot['save_path']) and payload.get('verb')=='history':expected.update(payload['paths'])
        return expected=={value(f,'fullpath') for f in files} and all(value(f,'downloader')==snapshot['downloader'] for f in files)

    @staticmethod
    def transfer_receipt(source,destination,snapshot):
        from app.db.oper.transferhistory import TransferHistoryOper
        row=TransferHistoryOper().get_success_by_src(source,storage='local')
        identity=json.loads(next(iter(snapshot['targets'])))
        return row is not None and row.src==source and row.dest==destination and row.dest_storage=='local' and row.status is True and (row.media_source,str(row.media_id))==(identity[1],identity[2])

    def transfer(self,src,destination,item,snapshot):
        from app.chain.transfer import TransferChain
        from app.sdk.media import MetaInfoPath
        from app.schemas.file import FileItem
        from app.schemas.system import TransferDirectoryConf
        videos=[f for f in snapshot['torrent_files'] if f['role']=='video' and set(item['targets'])<=set(f['targets'])]
        if len(videos)!=1:
            raise ValueError('UNIQUE_TRANSFER_VIDEO_REQUIRED')
        meta=(self.meta_factory or MetaInfoPath)(Path(videos[0]['path']))
        scopes=[json.loads(k) for k in item['targets']]
        if scopes[0][0]=='电视剧':
            meta.begin_season=scopes[0][3];meta.end_season=None
            meta.begin_episode=min(k[5] for k in scopes);meta.end_episode=max(k[5] for k in scopes) if len(scopes)>1 else None
        # Non-video dependencies retain their real names and relative directories;
        # fonts must keep the names referenced by ASS and licenses remain readable.
        rename=item['role'] in ('video','subtitle')
        target=destination
        if not rename:
            video=self.video_outputs.get(videos[0]['index'])
            if video is None and self.repository is not None:
                with self.repository.connection() as db:
                    row=db.execute("SELECT destination FROM organized_assets WHERE source=? AND state='COMPLETE'",(str(Path(snapshot['save_path'])/videos[0]['path']),)).fetchone()
                video=Path(row[0]) if row else None
            if video is None:raise ValueError('ORGANIZED_VIDEO_RECEIPT_REQUIRED')
            target=video.parent/('Fonts' if src.suffix.casefold() in ('.ttf','.otf','.woff','.woff2') else '')
        directory=TransferDirectoryConf(storage='local',download_path=snapshot['save_path'],library_storage='local',library_path=str(target),transfer_type='copy',overwrite_mode='never',renaming=rename,scraping=False,notify=False,library_type_folder=False,library_category_folder=False)
        chain=TransferChain()
        kwargs=dict(fileitem=FileItem(storage='local',path=str(src),type='file',name=src.name,basename=src.stem,extension=src.suffix.lstrip('.'),size=item['size']),meta=meta,mediainfo=self.media,target_directory=directory,target_storage='local',target_path=target,transfer_type='copy',scrape=False,library_type_folder=False,library_category_folder=False)
        checkpoint=chain.plan_transfer(**kwargs)
        expected=value(checkpoint,'final_target_path')
        if not expected:raise ValueError('TRANSFER_DESTINATION_UNCONFIRMED')
        planned=safe_local(destination,expected,exists=False)
        if planned.exists():raise ValueError('DESTINATION_ALREADY_EXISTS')
        if self.repository is not None:
            with self.repository.connection(write=True) as db:
                db.execute("UPDATE organized_assets SET destination=? WHERE source=? AND state='AUTHORIZED'",(str(planned),str(src)))
        result=chain.transfer(**kwargs)
        if result is None or not value(result,'success'):
            return None
        output=value(value(result,'target_item'),'path')
        if not output or Path(output)!=planned:
            raise ValueError('TRANSFER_TARGET_RECEIPT_MISSING')
        if not self.transfer_receipt(str(src),str(output),snapshot):
            raise ValueError('TRANSFER_HISTORY_NOT_CONFIRMED')
        if item['role']=='video':self.video_outputs[item['index']]=Path(output)
        return Path(output)
