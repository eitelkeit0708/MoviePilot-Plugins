"""Durable strict downloader execution. Every mutation consumes W04 authority."""
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
from threading import RLock

from .candidates import torrent_table, value
from .planner import Authority, encoded, validate_files, asset_table
from .repository import utcnow
from .scheduler import parse, instant

# ponytail: one host-process mutation lock; per-download locks if throughput matters.
MUTATION_LOCK = RLock()


class TransferNotSent(ValueError):
    """A synchronous pre-copy refusal, before the native transfer entry."""


def subtitle_name(item,video,files):
    source=PurePosixPath(item['path']);stem=source.stem
    # Preserve the complete track description, including unknown languages,
    # forced/SDH and same-language editions. Do not guess which token matters.
    if any(f['role']=='subtitle' and set(f['targets'])==set(item['targets']) and PurePosixPath(f['path']).stem.casefold()==stem.casefold() and PurePosixPath(f['path']).with_suffix('')!=source.with_suffix('') for f in files):
        stem+='.'+sha256(str(source.with_suffix('')).encode()).hexdigest()[:12]
    return video.stem+'.'+stem+source.suffix


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
            actual=value(row,'hash_string',value(row,'hashString'));tid=value(row,'id');path=value(row,'download_dir',value(row,'downloadDir'));state=str(value(row,'status','')).lower()
            if type(tid)is not int or tid<0:
                raise ValueError('TR_TASK_ID_INVALID')
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
            # transmission_rpc.File is a NamedTuple: its inherited .index is a
            # tuple method. Each public client has an explicit ID field.
            size=value(row,'size');index=value(row,'index' if self.kind=='qbittorrent' else 'id');name=value(row,'name')
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
        if result is None or str(value(result,'hash_string',value(result,'hashString'))).lower()!=infohash or type(value(result,'id'))is not int or value(result,'id')<0:
            return None
        return str(value(result,'id'))

    def _rpc_id(self,task_id):
        if self.kind=='qbittorrent':return task_id
        # SQLite identity is textual; Transmission distinguishes integer IDs
        # from string hashes and its public host wrapper does not coerce them.
        if type(task_id)is int and task_id>=0:return task_id
        if isinstance(task_id,str) and len(task_id) in (40,64) and all(c in '0123456789abcdef' for c in task_id.lower()):return task_id
        if isinstance(task_id,str) and task_id.isascii() and task_id.isdecimal():return int(task_id)
        raise ValueError('TR_TASK_ID_INVALID')

    def pause(self,task_id):
        return self.instance.stop_torrents(ids=self._rpc_id(task_id))

    def resume(self,task_id):
        return self.instance.start_torrents(ids=self._rpc_id(task_id))

    def remove(self,task_id):
        # Physical source-data cleanup has a separate permission and nofollow gate.
        return self.instance.delete_torrents(delete_file=False,ids=self._rpc_id(task_id))

    def select_files(self,task_id,indices,wanted):
        if not indices or type(wanted)is not bool:
            raise ValueError('EXACT_SELECTION_REQUIRED')
        if self.kind=='qbittorrent':
            return self.instance.set_files(torrent_hash=task_id,file_ids=indices,priority=int(wanted))
        tid=self._rpc_id(task_id)
        return self.instance.set_files(tid,indices) if wanted else self.instance.set_unwanted_files(tid,indices)


class Exclusions:
    def __init__(self, repository):
        self.repository=repository

    def add(self, exclusion_id, criteria, *, reason, expires_at=None, db=None):
        if not exclusion_id or not reason or not isinstance(criteria,dict) or not criteria or set(criteria)-{'candidate_key','infohash','targets','content_sha1','condition'}:
            raise ValueError('explicit exclusion identity/scope required')
        if expires_at is not None:
            parse(expires_at)
        if criteria.get('condition'):
            from .policy import import_predicates
            import_predicates({'exclusion':criteria['condition']})
        encoded(criteria)
        from contextlib import nullcontext
        with MUTATION_LOCK, (self.repository.connection(write=True) if db is None else nullcontext(db)) as db:
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

    def matches_target(self, target):
        """Match work scope against exact task or episode-unit exclusions."""
        if self.matches({'targets': [target.key]}):
            return True
        expected = json.loads(target.key)
        with self.repository.connection() as db:
            rows = db.execute('SELECT criteria,expires_at FROM exclusions WHERE active=1').fetchall()
        for row in rows:
            if row['expires_at'] and parse(row['expires_at']) <= instant():
                continue
            criteria = json.loads(row['criteria'])
            if set(criteria) != {'targets'}:
                continue
            for key in criteria['targets']:
                try:
                    unit = json.loads(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(unit, list) and len(unit) == 6 and unit[:5] == expected:
                    return True
        return False


class StrictExecutor:
    def __init__(self, repository, client_factory, *, revalidate, verify_torrent=torrent_table,dispatch_gate=None):
        if not callable(revalidate):
            raise ValueError('CURRENT_CANDIDATE_REVALIDATOR_REQUIRED')
        self.repository=repository
        self.authority=Authority(repository)
        self.clients=client_factory
        self.verify_torrent=verify_torrent
        self.revalidate=revalidate
        self.exclusions=Exclusions(repository)
        self.dispatch_gate=dispatch_gate

    def _read(self,function,*args):
        if self.dispatch_gate:self.dispatch_gate()
        return function(*args)

    def _plan(self, plan_id):
        plan=self.authority.plan(plan_id)
        snapshot=plan['snapshot']
        if [f['index'] for f in snapshot['torrent_files']]!=list(range(len(snapshot['torrent_files']))):
            raise ValueError('CANONICAL_TORRENT_INDICES_REQUIRED')
        active={t['target_key']:t for t in plan['targets'] if t['state']=='ACTIVE'}
        indices=[i for i in snapshot['selected_indices'] if set(asset_table(snapshot)[i]['targets'])<=set(active)]
        if not indices:
            raise ValueError('NO_ACTIVE_SAFE_FILES')
        keys={k for i in indices for k in asset_table(snapshot)[i]['targets']}
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
            if 'evidence' in changes:
                old=db.execute('SELECT evidence FROM managed_downloads WHERE downloader=? AND infohash=?',(s['downloader'],s['infohash'])).fetchone()
                changes['evidence']={**(json.loads(old[0]) if old else {}),**changes['evidence']}
            db.execute('UPDATE managed_downloads SET '+','.join(k+'=?' for k in changes)+',updated_at=? WHERE downloader=? AND infohash=?',(*[encoded(v) if k=='evidence' else v for k,v in changes.items()],utcnow(),s['downloader'],s['infohash']))

    def _cohort(self,s):
        token=self.exclusions.token()
        refs=self.authority.download_references(s['downloader'],s['infohash'],s['save_path'])
        execution=[]
        for ref in refs:
            plan,_,indices,vector=self._plan(ref['plan_id'])
            files=plan['snapshot']['torrent_files'];physical=[i for i in indices if i<len(files)]
            keys={key for i in physical for key in files[i]['targets']}
            if physical!=ref['indices'] or {k:vector[k] for k in keys}!=ref['vector']:
                raise ValueError('SHARED_AUTHORITY_CHANGED')
            execution.append(dict(plan_id=plan['id'],indices=indices,vector=vector))
        return refs,token,execution

    def _new_cycle(self,s):
        owned=self._owned(s)
        cycle=json.loads(owned['evidence']).get('cycle',0)+1
        self._save(s,state='PREPARING',evidence={'cycle':cycle})

    def _prepare_cycle(self,s,owned,task,*,selection_changed=False):
        # Consume the old running state before either public entry can replace
        # it with PAUSED_VERIFIED. PREPARING consumes this transition only once.
        if owned['state'] in ('RUNNING','PAUSED_VERIFIED') and (owned['state']=='RUNNING' or task['state']!='PAUSED' or selection_changed):
            self._new_cycle(s)

    def _receipt(self,action,outcome,evidence):
        """Only the persisted original cohort can receive a physical RPC receipt."""
        shared=json.loads(action['payload']).get('shared')
        ids=[action['id']] if not shared else [shared['action_id']+':'+r['plan_id'] for r in shared['references']]
        for action_id in ids:self.authority.record_result(action_id,outcome,evidence)

    def _mutation(self,plan,indices,vector,verb,kind,fn,payload):
        if self.dispatch_gate:self.dispatch_gate()
        s=plan['snapshot']
        if kind in ('ADD','SET_WANTED','RESUME'):
            refs,token,execution=self._cohort(s)
            if not any(ref==dict(plan_id=plan['id'],indices=indices,vector=vector) for ref in execution):
                raise ValueError('SHARED_AUTHORITY_CHANGED')
            union=sorted({i for ref in refs for i in ref['indices']})
            if 'wanted_indices' in payload and payload['wanted_indices']!=union:
                raise ValueError('SHARED_SELECTION_CHANGED')
            owned=self._owned(s)
            payload=dict(payload,verb=verb,cycle=json.loads(owned['evidence']).get('cycle',0),wanted_indices=union)
            if execution!=refs:payload=dict(payload,execution=execution)
            family=[s['downloader'],s['infohash'],s['save_path']]
            action_id='exec:'+sha256(encoded([family,refs,payload]).encode()).hexdigest()
            actions=self.authority.begin_shared_attempt(action_id,family,refs,kind,payload,exclusion_token=token)
            action=next(a for a in actions if a['plan_id']==plan['id'])
            action_id=action['id']
        else:
            self._fresh_candidate(plan,vector,payload.get('content_sha1'))
            action_id='exec:'+sha256(encoded([plan['id'],verb,payload,vector]).encode()).hexdigest()
            action=self.authority.begin_attempt(action_id,plan['id'],vector,kind,indices,dict(verb=verb,**payload))
        if not action['dispatch']:
            return action['state']=='SUCCEEDED',action_id,None
        if self.dispatch_gate:
            try:self.dispatch_gate()
            except ValueError:
                self._receipt(action,'FAILED',{'code':'RUNTIME_DISPATCH_BLOCKED','not_sent':True})
                return False,action_id,None
        try:
            result=fn()
        except TransferNotSent:
            self._receipt(action,'FAILED',{'code':'TRANSFER_NOT_SENT','stage':'before native transfer'})
            raise ValueError('DESTINATION_ALREADY_EXISTS')
        except Exception:
            self._receipt(action,'UNKNOWN',{'code':'CLIENT_RESPONSE_UNKNOWN'})
            return False,action_id,None
        outcome='SUCCEEDED' if result is not None and result is not False else 'UNKNOWN'
        self._receipt(action,outcome,{'accepted':outcome=='SUCCEEDED'})
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
        for asset in s.get('local_assets',[]):
            item=asset['file'];path=safe_local(s['save_path'],Path(s['save_path'])/item['path'])
            if path.stat().st_size!=item['size'] or path.stat().st_mtime_ns!=asset['mtime_ns'] or digest(path)!=asset['sha256']:raise ValueError('LOCAL_ASSET_CHANGED')
            mapping[item['index']]=dict(id=item['index'],wanted=False,completed=item['size'],path=item['path'],size=item['size'])
        return mapping

    @staticmethod
    def _task(s,task):
        if not task or task.get('infohash','').lower()!=s['infohash'] or task.get('save_path')!=s['save_path'] or not task.get('id'):
            raise ValueError('TASK_IDENTITY_LAYOUT_MISMATCH')
        return task

    def _selection(self,s,client):
        mapping=self._table(s,self._read(client.files,s['infohash']))
        union=set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))
        if not union:
            if s.get('local_assets') and all(i>=len(s['torrent_files']) for i in s['selected_indices']):return mapping,union
            raise ValueError('EMPTY_SELECTION')
        wanted={mapping[i]['id'] for i in union}
        if {row['id'] for row in mapping.values() if row['wanted']}!=wanted:
            raise ValueError('WANTED_READBACK_MISMATCH')
        return mapping,union

    def _prepare_selection(self,plan,s,indices,vector,client,owned,task,*,selection_changed):
        self._prepare_cycle(s,owned,task,selection_changed=selection_changed)
        if task['state']!='PAUSED':
            ok,_,_=self._mutation(plan,indices,vector,'pause','SET_WANTED',lambda:client.pause(task['id']),{'id':task['id']})
            if not ok or self._task(s,self._read(client.task,s['infohash']))['state']!='PAUSED':
                raise ValueError('PAUSE_NOT_CONFIRMED')
        mapping=self._table(s,self._read(client.files,s['infohash']))
        union=set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))
        if not {i for i in indices if i<len(s['torrent_files'])}<=union:
            raise ValueError('SELECTION_AUTHORITY_CHANGED')
        wanted=sorted(mapping[i]['id'] for i in union)
        unwanted=sorted(mapping[i]['id'] for i in range(len(s['torrent_files'])) if i not in union)
        if {r['id'] for r in mapping.values() if r['wanted']}!=set(wanted):
            for ids,enabled in ((unwanted,False),(wanted,True)):
                if not ids:continue
                if set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))!=union:
                    raise ValueError('SHARED_SELECTION_CHANGED')
                ok,_,_=self._mutation(plan,indices,vector,'select:'+str(enabled),'SET_WANTED',lambda:client.select_files(task['id'],ids,enabled),{'id':task['id'],'indices':ids,'wanted':enabled,'wanted_indices':sorted(union)})
                if not ok:
                    raise ValueError('SELECTION_OUTCOME_UNKNOWN')
        self._selection(s,client)
        if self._task(s,self._read(client.task,s['infohash']))['state']!='PAUSED':
            raise ValueError('TASK_NOT_PAUSED')
        self._save(s,state='PAUSED_VERIFIED',evidence={'wanted':wanted,'unwanted':unwanted})
        return wanted

    def execute(self,plan_id,content,*,resume=False):
        with MUTATION_LOCK:
            try:
                plan,s,indices,vector=self._plan(plan_id)
                infohash,table=self.verify_torrent(content)
                if infohash.lower()!=s['infohash'] or table!=[(f['path'],f['size']) for f in s['torrent_files']]:
                    raise ValueError('TORRENT_CHANGED')
                validate_files(asset_table(s),indices)
                client=self.clients(s['downloader'])
                owned=self._owned(s)
                task=self._read(client.task,s['infohash'])
                if s.get('local_assets'):
                    self._task(s,task)
                    if not owned or str(task['id'])!=owned['client_id']:raise ValueError('DOWNLOAD_OWNERSHIP_UNCONFIRMED')
                    if owned['state'] in ('ADD_INTENT','UNKNOWN') or owned['save_path']!=s['save_path'] or json.loads(owned['file_table'])!=[list(x) for x in table]:raise ValueError('DOWNLOAD_OWNERSHIP_UNCONFIRMED')
                    mapping=self._table(s,self._read(client.files,s['infohash']))
                    if task['state'] in ('CHECKING','FAILED','DISCONNECTED') or any(mapping[i]['completed']!=asset_table(s)[i]['size'] for i in indices):return dict(state='WAITING_ASSETS')
                    physical=[i for i in indices if i<len(s['torrent_files'])]
                    union=set(self.authority.active_files(s['downloader'],s['infohash'],s['save_path']))
                    if physical and {i for i,row in mapping.items() if row['wanted']}!=union:
                        self._prepare_selection(plan,s,indices,vector,client,owned,task,selection_changed=True)
                    self._selection(s,client)
                    return dict(state='RUNNING')
                if owned is None:
                    if task is not None:
                        raise ValueError('UNMANAGED_SAME_HASH')
                    if self.dispatch_gate:self.dispatch_gate()
                    marker='subscribetter:'+sha256((s['downloader']+s['infohash']).encode()).hexdigest()[:24]
                    with self.repository.connection(write=True) as db:
                        db.execute('INSERT INTO managed_downloads(downloader,infohash,save_path,file_table,marker,add_action,state,updated_at) VALUES(?,?,?,?,?,?,?,?)',(s['downloader'],s['infohash'],s['save_path'],encoded(table),marker,plan_id,'ADD_INTENT',utcnow()))
                    ok,action,result=self._mutation(plan,indices,vector,'add','ADD',lambda:client.add(content,s['infohash'],s['save_path'],marker),{'infohash':s['infohash'],'save_path':s['save_path'],'marker':marker})
                    if not ok or not result:
                        self._save(s,state='UNKNOWN',evidence={'action_id':action,'code':'ADD_OUTCOME_UNKNOWN'})
                        return {'state':'UNKNOWN','reason':'ADD_OUTCOME_UNKNOWN'}
                    self._save(s,client_id=str(result),state='ADDED',evidence={'action_id':action})
                    owned=self._owned(s)
                    task=self._read(client.task,s['infohash'])
                elif owned['state'] in ('ADD_INTENT','UNKNOWN') or not owned['client_id']:
                    # No response cannot establish new ownership; never adopt a coincident manual task.
                    return {'state':'UNKNOWN','reason':'ADD_OWNERSHIP_UNCONFIRMED'}
                if owned['save_path']!=s['save_path'] or json.loads(owned['file_table'])!=[list(x) for x in table]:
                    raise ValueError('SHARED_LAYOUT_CONFLICT')
                task=self._task(s,task)
                if str(task['id'])!=owned['client_id']:
                    raise ValueError('CLIENT_TASK_ID_CHANGED')
                mapping=self._table(s,self._read(client.files,s['infohash']))
                refs,_,_=self._cohort(s)
                union={i for ref in refs for i in ref['indices']}
                unchanged={i for i,r in mapping.items() if r['wanted']}==union
                if owned['state']=='RUNNING' and task['state'] in ('PAUSED','CHECKING','LIMITED','DISCONNECTED'):
                    return {'state':'WAITING_ASSETS','reason':'DOWNLOADER_'+task['state']}
                if resume and unchanged and task['state'] in ('DOWNLOADING','QUEUED','COMPLETED'):
                    self._save(s,state='RUNNING',evidence={'actual_state':task['state']})
                    self.authority.set_transfer_phase(plan_id,vector,'DOWNLOADING')
                    return {'state':'RUNNING','actual_state':task['state'],'infohash':s['infohash']}
                wanted=self._prepare_selection(plan,s,indices,vector,client,owned,task,selection_changed=not unchanged)
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
                task=self._task(s,self._read(client.task,s['infohash']))
                if str(task['id'])!=owned['client_id']:
                    raise ValueError('CLIENT_TASK_ID_CHANGED')
                _,union=self._selection(s,client)
                self._cohort(s)
                if task['state'] in ('DOWNLOADING','QUEUED','COMPLETED'):
                    self._save(s,state='RUNNING',evidence={'actual_state':task['state']})
                    self.authority.set_transfer_phase(plan_id,vector,'DOWNLOADING')
                    return {'state':'RUNNING','actual_state':task['state'],'infohash':s['infohash']}
                self._prepare_cycle(s,owned,task)
                ok,_,_=self._mutation(plan,indices,vector,'resume','RESUME',lambda:client.resume(task['id']),{'id':task['id'],'wanted_indices':sorted(union)})
                state=self._task(s,self._read(client.task,s['infohash']))['state']
                if not ok or state not in ('DOWNLOADING','QUEUED','COMPLETED'):
                    self._save(s,state='PAUSED_VERIFIED',evidence={'code':'RESUME_NOT_ACCEPTED','actual_state':state})
                    return {'state':'UNKNOWN','reason':'RESUME_NOT_ACCEPTED','actual_state':state}
                self._save(s,state='RUNNING',evidence={'actual_state':state})
                self.authority.set_transfer_phase(plan_id,vector,'DOWNLOADING')
                return {'state':'RUNNING','actual_state':state,'infohash':s['infohash']}
            except Exception:
                return {'state':'BLOCKED','reason':'RESUME_CHECK_FAILED'}

    def sample(self,plan_id,*,targets=None):
        plan,s,indices,_=self._plan(plan_id)
        if targets is not None:
            requested=set(targets);table=asset_table(s)
            indices=[i for i in indices if requested.intersection(table[i]['targets'])]
            if not requested or any(not set(table[i]['targets'])<=requested for i in indices) or {k for i in indices for k in table[i]['targets']}!=requested:
                raise ValueError('EXACT_SAFE_PROGRESS_SCOPE_REQUIRED')
        owned=self._owned(s)
        if not owned or not owned['client_id']:
            raise ValueError('DOWNLOAD_OWNERSHIP_UNCONFIRMED')
        client=self.clients(s['downloader'])
        task=self._task(s,self._read(client.task,s['infohash']))
        if str(task['id'])!=owned['client_id']:
            raise ValueError('CLIENT_TASK_ID_CHANGED')
        mapping=self._table(s,self._read(client.files,s['infohash']))
        stats={i:{'downloaded_bytes':mapping[i].get('completed'),'speed':None} for i in indices}
        previous=self.authority.progress(plan_id,indices)
        now=instant()
        if previous and task['state']=='DOWNLOADING' and previous['status']=='DOWNLOADING':
            elapsed=(now-parse(previous['sampled_at'])).total_seconds()
            if elapsed>0:
                for i,row in stats.items():
                    before=previous['files'].get(str(i),{}).get('downloaded_bytes');after=row['downloaded_bytes']
                    if type(before)is int and type(after)is int and after>=before:row['speed']=(after-before)/elapsed
        complete=all(stats[i]['downloaded_bytes']==asset_table(s)[i]['size'] for i in stats)
        status='COMPLETED' if complete and task['state'] not in ('CHECKING','DISCONNECTED','FAILED') else task['state']
        return self.authority.record_progress(plan_id,indices,stats,torrent=task,status=status,now=now)

    def reconcile(self,plan_id):
        """Read only at the client: settle known intents, never guess or retransmit."""
        with MUTATION_LOCK:
            plan=self.authority.plan(plan_id);s=plan['snapshot'];owned=self._owned(s)
            if not owned:return {'state':'UNKNOWN','reason':'NO_OWNERSHIP_INTENT'}
            client=self.clients(s['downloader']);task=self._task(s,self._read(client.task,s['infohash']))
            mapping=self._table(s,self._read(client.files,s['infohash']))
            if not owned['client_id']:
                if owned['marker'] not in task.get('markers',[]) or task['state']!='PAUSED':
                    return {'state':'UNKNOWN','reason':'ADD_IDENTITY_UNPROVEN'}
                with self.repository.connection() as db:
                    adds=db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind='ADD' AND state IN ('IN_FLIGHT','UNKNOWN')",(owned['add_action'],)).fetchall()
                if len(adds)!=1 or json.loads(adds[0]['payload']).get('marker')!=owned['marker']:
                    return {'state':'UNKNOWN','reason':'ADD_INTENT_UNPROVEN'}
                self._receipt(adds[0],'SUCCEEDED',{'evidence':'exact marker/hash/layout/full table paused readback','client_id':task['id']})
                self._save(s,client_id=task['id'],state='ADDED',evidence={'reconciled_add':adds[0]['id']})
            elif owned['client_id']!=task['id']:
                raise ValueError('CLIENT_TASK_ID_CHANGED')
            with self.repository.connection() as db:
                pending=[r for r in db.execute("SELECT a.*,p.snapshot AS plan_snapshot FROM plan_actions a JOIN plans p ON p.id=a.plan_id WHERE a.kind IN ('SET_WANTED','RESUME') AND a.state IN ('IN_FLIGHT','UNKNOWN')") if all(json.loads(r['plan_snapshot'])[k]==s[k] for k in ('downloader','infohash','save_path'))]
            actual={r['id']:r for r in mapping.values()};remaining=[]
            for action in pending:
                payload=json.loads(action['payload']);verb=payload.get('verb');matched=False
                if verb=='pause':matched=task['state']=='PAUSED'
                elif verb.startswith('select:'):
                    matched=task['state']=='PAUSED' and all(i in actual and actual[i]['wanted']==payload['wanted'] for i in payload['indices'])
                elif verb=='resume':
                    try:
                        wanted=payload.get('shared',{}).get('wanted')
                        if wanted is None:_,wanted=self._selection(s,client)
                        matched={i for i,r in mapping.items() if r['wanted']}==set(wanted) and task['state'] in ('DOWNLOADING','QUEUED','COMPLETED')
                    except ValueError:matched=False
                if matched:self._receipt(action,'SUCCEEDED',{'evidence':'exact task and whole file selection readback','actual_state':task['state']})
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

    def _source_hashes(self,plan_id,index,path,root):
        from .delivery import LocalSource
        key='organize-source-hash:'+encoded([plan_id,index])
        with LocalSource(path,[root]) as source:
            cached=self.repository.setting(key) if os.name=='posix' else None
            reusable=(isinstance(cached,dict) and cached.get('snapshot')==source.snapshot
                and all(isinstance(cached.get(k),str) and len(cached[k])==n
                        and all(c in '0123456789abcdef' for c in cached[k])
                        for k,n in (('sha1',40),('sha256',64))))
            if reusable:
                source.check();return cached['sha256'],cached['sha1']
            content,strong=source.hash();source.check()
            if os.name=='posix':
                # Completed work survives a later deadline; this stores no authority.
                self.repository.setting(key,dict(snapshot=source.snapshot,sha1=content,sha256=strong))
            return strong,content

    def organize(self,plan_id,target_root):
        with MUTATION_LOCK:
            try:
                plan,s,indices,vector=self.executor._plan(plan_id)
                target=Path(target_root).absolute()
                safe_local(target,target,exists=False)
                client=self.executor.clients(s['downloader'])
                task=self.executor._task(s,self.executor._read(client.task,s['infohash']))
                mapping,_=self.executor._selection(s,client)
                if task['state'] in ('CHECKING','FAILED','DISCONNECTED') or any(mapping[i].get('completed')!=asset_table(s)[i]['size'] for i in indices):
                    return {'state':'WAITING_ASSETS'}
                paths={i:safe_local(self.source_root(s),Path(self.source_root(s))/asset_table(s)[i]['path']) for i in indices}
                asset_digests={i:self._source_hashes(plan_id,i,p,self.source_root(s)) for i,p in paths.items()}
                hashes={i:v[0] for i,v in asset_digests.items()}
                content_sha1=[v[1] for v in asset_digests.values()]
                if any(paths[i].stat().st_size!=asset_table(s)[i]['size'] for i in indices):
                    raise ValueError('COMPLETED_ASSET_SIZE_MISMATCH')
                self.executor.sample(plan_id)
                history_snapshot=dict(s,selected_indices=indices,targets={k:s['targets'][k] for k in vector})
                ok,_,_=self.executor._mutation(plan,indices,vector,'history','ORGANIZE',lambda:self.host.history(history_snapshot,paths),{'paths':[str(paths[i]) for i in indices],'content_sha1':content_sha1})
                if not ok:
                    return {'state':'UNKNOWN','reason':'HISTORY_OUTCOME_UNKNOWN'}
                outputs=[]
                for index in sorted(indices,key=lambda i:(asset_table(s)[i]['role']!='video',i)):
                    with self.repository.connection() as db:
                        row=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(plan_id,index)).fetchone()
                    if row and row['state']=='COMPLETE':
                        output=safe_local(target,row['destination'])
                        if output.stat().st_size!=row['size'] or digest(output)!=row['sha256']:
                            raise ValueError('ORGANIZED_ASSET_CHANGED')
                        outputs.append(str(output));continue
                    item=asset_table(s)[index];src=paths[index]
                    evidence=dict(vector=vector,target_root=str(target),indices=indices,source_mtime_ns=src.stat().st_mtime_ns,exclusions_token=self.executor.exclusions.token())
                    with self.repository.connection(write=True) as db:
                        db.execute('INSERT INTO organized_assets VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plan_id,file_index) DO UPDATE SET evidence=excluded.evidence',
                            (plan_id,index,str(src),None,item['size'],hashes[index],'AUTHORIZED',encoded(evidence)))
                    payload={'file_index':index,'source':str(src),'target_root':str(target),'sha256':hashes[index],'content_sha1':content_sha1}
                    transfer=lambda:self.host.transfer(src,target,item,s)
                    adopted=False
                    if hasattr(self.host,'prepare_transfer'):
                        # Native planning is pure. Reject predictable collisions before
                        # creating a copy attempt, instead of mislabelling them UNKNOWN.
                        prepared=self.host.prepare_transfer(src,target,item,s)
                        if prepared['planned'].exists():
                            output=safe_local(target,prepared['planned'])
                            receipt=getattr(self.host,'transfer_receipt',None)
                            if (output.stat().st_size!=item['size'] or not receipt
                                    or not receipt(str(src),str(output),s)
                                    or self._source_hashes(plan_id,'adopt:'+str(index),output,target)[0]!=hashes[index]):
                                raise ValueError('DESTINATION_ALREADY_EXISTS')
                            adopted=True
                        token=self.executor.exclusions.token()
                        self.executor._fresh_candidate(plan,vector,content_sha1)
                        with self.repository.connection() as db:
                            old=[dict(a) for a in db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind='ORGANIZE' AND state='UNKNOWN'",(plan_id,)) if json.loads(a['payload']).get('file_index')==index and 'naming_revision' not in json.loads(a['payload'])]
                        if len(old)==1 and item['role']=='subtitle':
                            proof=self.host.legacy_preflight_proof(plan_id,src,target,item,s)
                            if proof:self.executor.authority.settle_legacy_preflight(old[0]['id'],vector,proof,exclusion_token=token)
                        payload.update(naming_revision=prepared['naming_revision'],planned_destination=str(prepared['planned']),adopted_native_receipt=adopted)
                        if adopted:
                            def transfer():
                                output=safe_local(target,prepared['planned'])
                                if (output.stat().st_size!=item['size']
                                        or self._source_hashes(plan_id,'adopt:'+str(index),output,target)[0]!=hashes[index]
                                        or not self.host.transfer_receipt(str(src),str(output),s)):
                                    raise ValueError('ADOPTED_TRANSFER_CHANGED')
                                return output
                        else:transfer=lambda:self.host.transfer_prepared(prepared)
                    ok,action,result=self.executor._mutation(plan,indices,vector,'organize:'+str(index),'ORGANIZE',transfer,payload)
                    if not ok or result is None:
                        if self.executor.authority.action(action)['state']=='FAILED':
                            return {'state':'BLOCKED','reason':'ORGANIZE_NOT_SENT_REPLAN_REQUIRED','action_id':action}
                        return {'state':'UNKNOWN','reason':'ORGANIZE_OUTCOME_UNKNOWN','action_id':action}
                    output=safe_local(target,result)
                    output_hash=self._source_hashes(plan_id,'adopt:'+str(index),output,target)[0] if adopted else digest(output)
                    if output.stat().st_size!=item['size'] or output_hash!=hashes[index]:
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
        with MUTATION_LOCK:
            return self._reconcile(plan_id)

    def _reconcile(self,plan_id):
        plan=self.executor.authority.plan(plan_id);s=plan['snapshot'];settled=[]
        with self.repository.connection() as db:
            rows=db.execute("SELECT * FROM organized_assets WHERE plan_id=? AND state='AUTHORIZED'",(plan_id,)).fetchall()
            actions=db.execute("SELECT * FROM plan_actions WHERE plan_id=? AND kind='ORGANIZE' AND state IN ('UNKNOWN','IN_FLIGHT','SUCCEEDED')",(plan_id,)).fetchall()
        for action in actions:
            payload=json.loads(action['payload'])
            if payload.get('verb')!='history' or action['state']=='SUCCEEDED':continue
            history_snapshot=dict(s,selected_indices=json.loads(action['files']),targets={k:s['targets'][k] for k in json.loads(action['targets'])})
            complete=self.host.history_receipt(history_snapshot,payload['paths'])
            if not complete and action['state']=='UNKNOWN' and hasattr(self.host,'history_missing'):
                try:
                    token=self.executor.exclusions.token()
                    current,_,_,vector=self.executor._plan(plan_id)
                    self.executor._fresh_candidate(current,vector,payload.get('content_sha1'))
                    missing=self.host.history_missing(history_snapshot,payload['paths'])
                    repair=self.executor.authority.begin_history_repair(action['id'],vector,missing,exclusion_token=token)
                    if repair['dispatch']:
                        try:
                            complete=self.host.repair_history(history_snapshot,payload['paths'],missing)
                            self.executor.authority.record_result(repair['id'],'SUCCEEDED' if complete else 'UNKNOWN',{'code':'LOCAL_HISTORY_REPAIR_RETURNED','evidence':'bounded missing history rows and full readback'})
                        except Exception:
                            self.executor.authority.record_result(repair['id'],'UNKNOWN',{'code':'LOCAL_HISTORY_REPAIR_RETURNED'})
                except (ValueError,RuntimeError) as error:
                    if str(error)=='HISTORY_REPAIR_EXHAUSTED':
                        return {'state':'BLOCKED','reason':'HISTORY_REPAIR_EXHAUSTED','settled_actions':settled}
            if complete:
                for prior in actions:
                    data=json.loads(prior['payload'])
                    if prior['id']==action['id'] or data.get('original_action')==action['id']:
                        self.executor.authority.record_result(prior['id'],'SUCCEEDED',{'evidence':'public exact download history readback'});settled.append(prior['id'])
        for row in rows:
            if not row['destination']:continue
            evidence=json.loads(row['evidence'])
            try:
                destination=safe_local(evidence['target_root'],row['destination'])
                if destination.stat().st_size!=row['size'] or digest(destination)!=row['sha256'] or not self.host.transfer_receipt(row['source'],str(destination),s):continue
                matched=[a for a in actions if all(json.loads(a['payload']).get(k)==v for k,v in dict(verb='organize:'+str(row['file_index']),sha256=row['sha256'],source=row['source'],target_root=evidence['target_root']).items()) and json.loads(a['targets'])==evidence['vector']]
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
                # Native extras ignore overwrite_mode=never; reject occupied targets
                # ponytail: atomic no-replace after this check needs host support.
                if safe_local(evidence['target_root'],destination,exists=False).exists():
                    continue
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
            oper.add(path=s['save_path'],type=identities[0][0],title=self.media.title,year=str(self.media.year or ''),media_source=source.value,media_id=str(mid),seasons=','.join(sorted({f'S{i[3]:02}' for i in identities if i[3] is not None})),episodes=','.join(sorted({f'E{i[5]:02}' for i in identities if i[5] is not None})),episode_group=identities[0][4],downloader=s['downloader'],download_hash=s['infohash'],torrent_name=PurePosixPath(asset_table(s)[0]['path']).parts[0],username='subscriBetter',note={'strict_plan':True,'strict_scope':scope,'candidate_key':s['candidate_key']})
        selected=[str(paths[i]) for i in s['selected_indices']]
        return self.repair_history(s,selected,self.history_missing(s,selected))

    def _history_state(self,snapshot,paths):
        """Validate the header and every existing file before any compensation."""
        from app.db.oper.downloadhistory import DownloadHistoryOper
        oper=DownloadHistoryOper();row=oper.get_by_hash(snapshot['infohash'])
        identity=json.loads(next(iter(snapshot['targets'])))
        if row is None or row.downloader!=snapshot['downloader'] or row.path!=snapshot['save_path'] or (row.media_source,str(row.media_id))!=(identity[1],identity[2]):
            raise ValueError('HISTORY_IDENTITY_CONFLICT')
        expected={}
        def include(s,selected):
            if len(selected)!=len(s['selected_indices']) or len(set(selected))!=len(selected):raise ValueError('HISTORY_SCOPE_CONFLICT')
            for i,path in zip(s['selected_indices'],selected):
                item=dict(downloader=s['downloader'],download_hash=s['infohash'],fullpath=path,savepath=s['save_path'],filepath=asset_table(s)[i]['path'],torrentname=PurePosixPath(asset_table(s)[i]['path']).parts[0],state=1)
                if path in expected and expected[path]!=item:raise ValueError('HISTORY_SCOPE_CONFLICT')
                expected[path]=item
        include(snapshot,paths)
        if self.repository is not None:
            with self.repository.connection() as db:
                previous=db.execute("SELECT a.payload,a.files,p.snapshot FROM plan_actions a JOIN plans p ON p.id=a.plan_id WHERE a.kind='ORGANIZE' AND a.state='SUCCEEDED'").fetchall()
            for prior in previous:
                old=json.loads(prior['snapshot']);payload=json.loads(prior['payload'])
                if all(old[k]==snapshot[k] for k in ('infohash','downloader','save_path')) and payload.get('verb')=='history':include(dict(old,selected_indices=json.loads(prior['files'])),payload['paths'])
        files=oper.get_files_by_hash(snapshot['infohash'],state=1)
        present=[value(f,'fullpath') for f in files]
        if len(set(present))!=len(present) or any(value(f,'fullpath') not in expected or any(value(f,k)!=v for k,v in expected[value(f,'fullpath')].items()) for f in files):
            raise ValueError('HISTORY_FILES_CONFLICT')
        missing=set(expected)-set(present)
        if not missing<=set(paths):raise ValueError('PRIOR_HISTORY_FILES_MISSING')
        return oper,expected,sorted(missing)

    def history_missing(self,snapshot,paths):
        return self._history_state(snapshot,paths)[2]

    def repair_history(self,snapshot,paths,missing):
        oper,expected,actual=self._history_state(snapshot,paths)
        if sorted(missing)!=actual:raise ValueError('HISTORY_ROWS_CHANGED')
        if actual:oper.add_files([expected[path] for path in actual])
        return self.history_receipt(snapshot,paths)

    def history_receipt(self,snapshot,paths):
        try:return not self.history_missing(snapshot,paths)
        except ValueError:return False

    @staticmethod
    def transfer_receipt(source,destination,snapshot):
        from app.db.oper.transferhistory import TransferHistoryOper
        row=TransferHistoryOper().get_success_by_src(source,storage='local')
        identity=json.loads(next(iter(snapshot['targets'])))
        return row is not None and row.src==source and row.dest==destination and row.dest_storage=='local' and row.status is True and (row.media_source,str(row.media_id))==(identity[1],identity[2])

    def _video_output(self,video,snapshot,destination):
        output=self.video_outputs.get(video['index'])
        if output is None and self.repository is not None:
            with self.repository.connection() as db:
                row=db.execute("SELECT destination FROM organized_assets WHERE source=? AND state='COMPLETE'",(str(Path(snapshot['save_path'])/video['path']),)).fetchone()
            output=Path(row[0]) if row else None
        if output is None and all(v['action']=='SIDECAR_SUPPLEMENT' for v in snapshot['targets'].values()):
            # Pure native name planning, no video copy/download. Archive's final
            # confirmation still binds the sidecar to the proved current video.
            output=self.prepare_transfer(Path(snapshot['save_path'])/video['path'],destination,video,snapshot)['planned']
        if output is None:raise ValueError('ORGANIZED_VIDEO_RECEIPT_REQUIRED')
        return safe_local(destination,output,exists=False)

    def prepare_transfer(self,src,destination,item,snapshot,*,legacy=False):
        """Public pure planning; no asset reservation or copy has happened yet."""
        from app.chain.transfer import TransferChain
        from app.sdk.media import MetaInfoPath
        from app.schemas.file import FileItem
        from app.schemas.system import TransferDirectoryConf
        unique=item['role'] in ('video','subtitle')
        rename=item['role']=='video' or (legacy and item['role']=='subtitle')
        videos=[f for f in asset_table(snapshot) if f['role']=='video' and (set(item['targets'])<=set(f['targets']) if unique else set(item['targets'])&set(f['targets']))]
        if not videos or (unique and len(videos)!=1) or not set(item['targets'])<={k for video in videos for k in video['targets']}:
            raise ValueError('UNIQUE_TRANSFER_VIDEO_REQUIRED')
        meta=(self.meta_factory or MetaInfoPath)(Path(videos[0]['path']))
        scopes=[json.loads(k) for k in item['targets']]
        if scopes[0][0]=='电视剧':
            meta.begin_season=scopes[0][3];meta.end_season=None
            meta.begin_episode=min(k[5] for k in scopes);meta.end_episode=max(k[5] for k in scopes) if len(scopes)>1 else None
        # Non-video dependencies retain their real names and relative directories;
        # fonts must keep the names referenced by ASS and licenses remain readable.
        target=destination;name=src.name
        if item['role']=='subtitle' and not legacy:
            video=self._video_output(videos[0],snapshot,destination)
            target=video.parent;name=subtitle_name(item,video,asset_table(snapshot))
        elif not rename:
            parents={self._video_output(consumer,snapshot,destination).parent for consumer in videos}
            # The configured host season layout puts these consumers together.
            # An arbitrary ancestor is not proof that subtitles can find a font.
            if len(parents)!=1:raise ValueError('SHARED_DEPENDENCY_DIRECTORY_CONFLICT')
            target=parents.pop()/('Fonts' if src.suffix.casefold() in ('.ttf','.otf','.woff','.woff2') else '')
        directory=TransferDirectoryConf(storage='local',download_path=snapshot['save_path'],library_storage='local',library_path=str(target),transfer_type='copy',overwrite_mode='never',renaming=rename,scraping=False,notify=False,library_type_folder=False,library_category_folder=False)
        if len(name.encode('utf-8'))>255:raise ValueError('TRANSFER_NAME_TOO_LONG')
        chain=TransferChain()
        kwargs=dict(fileitem=FileItem(storage='local',path=str(src),type='file',name=name,basename=Path(name).stem,extension=src.suffix.lstrip('.'),size=item['size']),meta=meta,mediainfo=self.media,target_directory=directory,target_storage='local',target_path=target,transfer_type='copy',scrape=False,library_type_folder=False,library_category_folder=False)
        checkpoint=chain.plan_transfer(**kwargs)
        expected=value(checkpoint,'final_target_path')
        if not expected:raise ValueError('TRANSFER_DESTINATION_UNCONFIRMED')
        if len(Path(expected).name.encode('utf-8'))>255:raise ValueError('TRANSFER_NAME_TOO_LONG')
        planned=safe_local(destination,expected,exists=False)
        if not rename and planned!=target/name:raise ValueError('EXACT_TRANSFER_NAME_REQUIRED')
        return dict(source=src,planned=planned,kwargs=kwargs,item=item,snapshot=snapshot,naming_revision='subtitle-tracks-v1' if item['role']=='subtitle' and not legacy else 'exact-plan-v1')

    def transfer(self,src,destination,item,snapshot):
        return self.transfer_prepared(self.prepare_transfer(src,destination,item,snapshot))

    def legacy_preflight_proof(self,plan_id,src,destination,item,snapshot):
        """Public reads plus the persisted pre-transfer boundary, never absence alone."""
        if self.repository is None:return None
        with self.repository.connection() as db:
            asset=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(plan_id,item['index'])).fetchone()
            if not asset or asset['destination'] is not None or asset['state']!='AUTHORIZED':return None
        from app.db.oper.transferhistory import TransferHistoryOper
        if TransferHistoryOper().get_by_src(str(src),storage='local') is not None:return None
        legacy=self.prepare_transfer(src,destination,item,snapshot,legacy=True)['planned']
        if not legacy.is_file():return None
        collision_hash=digest(safe_local(destination,legacy))
        with self.repository.connection() as db:
            collision=db.execute("SELECT size FROM organized_assets WHERE plan_id=? AND file_index!=? AND state='COMPLETE' AND destination=? AND sha256=?",(plan_id,item['index'],str(legacy),collision_hash)).fetchone()
        if not collision or legacy.stat().st_size!=collision['size']:return None
        return dict(code='LEGACY_PREFLIGHT_COLLISION_V1',target_root=str(destination),source_sha256=digest(src),collision_destination=str(legacy),collision_sha256=collision_hash,native_history_count=0)

    def transfer_prepared(self,prepared):
        from app.chain.transfer import TransferChain
        src,planned,item,snapshot=(prepared[k] for k in ('source','planned','item','snapshot'))
        if planned.exists():raise TransferNotSent('DESTINATION_ALREADY_EXISTS')
        if self.repository is not None:
            with self.repository.connection(write=True) as db:
                db.execute("UPDATE organized_assets SET destination=? WHERE source=? AND state='AUTHORIZED'",(str(planned),str(src)))
        result=TransferChain().transfer(**prepared['kwargs'])
        if result is None or not value(result,'success'):
            return None
        output=value(value(result,'target_item'),'path')
        if not output or Path(output)!=planned:
            raise ValueError('TRANSFER_TARGET_RECEIPT_MISSING')
        if not self.transfer_receipt(str(src),str(output),snapshot):
            raise ValueError('TRANSFER_HISTORY_NOT_CONFIRMED')
        if item['role']=='video':self.video_outputs[item['index']]=Path(output)
        return Path(output)
