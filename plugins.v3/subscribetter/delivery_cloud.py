"""Owned public clients only. No host storage upload or OSS fallback exists here."""
from hashlib import md5, sha1, sha256
from pathlib import PurePosixPath
import re
import time

from .delivery import cloud_path, beneath
from .planner import encoded


CHUNK=1024*1024
STATES={0:'WAITFORPREPROCESSING',1:'PREPROCESSING',2:'CANCELLED',3:'TRANSFER',4:'PAUSE',5:'FINISH',6:'SKIPPED',7:'INQUEUE',8:'IGNORED',9:'ERROR',10:'FATALERROR'}
TERMINAL={'CANCELLED','FINISH','SKIPPED','IGNORED','ERROR','FATALERROR'}


class HostDeliveryCloud:
    def __init__(self,sources,*,timeout=15):
        if type(timeout) not in (int,float) or not 0<timeout<=30:raise ValueError('INVALID_TIMEOUT')
        self.sources=sources;self.timeout=timeout;self.p115={}

    def _scope(self,scope,path=None):
        config=self.sources.scopes.get(scope)
        if not config:raise ValueError('CLOUD_SCOPE_UNKNOWN')
        if path is not None:
            path=cloud_path(path)
            if not any(beneath(path,cloud_path(p)) for p in config['allowed_prefixes']):raise ValueError('CLOUD_PATH_UNAUTHORIZED')
        return config

    def _raw(self,scope):return self.sources._client(scope,self.timeout)

    def stat(self,scope,path):
        self._scope(scope,path);client,pb,meta=self._raw(scope)
        try:raw=client.stub.FindFileByPath(pb.FindFileByPathRequest(parentPath='',path=path),metadata=meta,timeout=self.timeout)
        except Exception as error:
            # Only NOT_FOUND proves absence. Authentication/timeouts are unknown.
            code=getattr(error,'code',lambda:None)()
            if getattr(code,'name',None)=='NOT_FOUND':return None
            raise ValueError('CLOUD_STAT_UNKNOWN') from None
        if not raw.fullPathName and not raw.id:return None
        if raw.fullPathName!=path or not raw.id:raise ValueError('CLOUD_PATH_CONFLICT')
        account=sha256(encoded([scope,self.sources.accounts[scope]]).encode()).hexdigest()
        if raw.isDirectory:return dict(path=path,id=str(raw.id),directory=True,account_ref=account)
        digest=raw.fileHashes.get(2,'').lower()
        if not re.fullmatch('[a-f0-9]{40}',digest):
            # Populate only from an actual account/path-verified 115 directory.
            parent=str(PurePosixPath(path).parent)
            self.sources.scopes[scope].setdefault('p115_parents',{})[parent]=self._parent(scope,parent)
            value=self.sources.cloud_stat(scope,path,timeout=self.timeout);digest=value['sha1']
        return dict(path=path,id=str(raw.id),directory=False,sha1=digest,size=int(raw.size),account_ref=account)

    def ensure_directory(self,scope,path):
        self._scope(scope,path);old=self.stat(scope,path)
        if old:
            if not old.get('directory'):raise ValueError('DIRECTORY_CONFLICT')
            return old
        client,pb,meta=self._raw(scope)
        response=client.stub.CreateFolder(pb.CreateFolderRequest(parentPath=str(PurePosixPath(path).parent),folderName=PurePosixPath(path).name),metadata=meta,timeout=self.timeout)
        if not response.result.success or not response.folderCreated.isDirectory or response.folderCreated.fullPathName!=path or not response.folderCreated.id:raise ValueError('DIRECTORY_CREATE_UNKNOWN')
        value=self.stat(scope,path)
        if not value or value['id']!=str(response.folderCreated.id):raise ValueError('DIRECTORY_CREATE_UNKNOWN')
        return value

    def _p115(self,scope):
        config=self._scope(scope);self._raw(scope)
        if scope not in self.p115:
            from p115client import P115Client
            public=self.sources.plugin.get_config(config.get('p115_plugin','P115Disk')) or {}
            self.p115[scope]=P115Client(public['cookie'])
            if str(self.p115[scope].user_id)!=self.sources.accounts[scope]:raise ValueError('ACCOUNT_MISMATCH')
        return self.p115[scope]

    def _parent(self,scope,parent):
        config=self._scope(scope,parent);client=self._p115(scope)
        relative='/' + str(PurePosixPath(parent).relative_to(PurePosixPath(config['root'])))
        response=client.fs_dir_getid(relative,timeout=self.timeout)
        cid=str(response.get('id',''))
        if response.get('state') is not True or not cid.isdecimal() or cid=='0':raise ValueError('P115_PARENT_UNKNOWN')
        page=client.fs_files({'cid':cid,'offset':0,'limit':1,'cur':1,'record_open_time':0},timeout=self.timeout)
        chain=page.get('path') or []
        actual=str(page.get('cid') if page.get('cid') is not None else chain[-1].get('cid') if chain else '')
        names=[str(x.get('name',x.get('n',''))) for x in chain if str(x.get('cid'))!='0']
        if page.get('state') is not True or actual!=cid or '/'+ '/'.join(names)!=relative:raise ValueError('P115_PARENT_UNKNOWN')
        return cid

    def rapid(self,scope,path,source):
        self._scope(scope,path);client=self._p115(scope);cid=self._parent(scope,str(PurePosixPath(path).parent))
        deadline=time.monotonic()+self.timeout*3
        def ranges(value):
            if time.monotonic()>deadline:raise TimeoutError('RAPID_DEADLINE')
            return source.range_hash(value,deadline=deadline)
        source.check()
        try:result=client.upload_file_init(filename=PurePosixPath(path).name,filesha1=source.sha1.upper(),filesize=source.size,pid=cid,read_range_bytes_or_hash=ranges,timeout=self.timeout)
        except Exception as error:
            status=getattr(getattr(error,'response',None),'status_code',None)
            if status in (401,403):raise ValueError('AUTH_FAILED') from None
            if status==429:raise ValueError('RATE_LIMITED') from None
            raise
        source.check()
        if result.get('state') is True and result.get('status')==1 and result.get('reuse') is False:return {'state':'MISS'}
        if result.get('state') is True and result.get('status')==2 and result.get('reuse') is True:return {'state':'HIT'}
        # Do not persist a provider body: callback/bucket/object can be credentials.
        return {'state':'UNKNOWN'}

    def start(self,scope,path,source):
        self._scope(scope,path);client,pb,meta=self._raw(scope);source.check()
        response=client.stub.StartRemoteUpload(pb.StartRemoteUploadRequest(file_path=path,file_size=source.size,known_hashes={2:source.sha1},client_can_calculate_hashes=True),metadata=meta,timeout=self.timeout)
        if not response.upload_id:raise ValueError('UPLOAD_START_UNKNOWN')
        return response.upload_id

    def pump(self,scope,upload_id,device_id,source,*,cancel=False,budget=10):
        """One synchronous reader, <=1MiB resident bytes, finite RPC/session work.

        No detached tasks exist. Returning closes the stream and proves only this
        local reader stopped; a terminal server receipt is an independent fact.
        """
        if type(budget) not in (int,float) or not 0<budget<=30:raise ValueError('INVALID_READER_BUDGET')
        client,pb,meta=self._raw(scope);deadline=time.monotonic()+budget
        state='UNKNOWN';sent=0;requests=0;call=None
        def remaining():
            value=min(self.timeout,deadline-time.monotonic())
            if value<=0:raise TimeoutError('READER_BUDGET')
            return value
        try:
            if cancel:
                source.stop.set()
                client.stub.RemoteUploadControl(pb.RemoteUploadControlRequest(upload_id=upload_id,cancel=pb.CancelRemoteUpload()),metadata=meta,timeout=remaining())
            call=client.stub.RemoteUploadChannel(pb.RemoteUploadChannelRequest(device_id=device_id),metadata=meta,timeout=remaining())
            for message in call:
                remaining();requests+=1
                if requests>1024:break
                if message.upload_id!=upload_id:continue
                kind=message.WhichOneof('request')
                if kind=='status_changed':
                    state=STATES.get(message.status_changed.status,'UNKNOWN')
                    if state in TERMINAL:break
                elif cancel:continue
                elif kind=='read_data':
                    req=message.read_data
                    if req.length<=0 or req.offset<0 or req.offset+req.length>source.size or req.length>16*CHUNK:raise ValueError('READ_RANGE_INVALID')
                    for offset in range(req.offset,req.offset+req.length,CHUNK):
                        remaining();size=min(CHUNK,req.offset+req.length-offset)
                        if sent+size>64*CHUNK:raise TimeoutError('READER_BYTE_BUDGET')
                        data=source.read(offset,size);last=offset+size==source.size
                        answer=client.stub.RemoteReadData(pb.RemoteReadDataUpload(upload_id=upload_id,offset=offset,length=size,lazy_read=req.lazy_read,data=data,is_last_chunk=last),metadata=meta,timeout=remaining())
                        if not answer.success or answer.bytes_received!=size or answer.is_last_chunk!=last:raise ValueError('READ_REPLY_UNVERIFIED')
                        sent+=size
                elif kind=='hash_data':
                    req=message.hash_data
                    if req.hash_type not in (1,2):raise ValueError('HASH_TYPE_UNSUPPORTED')
                    block_size=req.block_size if req.HasField('block_size') else 0
                    if block_size and (req.hash_type!=1 or block_size<CHUNK or (source.size+block_size-1)//block_size>4096):raise ValueError('HASH_BLOCK_LIMIT')
                    cached=source.md5 if req.hash_type==1 else source.sha1
                    if not block_size and cached:
                        source.check()
                        client.stub.RemoteHashProgress(pb.RemoteHashProgressUpload(upload_id=upload_id,bytes_hashed=source.size,total_bytes=source.size,hash_type=req.hash_type,hash_value=cached),metadata=meta,timeout=remaining())
                        continue
                    whole=md5() if req.hash_type==1 else sha1();blocks=[];block=md5();block_bytes=0
                    for offset in range(0,source.size,CHUNK):
                        remaining();data=source.read(offset,min(CHUNK,source.size-offset));whole.update(data)
                        if block_size:
                            position=0
                            while position<len(data):
                                end=min(len(data),position+block_size-block_bytes);block.update(data[position:end]);block_bytes+=end-position;position=end
                                if block_bytes==block_size:blocks.append(block.hexdigest());block=md5();block_bytes=0
                    if block_bytes:blocks.append(block.hexdigest())
                    client.stub.RemoteHashProgress(pb.RemoteHashProgressUpload(upload_id=upload_id,bytes_hashed=source.size,total_bytes=source.size,hash_type=req.hash_type,hash_value=whole.hexdigest(),block_hashes=blocks),metadata=meta,timeout=remaining())
                else:raise ValueError('REMOTE_REQUEST_UNKNOWN')
        except Exception as error:
            if isinstance(error,ValueError):raise
            # Stream deadline/disconnect is never equivalent to Finish/Cancelled.
            state='UNKNOWN'
        finally:
            if call is not None:call.cancel()
        return dict(state=state,reader_stopped=True,bytes_sent=sent,requests=requests)

    def refresh(self,scope,path):
        self._scope(scope,path);client,pb,meta=self._raw(scope);count=0
        call=client.stub.GetSubFiles(pb.ListSubFileRequest(path=path,forceRefresh=True),metadata=meta,timeout=self.timeout)
        try:
            for reply in call:
                count+=len(reply.subFiles)
                if count>10000:raise ValueError('REFRESH_LIMIT')
        finally:call.cancel()

    def inventory(self,scope,path):
        """Only the exact owned batch subtree, never the library/cloudfs root."""
        self._scope(scope,path);client,pb,meta=self._raw(scope)
        pending=[path];rows=[];seen=set();deadline=time.monotonic()+30
        while pending:
            current=pending.pop()
            remaining=min(self.timeout,deadline-time.monotonic())
            if remaining<=0:raise ValueError('BUNDLE_LIST_TIMEOUT')
            call=client.stub.GetSubFiles(pb.ListSubFileRequest(path=current,forceRefresh=True),metadata=meta,timeout=remaining)
            try:
                for reply in call:
                    for raw in reply.subFiles:
                        name=cloud_path(raw.fullPathName)
                        if str(PurePosixPath(name).parent)!=current or name in seen or not raw.id:raise ValueError('BUNDLE_LIST_AMBIGUOUS')
                        seen.add(name);rows.append({'path':name,'id':str(raw.id),'directory':raw.isDirectory})
                        if len(rows)>10000:raise ValueError('BUNDLE_LIST_LIMIT')
                        if raw.isDirectory:pending.append(name)
            finally:call.cancel()
        return rows

    def move(self,scope,source,destination):
        self._scope(scope,source);self._scope(scope,destination)
        if PurePosixPath(source).name!=PurePosixPath(destination).name:raise ValueError('BUNDLE_RENAME_FORBIDDEN')
        client,pb,meta=self._raw(scope)
        response=client.stub.MoveFile(pb.MoveFileRequest(theFilePaths=[source],destPath=str(PurePosixPath(destination).parent),conflictPolicy=2,moveAcrossClouds=False,handleConflictRecursively=False),metadata=meta,timeout=self.timeout)
        return {'success':response.success}  # Never used as location proof.

    def delete_file(self,scope,path,expected):
        if self.stat(scope,path)!=expected:raise ValueError('REMOTE_IDENTITY_CHANGED')
        if expected.get('directory'):raise ValueError('RECURSIVE_DELETE_FORBIDDEN')
        client,pb,meta=self._raw(scope)
        client.stub.DeleteFile(pb.FileRequest(path=path),metadata=meta,timeout=self.timeout)
        if self.stat(scope,path) is not None:raise ValueError('DELETE_OUTCOME_UNKNOWN')

    def close(self):
        for client in self.p115.values():
            if hasattr(client,'close'):client.close()
        self.p115.clear()
