"""Durable, isolated delivery. Provider receipts never constitute final ingest."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from hashlib import md5, sha1, sha256
import json
import os
from pathlib import Path, PurePosixPath
import stat
import threading
import time
import uuid
from contextlib import nullcontext
from functools import wraps

from .planner import BARRIERS, encoded, validate_files, asset_table
from .scheduler import instant, parse, stamp
from .execution import Exclusions, MUTATION_LOCK


LOCK = MUTATION_LOCK
PERMISSIONS = ('cleanup_success', 'cleanup_abandoned', 'cleanup_staging',
               'remove_downloader_task_enabled', 'delete_downloader_data_enabled')


class UploadNotSent(ValueError):
    """Local budget/checkpoint rejected CD2 before invoking StartRemoteUpload."""


def reader_error(error,stage):
    # Only our finite reason vocabulary is durable; never provider text/types.
    reason=str(error) if isinstance(error,(ValueError,TimeoutError)) else ''
    allowed={'TICK_DEADLINE','READER_BUDGET','STALE_OR_DISABLED_RUNTIME','ORIGINAL_SCOPE_SAFETY_ONLY',
        'SOURCE_CHANGED','READ_RANGE_INVALID','READ_REPLY_UNVERIFIED','HASH_TYPE_UNSUPPORTED',
        'HASH_BLOCK_LIMIT','PRECOMPUTED_HASH_REQUIRED','REMOTE_REQUEST_UNKNOWN','DEVICE_ID_CHANGED',
        'INVALID_READER_BUDGET'}
    return dict(stage=stage,reason=reason if reason in allowed else 'READER_FAILED')


def exclusive(function):
    @wraps(function)
    def run(self,identity,*args,**kwargs):
        # OS locks are released by process exit, unlike an expiring lease. They
        # serialize source readers/cleanup across plugin reloads and processes.
        directory=self.repository.path.parent/'delivery-locks';directory.mkdir(exist_ok=True)
        path=directory/(sha256(str(identity).encode()).hexdigest()+'.lock')
        with LOCK,path.open('a+b') as handle:
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            if os.name=='nt':
                import msvcrt
                try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                except OSError:raise ValueError('DELIVERY_WORKER_ACTIVE') from None
            else:
                import fcntl
                try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError:raise ValueError('DELIVERY_WORKER_ACTIVE') from None
            try:return function(self,identity,*args,**kwargs)
            finally:
                if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle,fcntl.LOCK_UN)
    return run


def identity(value):
    return [value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns if os.name != 'nt' else None]


def beneath(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def cloud_path(value):
    if not isinstance(value,str) or not value.startswith('/') or '\\' in value or '\x00' in value or any(p in ('.','..','') for p in value.split('/')[1:]):
        raise ValueError('INVALID_CLOUD_PATH')
    return str(PurePosixPath(value))


def validate_rules(rules):
    if not isinstance(rules,list) or len(rules)>100:raise ValueError('RULE_LIMIT')
    result={}
    for original in rules:
        r=json.loads(encoded(original)); name=r.get('id')
        if not isinstance(name,str) or not name or name in result: raise ValueError('INVALID_RULE_ID')
        if type(r.get('enabled')) is not bool: raise ValueError('INVALID_RULE_ENABLED')
        root=Path(r['local_root'])
        if not root.is_absolute() or '..' in root.parts or any(p.is_symlink() for p in (root,*root.parents)): raise ValueError('INVALID_LOCAL_ROOT')
        root=root.absolute()
        if os.name=='posix' and Path('/proc/self/mountinfo').exists():
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                before,after=line.split(' - ',1);mount=before.split()[4].replace('\\040',' ');filesystem=after.split()[0].casefold()
                if root.is_relative_to(Path(mount)) and any(word in filesystem for word in ('clouddrive','rclone','davfs')):raise ValueError('CLOUDFS_MONITOR_FORBIDDEN')
        r['local_root']=str(root)
        for key in ('staging_root','incoming_root'): r[key]=cloud_path(r[key])
        if beneath(r['staging_root'],r['incoming_root']) or beneath(r['incoming_root'],r['staging_root']): raise ValueError('OVERLAPPING_CLOUD_ROOTS')
        monitors=r.get('consumer_roots',[])
        if not monitors or not any(beneath(r['incoming_root'],cloud_path(p)) for p in monitors): raise ValueError('CONSUMER_SCOPE_REQUIRED')
        if any(beneath(r['staging_root'],cloud_path(p)) or beneath(cloud_path(p),r['staging_root']) for p in monitors): raise ValueError('STAGING_NOT_ISOLATED')
        for p in r.get('excluded_local_roots',[]):
            other=Path(p).absolute()
            if root.is_relative_to(other) or other.is_relative_to(root): raise ValueError('MONITOR_OUTPUT_OVERLAP')
        if not isinstance(r.get('cloud_scope_id'),str) or not r['cloud_scope_id']: raise ValueError('CLOUD_SCOPE_REQUIRED')
        for key,minimum in (('scan_interval',1),('stable_seconds',0),('rapid_interval',0),('rapid_misses',1)):
            value=r.get(key)
            if type(value) is not int or not minimum<=value<=86400*30: raise ValueError('INVALID_'+key.upper())
        for key in ('fallback','unlimited',*PERMISSIONS):
            r.setdefault(key,False)
            if type(r[key]) is not bool: raise ValueError('INVALID_PERMISSION')
        if r.get('fallback_gb') is not None:
            try:
                cap=Decimal(str(r['fallback_gb']))
                if not cap.is_finite() or cap<0: raise ValueError('INVALID_FALLBACK_LIMIT')
            except InvalidOperation: raise ValueError('INVALID_FALLBACK_LIMIT') from None
        r.setdefault('read_roots',[str(root)])
        if not r['read_roots'] or any(not Path(p).is_absolute() or '..' in Path(p).parts for p in r['read_roots']): raise ValueError('READ_ROOT_REQUIRED')
        if any(Path(x['local_root'])==root for x in result.values()): raise ValueError('DUPLICATE_LOCAL_OWNER')
        if r.get('watcher',False) is not False:raise ValueError('WATCHER_UNSUPPORTED_USE_PERIODIC_SCAN')
        for k in ('notify_success','notify_error'):
            r.setdefault(k,False)
            if type(r[k]) is not bool:raise ValueError('INVALID_NOTIFICATION_PERMISSION')
        r['transfer_revision']=sha256(encoded({k:v for k,v in r.items() if k not in (*PERMISSIONS,'notify_success','notify_error','enabled')}).encode()).hexdigest()
        r['revision']=sha256(encoded(r).encode()).hexdigest();result[name]=r
    return result


def fallback_allowed(rule,size):
    if rule.get('fallback') is not True: return False
    if rule.get('unlimited') is True:return True
    cap=rule.get('fallback_gb')
    return cap is not None and Decimal(size)<=Decimal(str(cap))*10**9


class LocalSource:
    """Pinned local input; no remote media reads. Cleanup has a separate nofollow gate."""
    def __init__(self,path,roots,expected=None):
        self.path=Path(path).absolute(); self.roots=[Path(r).resolve(strict=True) for r in roots]
        self.entry=identity(self.path.lstat()); self.target=self.path.resolve(strict=True)
        if not any(self.target.is_relative_to(r) for r in self.roots): raise ValueError('SOURCE_PATH_ESCAPE')
        self.stream=self.path.open('rb'); self.stop=threading.Event()
        try:
            observed=os.fstat(self.stream.fileno())
            if not stat.S_ISREG(observed.st_mode):raise ValueError('SOURCE_NOT_REGULAR')
            self.snapshot=dict(path=str(self.path),entry=self.entry,target=str(self.target),target_identity=identity(observed))
            self.size=observed.st_size;self.sha1=None;self.md5=None
            self.check()
            if expected is not None and self.snapshot!=expected:raise ValueError('SOURCE_CHANGED')
        except BaseException:self.stream.close();raise

    def check(self):
        if self.stop.is_set():raise ValueError('READER_CANCELLED')
        if (identity(self.path.lstat())!=self.entry or self.path.resolve(strict=True)!=self.target or
            identity(self.target.stat())!=self.snapshot['target_identity'] or identity(os.fstat(self.stream.fileno()))!=self.snapshot['target_identity']):
            raise ValueError('SOURCE_CHANGED')
        if os.name=='posix' and Path('/proc/self/fd').exists():
            if Path(os.readlink('/proc/self/fd/'+str(self.stream.fileno()))).resolve()!=self.target:raise ValueError('SOURCE_CHANGED')

    def hash(self):
        self.check(); a=sha1();b=sha256();c=md5();self.stream.seek(0)
        for block in iter(lambda:self.stream.read(1024*1024),b''):
            if self.stop.is_set():raise ValueError('READER_CANCELLED')
            a.update(block);b.update(block);c.update(block)
        self.check();self.sha1=a.hexdigest();self.md5=c.hexdigest()
        return self.sha1,b.hexdigest()

    def read(self,offset,length):
        if type(offset) is not int or type(length) is not int or offset<0 or length<0 or offset+length>self.size or length>1024*1024:raise ValueError('READ_RANGE_INVALID')
        self.check();self.stream.seek(offset);data=self.stream.read(length);self.check()
        if len(data)!=length:raise ValueError('SOURCE_SHORT_READ')
        return data

    def range_hash(self,value,*,deadline=None):
        import re
        match=re.fullmatch(r'(\d+)-(\d+)',value)
        if not match:raise ValueError('READ_RANGE_INVALID')
        start,end=map(int,match.groups())
        if not 0<=start<=end<self.size:raise ValueError('READ_RANGE_INVALID')
        h=sha1()
        for offset in range(start,end+1,1024*1024):
            if deadline is not None and time.monotonic()>deadline:raise TimeoutError('RANGE_HASH_DEADLINE')
            h.update(self.read(offset,min(1024*1024,end+1-offset)))
        return h.hexdigest().upper()

    def close(self):self.stop.set();self.stream.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class Delivery:
    def __init__(self,repository,authority,archive,cloud,*,rules,revalidate=None,publication_validator=None,dispatch_gate=None):
        if not callable(revalidate) and not callable(publication_validator):raise ValueError('CURRENT_CANDIDATE_REVALIDATOR_REQUIRED')
        self.repository,self.authority,self.archive,self.cloud=repository,authority,archive,cloud
        self.rules=validate_rules(rules);self.revalidate=revalidate;self.publication_validator=publication_validator;self.exclusions=Exclusions(repository)
        self.dispatch_gate=dispatch_gate

    def validate_publication(self,plan,publication):
        fresh=self.publication_validator(plan,publication) if self.publication_validator else self.revalidate(plan)
        if fresh!=publication:raise ValueError('PUBLICATION_CHANGED')
        return fresh

    def bundle(self,bundle_id):
        with self.repository.connection() as db:r=db.execute('SELECT * FROM delivery_bundles WHERE id=?',(bundle_id,)).fetchone()
        if not r:raise ValueError('BUNDLE_UNKNOWN')
        return dict(json.loads(r['data']),revision=r['revision'])

    def _save(self,b,db=None):
        if db is None:
            with self.repository.connection(write=True) as connection:return self._save(b,connection)
        data={k:v for k,v in b.items() if k!='revision'}
        result=db.execute('UPDATE delivery_bundles SET state=?,due=?,data=?,revision=revision+1 WHERE id=? AND revision=?',(b['state'],b['due'],encoded(data),b['id'],b['revision']))
        if result.rowcount!=1:raise ValueError('BUNDLE_CONCURRENT_CHANGE')
        b['revision']+=1

    def _rule(self,b,*,safety=False):
        r=self.rules.get(b['rule_id'])
        if r is None or (not r['enabled'] and not safety) or r['transfer_revision']!=b['rule_transfer_revision']:raise ValueError('RULE_CHANGED')
        return r

    def _valid(self,b,*,publication=False):
        if self.dispatch_gate:self.dispatch_gate()
        plan=self.authority.plan(b['plan_id']);s=plan['snapshot']
        with self.repository.connection() as db:
            self.authority._task_active(db,plan);self.authority._revisions(db,s)
            actual=self.authority._match(db,b['vector'],owner=b['plan_id'])
            if not publication and any(actual[k]['current_revision']!=b['vector'][k]['current_revision'] or actual[k]['current_revision']!=s['current'][k]['revision'] for k in actual):raise ValueError('CURRENT_REAUTHORIZE_REQUIRED')
        fresh=self.validate_publication(plan,b['manifest']['publication'])
        for key in b['vector']:
            if self.exclusions.matches(dict(s,targets=[key],content_sha1=[a['content']['sha1'] for a in b['manifest']['assets']]),facts=fresh[key]['raw']):raise ValueError('EXCLUDED')
        if publication:
            current=self.authority.vector(list(b['vector']))
            if any(current[k]['generation']!=b['vector'][k]['generation'] or current[k]['owner_plan_id']!=b['plan_id'] for k in current):raise ValueError('AUTHORITY_CHANGED')
            b['vector']=current
        return plan

    @exclusive
    def prepare(self,plan_id,rule_id,*,publication,indices=None,source_plan_id=None,now=None):
        with LOCK:
            r=self.rules[rule_id]
            if not r['enabled']:raise ValueError('RULE_DISABLED')
            plan=self.authority.plan(plan_id);s=plan['snapshot'];indices=sorted(indices if indices is not None else s['selected_indices'])
            source_plan_id=source_plan_id or plan_id;source_plan=self.authority.plan(source_plan_id)
            if source_plan_id!=plan_id:
                if source_plan['task_id']!=plan['task_id'] or source_plan['opportunity_id']!=plan['opportunity_id'] or source_plan['authorization'] not in ('CANCELLED','SUPERSEDED') or any(source_plan['snapshot'][k]!=s[k] for k in ('candidate_key','infohash','torrent_files','downloader','save_path')):raise ValueError('SOURCE_PLAN_LINEAGE_MISMATCH')
                with self.repository.connection() as db:
                    if db.execute("SELECT 1 FROM plan_actions WHERE plan_id=? AND kind IN ('RAPID','CD2_UPLOAD','PUBLISH','ORGANIZE') AND state IN ('IN_FLIGHT','UNKNOWN','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN','HANDED_OFF')",(source_plan_id,)).fetchone():raise ValueError('SOURCE_PLAN_UNSETTLED')
            table=validate_files(asset_table(s),indices)
            keys=sorted({k for i in indices for k in table[i]['targets']});vector=self.authority.vector(keys)
            bid=sha256(encoded([plan_id,source_plan_id,rule_id,r['transfer_revision'],indices,vector]).encode()).hexdigest()
            with self.repository.connection() as db:
                old=db.execute('SELECT id FROM delivery_bundles WHERE id=?',(bid,)).fetchone()
            if old:
                b=self.bundle(bid);self._valid(b)
                for f in b['files']:
                    with LocalSource(f['snapshot']['path'],r['read_roots'],f['snapshot']):pass
                return dict(state=b['state'],bundle_id=bid)
            if set(publication)!=set(keys):raise ValueError('PUBLICATION_SCOPE_REQUIRED')
            files=[];assets=[];relative=set()
            for index in indices:
                with self.repository.connection() as db:
                    row=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(source_plan_id,index)).fetchone()
                    aid=json.loads(row['evidence']).get('action_id') if row else None
                    proof=db.execute("SELECT a.payload,a.files FROM plan_actions a JOIN action_receipts r ON a.id=r.action_id WHERE a.id=? AND a.plan_id=? AND a.kind='ORGANIZE' AND a.state='SUCCEEDED' AND r.outcome='SUCCEEDED'",(aid,source_plan_id)).fetchone() if aid else None
                if not row or row['state']!='COMPLETE':raise ValueError('WAITING_ORGANIZED_ASSETS')
                if not proof or index not in json.loads(proof['files']) or json.loads(proof['payload']).get('file_index')!=index:raise ValueError('ORGANIZE_RECEIPT_REQUIRED')
                p=Path(row['destination']).absolute();root=Path(r['local_root'])
                owner=max((x for x in self.rules.values() if x['enabled'] and p.is_relative_to(Path(x['local_root']))),key=lambda x:len(Path(x['local_root']).parts),default=None)
                if owner is None or owner['id']!=rule_id:raise ValueError('LOCAL_RULE_OWNERSHIP')
                rel=p.relative_to(root).as_posix()
                if rel in relative or p.suffix.lower() in ('.strm','.zip','.rar','.7z'):raise ValueError('ASSET_PENDING_OR_COLLISION')
                relative.add(rel)
                with LocalSource(p,r['read_roots']) as source:
                    if source.size!=row['size'] or source.size!=table[index]['size']:raise ValueError('ORGANIZED_ASSET_CHANGED')
                    evidence=json.loads(row['evidence']);cached=evidence.get('prepare_hash')
                    reusable=(os.name=='posix' and isinstance(cached,dict) and cached.get('snapshot')==source.snapshot
                        and cached.get('sha256')==row['sha256'] and all(isinstance(cached.get(k),str)
                        and len(cached[k])==n and all(c in '0123456789abcdef' for c in cached[k])
                        for k,n in (('sha1',40),('sha256',64),('md5',32))))
                    if reusable:
                        source.check();digest,strong=cached['sha1'],cached['sha256'];source.sha1=digest;source.md5=cached['md5']
                    else:digest,strong=source.hash()
                    if strong!=row['sha256']:raise ValueError('ORGANIZED_ASSET_CHANGED')
                    source.check()
                    if os.name=='posix' and not reusable:
                        # Checkpoint the complete hash before the later receipt and dispatch gates; retries still pass _valid.
                        evidence['prepare_hash']=dict(snapshot=source.snapshot,sha1=digest,sha256=strong,md5=source.md5)
                        with self.repository.connection(write=True) as db:
                            updated=db.execute('UPDATE organized_assets SET evidence=? WHERE plan_id=? AND file_index=? AND state=? AND sha256=? AND evidence=?',
                                (encoded(evidence),source_plan_id,index,'COMPLETE',strong,row['evidence']))
                            if updated.rowcount!=1:raise ValueError('ORGANIZED_ASSET_CHANGED')
                    files.append(dict(file_index=index,relative_path=rel,snapshot=source.snapshot,parents=parent_snapshot(p),sha1=digest,md5=source.md5,size=source.size,misses=0,attempts=0,action_id=None,state='PENDING',due=stamp(instant(now)+timedelta(seconds=r['stable_seconds'])),reader_stopped=True))
                item=table[index]
                assets.append(dict(file_index=index,relative_path=item['path'],role=item['role'],targets=item['targets'],requires=item['requires'],content=dict(sha1=digest,size=item['size'])))
            manifest=dict(plan_id=plan_id,manifest_ref=bid,assets=assets,publication=publication)
            b=dict(id=bid,plan_id=plan_id,source_plan_id=source_plan_id,rule_id=rule_id,rule_revision=r['revision'],rule_transfer_revision=r['transfer_revision'],vector=vector,indices=indices,manifest=manifest,files=files,state='PREPARED',reason='',due=files[0]['due'],staging=r['staging_root']+'/'+bid,incoming=r['incoming_root']+'/'+bid,publication_action=None,revision=0)
            self._valid(b)
            with self.repository.connection(write=True) as db:db.execute('INSERT INTO delivery_bundles VALUES(?,?,?,?,?,0,?)',(bid,plan_id,rule_id,b['state'],b['due'],encoded({k:v for k,v in b.items() if k!='revision'})))
            return dict(state=b['state'],bundle_id=bid)

    def _source(self,f,r):
        source=LocalSource(f['snapshot']['path'],r['read_roots'],f['snapshot']);source.sha1=f['sha1'];source.md5=f['md5'];source.hash_blocks=f.setdefault('hash_blocks',{});return source

    def _remote(self,b,f):
        path=b['staging']+'/'+f['relative_path'];scope=self.rules[b['rule_id']]['cloud_scope_id']
        self.cloud.refresh(scope,str(PurePosixPath(path).parent));value=self.cloud.stat(scope,path)
        if value and (value.get('sha1'),value.get('size'))==(f['sha1'],f['size']):return value
        if value:raise ValueError('REMOTE_CONTENT_CONFLICT')
        return None

    def _receipt(self,b,f,outcome,evidence,now):
        with self.repository.connection(write=True) as db:
            self.authority.record_result(f['action_id'],outcome,evidence,now=now,db=db)
            self._save(b,db)

    def _verified(self,b,f,remote,now):
        f.update(state='VERIFIED',remote=remote,refresh_pending=True,reader_stopped=True)
        evidence={'asset_manifest':dict(b['manifest'],assets=[a for a in b['manifest']['assets'] if a['file_index']==f['file_index']]),'remote':remote}
        self._receipt(b,f,'SUCCEEDED',evidence,now)

    def _refresh(self,b,f,r):
        if f.get('refresh_pending'):
            try:self.cloud.refresh(r['cloud_scope_id'],str(PurePosixPath(b['staging']+'/'+f['relative_path']).parent))
            except Exception:b['reason']='REFRESH_PENDING';self._save(b);return False
            f['refresh_pending']=False;self._save(b)
        return True

    def _begin(self,b,f,kind,now):
        token=self.exclusions.token();self._valid(b);f['attempts']+=1
        aid=b['id']+':'+kind.lower()+':'+str(f['file_index'])+':'+str(f['attempts'])
        with self.repository.connection(write=True) as db:
            action=self.authority.begin_attempt(aid,b['plan_id'],b['vector'],kind,b['indices'],dict(bundle_id=b['id'],file_index=f['file_index'],path=b['staging']+'/'+f['relative_path']),now=now,exclusion_token=token,db=db)
            f.update(action_id=aid,state='UNKNOWN');b.update(state='UPLOADING',reason='');self._save(b,db)
        return action['dispatch']

    def _directories(self,b,f,r):
        if not b.get('directory'):
            if b.get('directory_intent') or self.cloud.stat(r['cloud_scope_id'],b['staging']) is not None:raise ValueError('STAGING_OWNERSHIP_UNKNOWN')
            b['directory_intent']=True;self._save(b)
            b['directory']=self.cloud.ensure_directory(r['cloud_scope_id'],b['staging']);self._save(b)
        if self.cloud.stat(r['cloud_scope_id'],b['staging'])!=b['directory']:raise ValueError('STAGING_IDENTITY_CHANGED')
        parent=PurePosixPath(b['staging']+'/'+f['relative_path']).parent
        parts=parent.relative_to(PurePosixPath(b['staging'])).parts
        path=b['staging']
        for part in parts:
            self._valid(b);path+='/'+part;self.cloud.ensure_directory(r['cloud_scope_id'],path)

    def _pump(self,b,f,r,now,*,cancel=False,budget=10,observe_only=False,deadline=None):
        terminal_states=('FINISH','CANCELLED','ERROR','FATALERROR','SKIPPED','IGNORED')
        stage='source'
        try:
            if deadline is not None and time.monotonic()>=deadline:raise ValueError('READER_BUDGET')
            status=f.get('progress',{})
            if status.get('state') not in terminal_states or status.get('reader_stopped') is not True:
                from types import SimpleNamespace
                source_context=nullcontext(SimpleNamespace(stop=threading.Event())) if cancel or observe_only else self._source(f,r)
                with source_context as source:
                    stage='dispatch'
                    if not cancel and not observe_only and self.dispatch_gate:self.dispatch_gate()
                    if deadline is not None:budget=min(budget,deadline-time.monotonic())
                    if budget<=0:raise ValueError('READER_BUDGET')
                    options=dict(cancel=cancel,budget=budget)
                    if observe_only:options['observe_only']=True
                    elif not cancel and status.get('state')=='PAUSE' and status.get('reader_stopped') is True:options['resume']=True
                    stage='pump'
                    status=self.cloud.pump(r['cloud_scope_id'],f['upload_id'],self.device_id(),source,**options)
            f['progress']=status
            f.pop('reader_error',None)
            if status.get('error'):f['reader_error']=status['error']
            terminal=status['state'] in terminal_states
            f['local_reader_stopped']=status.get('reader_stopped') is True
            f['reader_stopped']=terminal and f['local_reader_stopped']
            if status['state']=='FINISH':
                # Persist the original-ID terminal event before remote readback;
                # a failed lookup must not require the server to replay FINISH.
                self._save(b)
                try:remote=self._remote(b,f)
                except Exception:remote=None
                if remote and f['reader_stopped']:self._verified(b,f,remote,now);return
            elif terminal and f['reader_stopped']:
                f['state']='CANCELLED' if status['state']=='CANCELLED' else 'FAILED'
                b.update(state='BLOCKED',reason='CD2_'+status['state'])
                self._receipt(b,f,'FAILED',{'reason':b['reason'],'upload_id':f['upload_id'],'progress':status},now);return
            unknown=terminal or status['state']=='UNKNOWN'
            f['state']='UNKNOWN' if unknown else 'CD2_PAUSED' if status['state']=='PAUSE' else 'CD2_UPLOADING'
            b.update(state='UNKNOWN' if unknown else 'UPLOADING',reason='CD2_REMOTE_UNSETTLED' if unknown else 'CD2_PAUSED' if status['state']=='PAUSE' else 'CD2_PENDING')
            self._save(b)
        except Exception as error:
            progress=getattr(error,'reader_progress',None)
            if progress is not None:f['progress']=progress
            f['reader_error']=progress['error'] if progress is not None else reader_error(error,stage)
            f.update(state='UNKNOWN',reader_stopped=False,local_reader_stopped=True);b.update(state='UNKNOWN',reason='CD2_OUTCOME_UNKNOWN')
            self._receipt(b,f,'UNKNOWN',{'reason':b['reason'],'upload_id':f['upload_id'],'reader_error':f['reader_error']},now)
        finally:
            # Source reopen/dispatch may fail before pump adopts Start's stream.
            discard=getattr(self.cloud,'discard_pending',None)
            if discard:discard(r['cloud_scope_id'],f['upload_id'])

    def device_id(self):
        with self.repository.connection(write=True) as db:
            row=db.execute("SELECT value FROM settings WHERE key='delivery_device_id'").fetchone()
            if row:return json.loads(row[0])
            import uuid
            value='subscribetter-'+uuid.uuid4().hex
            db.execute('INSERT INTO settings VALUES(?,?)',('delivery_device_id',encoded(value)))
            return value

    def tick(self,*,now=None,limits=None):
        """Called by the existing host interval; no second scheduler/thread."""
        limits={'bundles':1,'scan_entries':1000,'seconds':5,**(limits or {})}
        if set(limits)!={'bundles','scan_entries','seconds'} or type(limits['bundles']) is not int or not 1<=limits['bundles']<=10:raise ValueError('INVALID_TICK_LIMITS')
        # One rule per tick, persisted rotation avoids starving later roots.
        rules=[r for r in sorted(self.rules) if self.rules[r]['enabled']]
        cursor=self.repository.setting('delivery_tick_cursor') or {'rule':'','bundle':''}
        scans=[]
        if rules:
            rule=next((r for r in rules if r>cursor['rule']),rules[0])
            scans.append(LocalReconciler(self.repository,[{k:v for k,v in r.items() if k not in ('revision','transfer_revision')} for r in self.rules.values()]).scan(rule,limits={'entries':limits['scan_entries'],'seconds':limits['seconds']},now=now))
            cursor['rule']=rule
        with self.repository.connection() as db:
            query="SELECT id FROM delivery_bundles WHERE state!='CONFIRMED' AND id>? ORDER BY id LIMIT ?"
            rows=db.execute(query,(cursor['bundle'],limits['bundles'])).fetchall()
            if not rows:rows=db.execute(query,('',limits['bundles'])).fetchall()
        results=[]
        for row in rows:
            bid=row['id'];cursor['bundle']=bid
            try:
                result=self.reconcile(bid,now=now,limits={'seconds':limits['seconds']})
                if result['state']=='REMOTE_VERIFIED':result=self.publish(bid,now=now)
                if result['state'] in ('WAIT_CONSUMER','ABANDONED'):
                    self.cleanup(bid,now=now)
                results.append(result)
            except Exception as error:
                reason=str(error) if isinstance(error,ValueError) and str(error).replace('_','').isalnum() else 'DELIVERY_CHECK_REQUIRED'
                results.append({'bundle_id':bid,'state':'BLOCKED','reason':reason})
        with self.repository.connection(write=True) as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('delivery_tick_cursor',encoded(cursor)))
        return dict(scans=[{k:s.get(k) for k in ('state','watermark','count','failed_paths')} for s in scans],bundles=results)

    def local_maintenance(self,*,entries,deadline):
        """One ordinary scan and one independently permitted cleanup scope."""
        if self.dispatch_gate:self.dispatch_gate()
        cursor=self.repository.setting('delivery-maintenance') or dict(rule='',bundle='',scope=0)
        rules=sorted(k for k,r in self.rules.items() if r['enabled']);scans=[];results=[]
        if rules and time.monotonic()<deadline:
            key=next((k for k in rules if k>cursor['rule']),rules[0])
            scans.append(LocalReconciler(self.repository,[{k:v for k,v in r.items() if k not in ('revision','transfer_revision')} for r in self.rules.values()]).scan(key,
                limits=dict(entries=entries,seconds=min(30,max(.001,deadline-time.monotonic())))))
            cursor['rule']=key
        scopes=('monitor','staging','downloader_task','downloader_data')
        with self.repository.connection() as db:
            rows=db.execute("SELECT id FROM delivery_bundles WHERE state IN ('WAIT_CONSUMER','CONFIRMED','ABANDONED') AND id>? ORDER BY id LIMIT 1",(cursor['bundle'],)).fetchall()
        if not rows:cursor.update(bundle='',scope=0)
        for row in rows:
            if time.monotonic()>=deadline:break
            if self.dispatch_gate:self.dispatch_gate()
            try:results.append(self.cleanup(row['id'],scope=scopes[cursor['scope']]))
            except ValueError as error:results.append(dict(state='DEFER',reason=str(error)))
            cursor['scope']=(cursor['scope']+1)%len(scopes)
            if not cursor['scope']:cursor['bundle']=row['id']
        self.repository.setting('delivery-maintenance',cursor)
        return dict(state='LOCAL_CHECKED',scans=scans,cleanup=results)

    @exclusive
    def reconcile(self,bundle_id,*,now=None,limits=None):
        deadline=time.monotonic()+(limits or {}).get('seconds',10)
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b)
            if not b.get('cancel_intent') and not b.get('publication_action') and self.authority.plan(b['plan_id'])['authorization']=='SUPERSEDED':
                b.update(cancel_intent={'reason':'SUPERSEDED','at':stamp(now)},state='CANCEL_PENDING');self._save(b)
            if b.get('cancel_intent'):return self._cancel_readers(b,r,now,deadline=deadline)
            if b.get('publication_action'):return self._publication_status(b,r,now)
            if parse(b['due'])>instant(now):return self._result(b)
            try:
                self._valid(b)
                for f in b['files']:
                    with self._source(f,r):pass
            except (ValueError,OSError):
                # Lost authority or unavailable local input blocks body/hash
                # serving, not bounded observation of an old exact upload ID.
                for f in b['files']:
                    if f.get('upload_id') and f['state']!='VERIFIED':
                        self._pump(b,f,r,now,observe_only=True,budget=(limits or {}).get('seconds',10),deadline=deadline);return self._result(b)
                raise
            for f in b['files']:
                if not self._refresh(b,f,r):return self._result(b)
                if f['state']=='VERIFIED':continue
                if parse(f['due'])>instant(now):continue
                if f.get('upload_id'):
                    self._pump(b,f,r,now,budget=(limits or {}).get('seconds',10),deadline=deadline);return self._result(b)
                if f['state']=='UNKNOWN':
                    remote=self._remote(b,f)
                    if remote:self._verified(b,f,remote,now)
                    else:b.update(state='UNKNOWN',reason='UPLOAD_OUTCOME_UNKNOWN');self._save(b)
                    return self._result(b)
                fallback=f['misses']>=r['rapid_misses']
                if fallback and not fallback_allowed(r,f['size']):
                    b.update(state='WAITING',reason='RAPID_EXHAUSTED' if not r['fallback'] else 'FALLBACK_LIMIT');self._save(b);return self._result(b)
                if not self._begin(b,f,'CD2_UPLOAD' if fallback else 'RAPID',now):return self._result(b)
                outcome=None;evidence=None;failure=None;stage='start';start_invoked=False
                try:
                    self._directories(b,f,r);self._valid(b)
                    with self._source(f,r) as source:
                        if fallback:
                            f['reader_stopped']=False;self._save(b)
                            start_invoked=True
                            upload_id=self.cloud.start(r['cloud_scope_id'],b['staging']+'/'+f['relative_path'],source,device_id=self.device_id(),budget=deadline-time.monotonic())
                            if not isinstance(upload_id,str) or not upload_id:raise TimeoutError()
                            stage='receipt';f.update(upload_id=upload_id,state='CD2_UPLOADING');self._receipt(b,f,'UNKNOWN',{'upload_id':upload_id,'state':'CD2_UPLOADING'},now)
                        else:result=self.cloud.rapid(r['cloud_scope_id'],b['staging']+'/'+f['relative_path'],source)
                    if fallback:
                        self._pump(b,f,r,now,budget=(limits or {}).get('seconds',10),deadline=deadline);return self._result(b)
                    if result['state']=='MISS':
                        f.update(state='PENDING',misses=f['misses']+1);outcome='FAILED';evidence={'reason':'EFFECTIVE_RAPID_MISS'}
                    elif result['state']=='HIT':
                        remote=self._remote(b,f)
                        if remote is None:raise TimeoutError()
                        self._verified(b,f,remote,now);self._refresh(b,f,r)
                    else:raise TimeoutError()
                except ValueError as error:
                    # Only an explicit pre-send/provider rejection is retryable. An
                    # arbitrary exception could follow a successful remote write.
                    failure=reader_error(error,stage);reason=str(error)
                    if fallback and not f.get('upload_id') and (isinstance(error,UploadNotSent) or not start_invoked and reason=='TICK_DEADLINE'):
                        f.update(state='PENDING',reader_stopped=True);b.update(state='WAITING',reason='UPLOAD_NOT_SENT_BUDGET');outcome='FAILED';evidence={'reason':b['reason']}
                    elif reason in ('AUTH_FAILED','RATE_LIMITED','PROVIDER_REJECTED','SOURCE_CHANGED','ACCOUNT_MISMATCH'):
                        f.update(state='PENDING');b['reason']=reason;outcome='FAILED';evidence={'reason':reason}
                    else:
                        b['reason']='UPLOAD_OUTCOME_UNKNOWN';outcome='UNKNOWN';evidence={'reason':b['reason']}
                except Exception as error:
                    failure=reader_error(error,stage)
                    b['reason']='UPLOAD_OUTCOME_UNKNOWN';outcome='UNKNOWN';evidence={'reason':b['reason']}
                finally:
                    if fallback and f.get('upload_id'):
                        discard=getattr(self.cloud,'discard_pending',None)
                        if discard:discard(r['cloud_scope_id'],f['upload_id'])
                if fallback and f.get('upload_id') and failure:
                    f.update(state='UNKNOWN',reader_stopped=False,local_reader_stopped=True,reader_error=failure)
                    b.update(state='UNKNOWN',reason='CD2_OUTCOME_UNKNOWN')
                    self._receipt(b,f,'UNKNOWN',{'reason':b['reason'],'upload_id':f['upload_id'],'reader_error':failure},now)
                    return self._result(b)
                f['due']=stamp(instant(now)+timedelta(seconds=max(1,r['rapid_interval'])))
                b['due']=f['due']
                if f['misses']>=r['rapid_misses'] and b['reason']!='UPLOAD_NOT_SENT_BUDGET':b.update(state='WAITING',reason='RAPID_EXHAUSTED')
                if outcome:self._receipt(b,f,outcome,evidence,now)
                else:self._save(b)
                return self._result(b)
            if all(f['state']=='VERIFIED' for f in b['files']):b.update(state='REMOTE_VERIFIED',reason='')
            self._save(b);return self._result(b)

    @staticmethod
    def _result(b):return dict(state=b['state'],reason=b['reason'],bundle_id=b['id'])

    def _publication_status(self,b,r,now):
        if b['state'] in ('CONFIRMED','WAIT_CONSUMER'):
            if b.get('publication_refresh_pending'):
                try:self.cloud.refresh(r['cloud_scope_id'],r['incoming_root'])
                except Exception:b['reason']='PUBLICATION_REFRESH_PENDING';self._save(b);return self._result(b)
                b['publication_refresh_pending']=False;self._save(b)
            return self._result(b)
        try:
            self.cloud.refresh(r['cloud_scope_id'],r['staging_root'])
            self.cloud.refresh(r['cloud_scope_id'],r['incoming_root'])
            moved=self.cloud.stat(r['cloud_scope_id'],b['incoming'])
            if not moved or moved.get('id')!=b['directory'].get('id'):raise ValueError('PUBLISH_LOCATION_UNKNOWN')
            self._bundle_contents(b,r,b['incoming'])
            for f in b['files']:
                remote=self.cloud.stat(r['cloud_scope_id'],b['incoming']+'/'+f['relative_path'])
                if not remote or (remote.get('id'),remote.get('sha1'),remote.get('size'))!=(f['remote'].get('id'),f['sha1'],f['size']):raise ValueError('PUBLISH_ASSETS_UNKNOWN')
            if self.cloud.stat(r['cloud_scope_id'],b['staging']) is not None:raise ValueError('PUBLISH_SOURCE_REMAINS')
            b.update(state='WAIT_CONSUMER',reason='CONSUMER_SETTLEMENT_REQUIRED',consumer_pending=True,publication_refresh_pending=True)
            with self.repository.connection(write=True) as db:
                self.authority.record_result(b['publication_action'],'HANDED_OFF',{'bundle_id':b['id'],'asset_manifest':b['manifest'],'entry':b['incoming']},now=now,db=db);self._save(b,db)
        except Exception:
            b.update(state='PUBLISH_OUTCOME_UNKNOWN',reason='PUBLISH_LOCATION_UNKNOWN')
            with self.repository.connection(write=True) as db:
                self.authority.record_result(b['publication_action'],'UNKNOWN',{'reason':b['reason']},now=now,db=db);self._save(b,db)
        return self._result(b)

    @exclusive
    def publish(self,bundle_id,*,now=None):
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b)
            if b.get('publication_action'):return self._publication_status(b,r,now)
            token=self.exclusions.token();plan=self._valid(b,publication=True)
            if any(f['state']!='VERIFIED' or f.get('refresh_pending') for f in b['files']):raise ValueError('ASSETS_NOT_READY')
            for f in b['files']:
                with self._source(f,r):pass
                if self._remote(b,f)!=f['remote']:raise ValueError('REMOTE_IDENTITY_CHANGED')
            self._bundle_contents(b,r,b['staging'])
            if self.cloud.stat(r['cloud_scope_id'],b['incoming']) is not None:
                b.update(reason='DESTINATION_CONFLICT');self._save(b);return self._result(b)
            self.authority.set_transfer_phase(b['plan_id'],b['vector'],'READY_TO_PUBLISH')
            checks={name:True for name in ('identity','admission','scope','not_excluded','current_allows','assets_complete','remote_verified')}
            validation=dict(policy_revision=plan['snapshot']['policy_revision'],parse_revision=plan['snapshot']['parse_revision'],current_revisions={k:v['current_revision'] for k,v in b['vector'].items()},checks={k:checks for k in b['vector']})
            aid=b['id']+':publish'
            with self.repository.connection(write=True) as db:
                action=self.authority.begin_publish(aid,b['plan_id'],b['vector'],b['indices'],validation=validation,now=now,exclusion_token=token,db=db)
                b.update(publication_action=aid,state='PUBLISHING');self._save(b,db)
            if not action['dispatch']:return self._publication_status(b,r,now)
            try:
                if self.dispatch_gate:self.dispatch_gate()
                self.cloud.move(r['cloud_scope_id'],b['staging'],b['incoming'])
            except Exception:
                b.update(state='PUBLISH_OUTCOME_UNKNOWN',reason='MOVE_RESPONSE_UNKNOWN')
                with self.repository.connection(write=True) as db:
                    self.authority.record_result(aid,'UNKNOWN',{'reason':b['reason']},now=now,db=db);self._save(b,db)
                return self._result(b)
            return self._publication_status(b,r,now)

    def _bundle_contents(self,b,r,root):
        expected={root+'/'+f['relative_path'] for f in b['files']}
        for f in b['files']:
            parent=PurePosixPath(root+'/'+f['relative_path']).parent
            while str(parent)!=root:
                expected.add(str(parent));parent=parent.parent
        rows=self.cloud.inventory(r['cloud_scope_id'],root)
        if len(rows)!=len(expected) or {x['path'] for x in rows}!=expected:raise ValueError('BUNDLE_CONTENTS_CHANGED')

    @exclusive
    def confirm(self,bundle_id,consumer_receipt,*,now=None):
        """Caller supplies an actual settled consumer receipt; Archive verifies it."""
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b,safety=True)
            if not b.get('publication_action'):raise ValueError('PUBLICATION_REQUIRED')
            if consumer_receipt.get('settled') is not True:
                return dict(state='WAIT_CONSUMER',reason='CONSUMER_SETTLEMENT_REQUIRED',bundle_id=bundle_id)
            if consumer_receipt.get('action_id')!=b['publication_action'] or not consumer_receipt.get('evidence_ref'):
                raise ValueError('EXACT_CONSUMER_RECEIPT_REQUIRED')
            if b['state'] not in ('WAIT_CONSUMER','CONFIRMED','PUBLISHING','PUBLISH_OUTCOME_UNKNOWN'):raise ValueError('HANDOFF_UNVERIFIED')
            unknown=b['state'] in ('PUBLISHING','PUBLISH_OUTCOME_UNKNOWN')
            if unknown:b.update(state='PUBLISH_OUTCOME_UNKNOWN')
            with self.repository.connection(write=True) as db:
                self.authority.record_result(b['publication_action'],'UNKNOWN' if unknown else 'HANDED_OFF',{'asset_manifest':b['manifest'],'consumer_receipt':consumer_receipt},now=now,db=db)
                b['consumer_receipt']=consumer_receipt;self._save(b,db)
            result=self.archive.confirm_ingest(b['publication_action'],b['manifest'],consumer_receipt,now=now)
            if result.get('accepted') is True or (result.get('duplicate') is True and result.get('reason')=='ALREADY_CONFIRMED'):b.update(state='CONFIRMED',reason='',consumer_pending=False)
            else:b.update(reason='FINAL_ASSOCIATION_UNVERIFIED')
            self._save(b);return dict(self._result(b),confirmation=result)

    @exclusive
    def safety_reconcile(self,bundle_id,*,now=None,limits=None):
        """Only already-issued IDs/receipts; no new upload, body, move or cleanup."""
        deadline=time.monotonic()+(limits or {}).get('seconds',10)
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b,safety=True)
            if b.get('cancel_intent'):return self._cancel_readers(b,r,now,deadline=deadline)
            if b.get('publication_action'):return self._publication_status(b,r,now)
            for f in b['files']:
                if f.get('upload_id') and f.get('state')!='VERIFIED':
                    self._pump(b,f,r,now,observe_only=True,budget=(limits or {}).get('seconds',10),deadline=deadline);return self._result(b)
                if f.get('action_id') and f.get('state')=='UNKNOWN':
                    remote=self._remote(b,f)
                    if remote:self._verified(b,f,remote,now)
                    return self._result(b)
            return self._result(b)

    @exclusive
    def cancel(self,bundle_id,*,reason,exclusion_id,criteria,now=None):
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b,safety=True)
            if not b.get('cancel_intent'):
                with self.repository.connection(write=True) as db:
                    self.exclusions.add(exclusion_id,criteria,reason=reason,db=db)
                    # Superseded bundles no longer own targets; never cancel the successor.
                    actual=self.authority.vector(list(b['vector']))
                    owned={k:v for k,v in actual.items() if v['owner_plan_id']==b['plan_id']}
                    if owned:self.authority.cancel(b['plan_id'],owned,reason=reason,db=db)
                    b.update(cancel_intent={'reason':reason,'exclusion_id':exclusion_id,'at':stamp(now)},state='CANCEL_PENDING')
                    self._save(b,db)
            return self._cancel_readers(b,r,now)

    def _cancel_readers(self,b,r,now,*,deadline=None):
        if deadline is None:deadline=time.monotonic()+10
        for f in b['files']:
            if f.get('upload_id') and not (f.get('reader_stopped') and f['state'] in ('VERIFIED','CANCELLED','FAILED')):
                self._pump(b,f,r,now,cancel=True,deadline=deadline)
            elif f['state']=='UNKNOWN' and f.get('action_id'):
                action=self.authority.action(f['action_id'])
                expected=dict(bundle_id=b['id'],file_index=f['file_index'],path=b['staging']+'/'+f['relative_path'])
                if action and action['kind']=='RAPID' and action['plan_id']==b['plan_id'] and json.loads(action['payload'])==expected:
                    try:
                        if not b.get('directory') or self.cloud.stat(r['cloud_scope_id'],b['staging'])!=b['directory']:continue
                        remote=self._remote(b,f)
                    except Exception:continue  # Absence/error never proves a lost send failed.
                    if remote:self._verified(b,f,remote,now)
        if b.get('publication_action'):
            b.update(state='PUBLISH_OUTCOME_UNKNOWN',reason='PUBLISHED_BARRIER_REQUIRES_SETTLEMENT')
        elif any(not f.get('reader_stopped') or f['state']=='UNKNOWN' for f in b['files']):
            b.update(state='CANCEL_PENDING',reason='READER_OR_REMOTE_UNSETTLED')
        else:b.update(state='ABANDONED',reason=b['cancel_intent']['reason'])
        self._save(b);return self._result(b)

    def _shared(self,b,f):
        """Any other live bundle/organizer reference vetoes physical cleanup."""
        path=f['snapshot']['path']
        with self.repository.connection() as db:
            for row in db.execute('SELECT id,data FROM delivery_bundles WHERE id!=?',(b['id'],)):
                other=json.loads(row['data'])
                if any(x['snapshot']['path']==path and not x.get('cleaned') for x in other['files']):return True
            for row in db.execute('SELECT plan_id FROM organized_assets WHERE destination=? AND plan_id!=?',(path,b['plan_id'])):
                plan=db.execute('SELECT authorization FROM plans WHERE id=?',(row[0],)).fetchone()
                if plan and plan[0]=='ACTIVE':return True
        return False

    @exclusive
    def cleanup(self,bundle_id,*,scope='monitor',now=None):
        with LOCK:
            b=self.bundle(bundle_id);r=self._rule(b)
            successful=b['state'] in ('WAIT_CONSUMER','CONFIRMED') and all(f['state']=='VERIFIED' for f in b['files']) and b.get('consumer_pending') is not None
            abandoned=b['state']=='ABANDONED' and bool(b.get('cancel_intent'))
            if not successful and not abandoned:return dict(self._result(b),reason='CLEANUP_NOT_ELIGIBLE')
            if any(not f.get('reader_stopped') or f['state']=='UNKNOWN' for f in b['files']):return dict(self._result(b),reason='READER_OR_REMOTE_UNSETTLED')
            if scope not in ('monitor','staging','downloader_task','downloader_data'):raise ValueError('CLEANUP_SCOPE_REQUIRED')
            permission={'monitor':'cleanup_success' if successful else 'cleanup_abandoned','staging':'cleanup_staging','downloader_task':'remove_downloader_task_enabled','downloader_data':'delete_downloader_data_enabled'}[scope]
            if not r[permission]:return dict(self._result(b),reason='CLEANUP_PERMISSION_DISABLED')
            if scope=='staging':return self._cleanup_staging(b,r,now)
            if scope in ('downloader_task','downloader_data'):return self._cleanup_downloader(b,r,scope,now)
            for f in b['files']:
                if f.get('cleaned'):continue
                if self._shared(b,f):return dict(self._result(b),reason='SHARED_LOCAL_REFERENCE')
                f.setdefault('cleanup_intent',{'permission':permission,'rule_revision':r['revision'],'at':stamp(now),'quarantine':'.subscribetter-clean-'+uuid.uuid4().hex});self._save(b)
                try:safe_unlink(f['snapshot'],f['parents'],resume=True,quarantine=f['cleanup_intent']['quarantine'])
                except (OSError,ValueError):return dict(self._result(b),reason='BLOCKED_CLEANUP_UNSAFE')
                f['cleaned']=True;self._save(b)
            return dict(self._result(b),monitor_cleaned=True)

    def _cleanup_staging(self,b,r,now):
        if b.get('publication_action'):return dict(self._result(b),reason='PUBLISHED_CLEANUP_FORBIDDEN')
        if not b.get('cancel_intent'):return dict(self._result(b),reason='EXPLICIT_ABANDON_REQUIRED')
        for f in b['files']:
            if f.get('staging_cleaned'):continue
            path=b['staging']+'/'+f['relative_path'];remote=self.cloud.stat(r['cloud_scope_id'],path)
            if remote is not None and remote!=f.get('remote'):return dict(self._result(b),reason='STAGING_IDENTITY_UNKNOWN')
            f['staging_cleanup_intent']={'path':path,'object':remote,'rule_revision':r['revision'],'at':stamp(now)};self._save(b)
            if remote:
                try:self.cloud.delete_file(r['cloud_scope_id'],path,remote)
                except Exception:return dict(self._result(b),reason='STAGING_DELETE_UNKNOWN')
            f['staging_cleaned']=True;self._save(b)
        # Empty transport parents are retained; no recursive directory deletion.
        return dict(self._result(b),staging_cleaned=True)

    def _cleanup_downloader(self,b,r,scope,now):
        from .execution import ConfiguredDownloader
        s=self.authority.plan(b['plan_id'])['snapshot']
        if set(b['indices'])!={f['index'] for f in s['torrent_files']}:return dict(self._result(b),reason='SHARED_DOWNLOAD_SCOPE')
        with self.repository.connection() as db:
            managed=db.execute('SELECT * FROM managed_downloads WHERE downloader=? AND infohash=?',(s['downloader'],s['infohash'])).fetchone()
            others=db.execute("SELECT id,snapshot FROM plans WHERE id!=? AND authorization IN ('ACTIVE','PREPARED')",(b['plan_id'],)).fetchall()
            bundles=db.execute('SELECT data FROM delivery_bundles WHERE id!=?',(b['id'],)).fetchall()
        if any(json.loads(p['snapshot'])['downloader']==s['downloader'] and json.loads(p['snapshot'])['infohash']==s['infohash'] for p in others):return dict(self._result(b),reason='SHARED_DOWNLOAD_REFERENCE')
        for row in bundles:
            other=json.loads(row[0]);plan=self.authority.plan(other['plan_id'])['snapshot']
            if plan['downloader']==s['downloader'] and plan['infohash']==s['infohash'] and other['state'] not in ('ABANDONED','CONFIRMED'):return dict(self._result(b),reason='SHARED_DOWNLOAD_REFERENCE')
        if not managed or managed['save_path']!=s['save_path']:return dict(self._result(b),reason='MANAGED_DOWNLOAD_REQUIRED')
        def guard():
            with self.repository.connection(write=True) as db:
                refs=self.authority.download_references(s['downloader'],s['infohash'],s['save_path'],db=db)
                if any(x['plan_id']!=b['plan_id'] or set(x['indices'])!=set(b['indices']) for x in refs):raise ValueError('SHARED_DOWNLOAD_REFERENCE')
                row=db.execute('SELECT state,evidence FROM managed_downloads WHERE downloader=? AND infohash=?',(s['downloader'],s['infohash'])).fetchone();evidence=json.loads(row['evidence'])
                if row['state']=='DELIVERY_CLEANUP' and evidence.get('cleanup_bundle')!=b['id']:raise ValueError('DOWNLOAD_CLEANUP_UNSETTLED')
                evidence.update(cleanup_bundle=b['id'],cleanup_scope=scope)
                db.execute("UPDATE managed_downloads SET state='DELIVERY_CLEANUP',evidence=? WHERE downloader=? AND infohash=?",(encoded(evidence),s['downloader'],s['infohash']))
        def settled():
            with self.repository.connection(write=True) as db:db.execute("UPDATE managed_downloads SET state='REMOVED' WHERE downloader=? AND infohash=? AND state='DELIVERY_CLEANUP'",(s['downloader'],s['infohash']))
        if self.dispatch_gate:self.dispatch_gate()
        client=ConfiguredDownloader.named(s['downloader'])
        if self.dispatch_gate:self.dispatch_gate()
        task=client.task(s['infohash'])
        if task and (task['id']!=managed['client_id'] or task['save_path']!=s['save_path'] or managed['marker'] not in task['markers']):return dict(self._result(b),reason='DOWNLOAD_OWNERSHIP_CHANGED')
        if scope=='downloader_task':
            if task:
                if b.get('downloader_remove_intent'):return dict(self._result(b),reason='DOWNLOADER_REMOVE_UNKNOWN')
                if self.dispatch_gate:self.dispatch_gate()
                actual=client.files(s['infohash'])
                if [(x['id'],x['path'],x['size']) for x in sorted(actual,key=lambda x:x['id'])]!=[(x['index'],x['path'],x['size']) for x in s['torrent_files']]:return dict(self._result(b),reason='DOWNLOAD_FILE_TABLE_CHANGED')
                if self.dispatch_gate:self.dispatch_gate()
                guard()
                b['downloader_remove_intent']={'client_id':task['id'],'delete_data':False,'rule_revision':r['revision'],'at':stamp(now)};self._save(b)
                try:
                    if self.dispatch_gate:self.dispatch_gate()
                except ValueError:
                    b.pop('downloader_remove_intent');self._save(b)
                    raise
                try:
                    client.remove(task['id'])
                    if self.dispatch_gate:self.dispatch_gate()
                    if client.task(s['infohash']) is not None:return dict(self._result(b),reason='DOWNLOADER_REMOVE_UNKNOWN')
                except Exception:return dict(self._result(b),reason='DOWNLOADER_REMOVE_UNKNOWN')
            settled();b['downloader_removed']=True;self._save(b);return dict(self._result(b),downloader_removed=True)
        # Source data permission never implies task removal. Even a paused task
        # can be resumed by another actor; absence is required before unlinking.
        if task:return dict(self._result(b),reason='DOWNLOADER_SOURCE_READER_UNSETTLED')
        if self.dispatch_gate:self.dispatch_gate()
        if os.name!='posix':return dict(self._result(b),reason='BLOCKED_CLEANUP_UNSAFE')
        guard()
        for f in b['files']:
            if f.get('source_cleaned'):continue
            if self._shared(b,f):return dict(self._result(b),reason='SHARED_LOCAL_REFERENCE')
            with self.repository.connection() as db:row=db.execute('SELECT * FROM organized_assets WHERE plan_id=? AND file_index=?',(b['source_plan_id'],f['file_index'])).fetchone()
            if not row:return dict(self._result(b),reason='SOURCE_RECEIPT_REQUIRED')
            with self.repository.connection() as db:
                if db.execute("SELECT 1 FROM organized_assets a JOIN plans p ON p.id=a.plan_id WHERE a.source=? AND a.plan_id NOT IN (?,?) AND p.authorization='ACTIVE'",(row['source'],b['plan_id'],b['source_plan_id'])).fetchone():return dict(self._result(b),reason='SHARED_LOCAL_REFERENCE')
            if not f.get('source_cleanup_intent'):
                try:
                    with LocalSource(row['source'],[s['save_path']]) as source:
                        digest,strong=source.hash()
                        if digest!=f['sha1'] or strong!=row['sha256']:raise ValueError('SOURCE_CHANGED')
                        f['source_cleanup_intent']={'snapshot':source.snapshot,'parents':parent_snapshot(row['source']),'rule_revision':r['revision'],'at':stamp(now),'quarantine':'.subscribetter-clean-'+uuid.uuid4().hex}
                except (ValueError,OSError):return dict(self._result(b),reason='SOURCE_CLEANUP_UNVERIFIED')
                self._save(b)
            intent=f['source_cleanup_intent']
            if self.dispatch_gate:self.dispatch_gate()
            try:safe_unlink(intent['snapshot'],intent['parents'],resume=True,quarantine=intent['quarantine'])
            except (ValueError,OSError):return dict(self._result(b),reason='BLOCKED_CLEANUP_UNSAFE')
            f['source_cleaned']=True;self._save(b)
        settled();return dict(self._result(b),source_cleaned=True)


def parent_snapshot(path):
    return {str(p):identity(p.lstat())[:3] for p in reversed(Path(path).absolute().parents)}


def safe_unlink(snapshot,parents,*,resume=False,quarantine=None):
    """Delete exactly one recorded entry, rooted through verified nofollow dirfds."""
    if os.name!='posix' or os.open not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd:
        raise ValueError('NO_SAFE_UNLINK_PRIMITIVES')
    # Rename-noreplace captures the actual entry atomically. Revalidate that
    # captured inode before unlinking; a racing replacement is never deleted.
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True)
    rename=getattr(libc,'renameat2',None)
    if rename is None:raise ValueError('NO_SAFE_RENAME_PRIMITIVE')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
    quarantine=quarantine or '.subscribetter-clean-'+uuid.uuid4().hex
    if not quarantine.startswith('.subscribetter-clean-') or '/' in quarantine or '\\' in quarantine:raise ValueError('INVALID_CLEANUP_ENTRY')
    path=Path(snapshot['path']);fd=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        current=Path(path.anchor)
        if identity(os.fstat(fd))[:3]!=parents[str(current)]:raise ValueError('PARENT_CHANGED')
        for part in path.parts[1:-1]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=new;current/=part
            if identity(os.fstat(fd))[:3]!=parents[str(current)]:raise ValueError('PARENT_CHANGED')
        try:captured=os.stat(quarantine,dir_fd=fd,follow_symlinks=False)
        except FileNotFoundError:captured=None
        if captured is None:
            try:observed=os.stat(path.name,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError:
                if resume:return
                raise
            if identity(observed)!=snapshot['entry']:raise ValueError('ENTRY_CHANGED')
            if not (stat.S_ISREG(observed.st_mode) or stat.S_ISLNK(observed.st_mode)):raise ValueError('ENTRY_NOT_FILE')
            if rename(fd,os.fsencode(path.name),fd,os.fsencode(quarantine),1)!=0:raise OSError(ctypes.get_errno(),'SAFE_RENAME_FAILED')
            captured=os.stat(quarantine,dir_fd=fd,follow_symlinks=False)
            if identity(captured)[:5]!=snapshot['entry'][:5]:
                # Never overwrite a concurrently recreated original name.
                rename(fd,os.fsencode(quarantine),fd,os.fsencode(path.name),1)
                raise ValueError('ENTRY_CHANGED')
        elif not resume or identity(captured)[:5]!=snapshot['entry'][:5]:raise ValueError('CLEANUP_ENTRY_CONFLICT')
        os.unlink(quarantine,dir_fd=fd)
    finally:os.close(fd)


class PublicationGate:
    """Same W06 classification/policy/current sources, before cloud side effects."""
    def __init__(self,archive,publication,*,locked=None):
        self.archive=archive;self.publication=json.loads(encoded(publication));self.locked=locked

    def __call__(self,plan):
        from .planner import active_snapshot,quality_locks_for_scope
        s=active_snapshot(plan,self.publication);keys=list(s['targets']);policy=self.archive.policy
        locked=quality_locks_for_scope(self.locked,keys)
        if s['policy_revision']!=policy.semantic_hash:raise ValueError('POLICY_CHANGED')
        if set(self.publication)!=set(keys):raise ValueError('PUBLICATION_SCOPE_REQUIRED')
        current=self.archive.current(keys)
        for key in keys:
            baseline=current[key];source=self.publication[key]
            if baseline['state'] not in ('MISSING','INVALID','PRESENT') or (baseline['state']=='PRESENT' and not baseline['versions']):raise ValueError('CURRENT_UNVERIFIED')
            if self.archive.sources.classify_target(key)!=source['classification']:raise ValueError('CLASSIFICATION_CHANGED')
            facts=policy.normalize(source['raw'])
            decision=policy.compare(facts,baseline['versions'],source['classification'],identity_ok=s['verified']['identity'],scope_ok=s['verified']['scope'],locked=locked)
            supplement=s['targets'][key]['action']=='SIDECAR_SUPPLEMENT'
            if supplement:
                before={v['version_id'] for v in s['current'][key].get('versions',[]) if v['active']}
                after={v.version_id for v in baseline['versions'] if v.active}
                if decision.reason!='EQUIVALENT' or not before or before!=after:raise ValueError('SIDECAR_CURRENT_CHANGED')
            elif decision.status!='ALLOW' or decision.action!='TRANSFER':raise ValueError(decision.reason)
            if list(decision.rank)!=s['targets'][key]['quality']:raise ValueError('FROZEN_QUALITY_MISMATCH')
        return self.publication


def local_source_identity(root):
    """Stable source anchor; a remount of the same source can recover naturally."""
    value=dict(root=identity(root.lstat())[:3],mount=None)
    if not root.is_dir() or root.is_symlink():raise ValueError('ROOT_UNAVAILABLE')
    if os.name=='posix':
        import re
        resolved=root.resolve();matches=[]
        # Linux mount IDs change on remount. Bind the filesystem, source and
        # mount root instead, together with the actual directory inode/device.
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left,right=line.split(' - ',1);fields=left.split();fs=right.split()
            decode=lambda x:re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),x)
            mount=Path(decode(fields[4]))
            if resolved.is_relative_to(mount):
                matches.append((len(mount.parts),[fields[2],decode(fields[3]),str(mount),fs[0],decode(fs[1])]))
        if not matches:raise ValueError('MOUNT_SOURCE_UNAVAILABLE')
        value['mount']=max(matches,key=lambda x:x[0])[1]
    return value


class LocalReconciler:
    """Durable full-range scans; notifications merely shorten the next due time."""
    def __init__(self,repository,rules):self.repository=repository;self.rules=validate_rules(rules)

    def hint(self,rule_id):
        with self.repository.connection(write=True) as db:
            row=db.execute('SELECT data FROM reconcile_checkpoints WHERE scope=?',('local:'+rule_id,)).fetchone()
            if row:
                data=json.loads(row[0]);data['hint']=True
                db.execute('UPDATE reconcile_checkpoints SET data=? WHERE scope=?',(encoded(data),'local:'+rule_id))

    def scan(self,rule_id,*,limits=None,force=False,now=None):
        limits={'entries':1000,'seconds':5,**(limits or {})}
        if set(limits)!={'entries','seconds'} or type(limits['entries']) is not int or not 1<=limits['entries']<=10000 or type(limits['seconds']) not in (float,int) or not 0<limits['seconds']<=30:raise ValueError('INVALID_SCAN_LIMITS')
        r=self.rules[rule_id];root=Path(r['local_root']);scope='local:'+rule_id
        if not r['enabled']:return {'state':'DISABLED'}
        with LOCK,self.repository.connection(write=True) as db:
            row=db.execute('SELECT data FROM reconcile_checkpoints WHERE scope=?',(scope,)).fetchone()
            data=json.loads(row[0]) if row else {}
            if data.get('state')=='COMPLETE' and not force and not data.get('hint') and parse(data['due'])>instant(now):return data
            try:
                source=local_source_identity(root);root_id=source['root']
            except (OSError,ValueError):
                data.update(state='INCOMPLETE',failed_paths=[str(root)])
                db.execute('INSERT OR REPLACE INTO reconcile_checkpoints VALUES(?,?)',(scope,encoded(data)));return data
            anchor=data.get('source_identity')
            # Existing schema-7 checkpoints already carry a root anchor. Do not
            # silently adopt a different root while upgrading its mount proof.
            legacy_root=data.get('root_identity')
            if ((anchor is not None and anchor!=source) or
                    (anchor is None and legacy_root is not None and legacy_root!=root_id)):
                data.update(state='SOURCE_UNVERIFIED',reason='ROOT_SOURCE_CHANGED',observed_source=source)
                db.execute('INSERT OR REPLACE INTO reconcile_checkpoints VALUES(?,?)',(scope,encoded(data)));return data
            anchor=anchor or source
            if data.get('state') in ('COMPLETE','SOURCE_UNVERIFIED') or data.get('revision')!=r['revision'] or data.get('failed_paths'):
                data={}
            if not data:data=dict(state='INCOMPLETE',epoch=uuid.uuid4().hex,revision=r['revision'],root_identity=root_id,source_identity=anchor,stack=[{'path':str(root),'offset':0}],directories=[],failed_paths=[],count=0,verify_offset=0)
            data['source_identity']=anchor
            deadline=time.monotonic()+limits['seconds'];used=0
            while data['stack'] and used<limits['entries'] and time.monotonic()<deadline:
                frame=data['stack'][-1];directory=Path(frame['path'])
                try:
                    before=identity(directory.lstat())
                    if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):raise ValueError('SCAN_PARENT_CHANGED')
                    if frame.get('identity',before)!=before:raise ValueError('SCAN_DIRECTORY_CHANGED')
                    frame['identity']=before;finished=True
                    with os.scandir(directory) as entries:
                        for index,entry in enumerate(entries):
                            if time.monotonic()>=deadline:finished=False;break
                            if index<frame['offset']:continue
                            if used>=limits['entries']:finished=False;break
                            frame['offset']=index+1;used+=1;data['count']+=1
                            path=Path(entry.path);observed=identity(entry.stat(follow_symlinks=False))
                            owner=max((x for x in self.rules.values() if x['enabled'] and path.is_relative_to(Path(x['local_root']))),key=lambda x:len(Path(x['local_root']).parts))
                            if owner['id']!=rule_id:continue
                            if stat.S_ISDIR(observed[2]):
                                data['stack'].append({'path':str(path),'offset':0});finished=False;break
                            if not stat.S_ISREG(observed[2]):continue
                            previous=db.execute('SELECT data FROM local_observations WHERE rule_id=? AND path=?',(rule_id,str(path))).fetchone()
                            old=json.loads(previous[0]) if previous else {}
                            value=dict(state='PRESENT',identity=observed,stable_since=old.get('stable_since') if old.get('identity')==observed else stamp(now))
                            db.execute('INSERT OR REPLACE INTO local_observations VALUES(?,?,?,?)',(rule_id,str(path),data['epoch'],encoded(value)))
                    if identity(directory.lstat())!=before:raise ValueError('SCAN_DIRECTORY_CHANGED')
                    if finished:data['directories'].append([str(directory),before]);data['stack'].pop()
                except (OSError,ValueError):data['failed_paths'].append(str(directory));break
            while not data['stack'] and not data['failed_paths'] and data['verify_offset']<len(data['directories']) and used<limits['entries'] and time.monotonic()<deadline:
                path,expected=data['directories'][data['verify_offset']];used+=1
                try:
                    if identity(Path(path).lstat())!=expected:raise ValueError('SCAN_DIRECTORY_CHANGED')
                except (OSError,ValueError):data['failed_paths'].append(path);break
                data['verify_offset']+=1
            if len(data['stack'])+len(data['directories'])>50000:data['failed_paths'].append('SCAN_DIRECTORY_LIMIT')
            if not data['stack'] and not data['failed_paths'] and data['verify_offset']==len(data['directories']):
                try:
                    if local_source_identity(root)!=anchor:raise ValueError('ROOT_SOURCE_CHANGED')
                except (OSError,ValueError):
                    data.update(state='SOURCE_UNVERIFIED',reason='ROOT_SOURCE_CHANGED')
                    db.execute('INSERT OR REPLACE INTO reconcile_checkpoints VALUES(?,?)',(scope,encoded(data)));return data
                # Missing is written only after an entire unchanged accessible range.
                db.execute("UPDATE local_observations SET data=json_set(data,'$.state','MISSING') WHERE rule_id=? AND epoch!=?",(rule_id,data['epoch']))
                data.update(state='COMPLETE',watermark=stamp(now),due=stamp(instant(now)+timedelta(seconds=r['scan_interval'])),hint=False)
            db.execute('INSERT OR REPLACE INTO reconcile_checkpoints VALUES(?,?)',(scope,encoded(data)))
            return data
