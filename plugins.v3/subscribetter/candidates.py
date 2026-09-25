"""Bounded raw site discovery and conservative complete physical torrent tables."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from itertools import zip_longest
import json
import math
from pathlib import Path, PurePosixPath
import re
from threading import Lock
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

from .meta import BRACKET, _stored
from .planner import (TEXT_SUBTITLE_SUFFIXES, VIDEO_SUFFIXES, TargetUnit, encoded,
                      validate_files, validate_new_asset_scope)
from .repository import Target, utcnow

VIDEO = VIDEO_SUFFIXES
SUBTITLE = TEXT_SUBTITLE_SUFFIXES


def _sample_video(path):
    value = PurePosixPath(path)
    return (any(part.casefold() == 'sample' for part in value.parts[:-1])
            or re.search(r'(?i)(?:^|[._ -])sample(?:[._ -]|$)', value.stem) is not None)


class HostCandidateAdapter:
    @staticmethod
    def site_description(raw, selected_sites, *, deadline):
        """One bounded, same-origin detail request; credentials and HTML stay in memory."""
        codes = {'SITE_DETAIL_TIMEOUT', 'SITE_DETAIL_UNAUTHORIZED', 'SITE_DETAIL_UNSAFE',
                 'SITE_DETAIL_HTTP', 'SITE_DETAIL_TOO_LARGE', 'SITE_DETAIL_FAILED'}
        try:
            from app.sdk.network import RequestUtils, SecurityUtils
            from app.sdk.config import settings
            def remaining():
                now = time.monotonic()
                if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= now:
                    raise ValueError('SITE_DETAIL_TIMEOUT')
                return min(15, deadline - now)
            remaining()
            site_id = value(raw, 'site')
            if (not isinstance(selected_sites, (list, tuple)) or len(selected_sites) > 32
                    or any(type(i) is not int or i < 1 for i in selected_sites)
                    or type(site_id) is not int or site_id not in selected_sites):
                raise ValueError('SITE_DETAIL_UNAUTHORIZED')
            matches = [s for s in HostCandidateAdapter.sites() if s.get('id') == site_id]
            if len(matches) != 1 or matches[0].get('is_active') is False:
                raise ValueError('SITE_DETAIL_UNAUTHORIZED')
            site = matches[0]
            url, base = value(raw, 'page_url'), site.get('url')
            def origin(address):
                if not isinstance(address, str) or any(c.isspace() for c in address):
                    raise ValueError('SITE_DETAIL_UNSAFE')
                parts = urlsplit(address)
                if parts.scheme != 'https' or not parts.hostname or parts.username is not None or parts.password is not None:
                    raise ValueError('SITE_DETAIL_UNSAFE')
                return parts.hostname.casefold(), parts.port or 443
            if origin(url) != origin(base) or not SecurityUtils.is_safe_url(
                    url, allowed_domains=[urlsplit(base).netloc], strict=True, block_private=True):
                raise ValueError('SITE_DETAIL_UNSAFE')
            request = RequestUtils(cookies=site.get('cookie'), ua=site.get('ua'),
                proxies=settings.PROXY if site.get('proxy') else None, timeout=remaining(), verify=True)
            with request.get_stream(url, allow_redirects=False, raise_exception=True) as response:
                if response is None or response.status_code != 200:
                    raise ValueError('SITE_DETAIL_HTTP')
                if origin(response.url) != origin(base):
                    raise ValueError('SITE_DETAIL_UNSAFE')
                limit = 2 * 1024 * 1024
                length = response.headers.get('Content-Length')
                if length is not None and int(length) > limit:
                    raise ValueError('SITE_DETAIL_TOO_LARGE')
                body = bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    remaining()
                    if len(body) + len(chunk) > limit:
                        raise ValueError('SITE_DETAIL_TOO_LARGE')
                    body.extend(chunk)
                remaining()
                return body.decode(response.encoding or 'utf-8')
        except Exception as error:
            code = str(error) if isinstance(error, ValueError) and str(error) in codes else 'SITE_DETAIL_FAILED'
            raise ValueError(code) from None

    @staticmethod
    def sites():
        from app.sdk.network import SitesHelper
        return SitesHelper().get_indexers()

    @staticmethod
    def rss(site,timeout):
        from app.sdk.network import RssHelper
        url=site.get('rss')
        if not isinstance(url,str) or urlsplit(url).scheme not in ('http','https'):
            raise ValueError('SITE_RSS_UNCONFIGURED')
        rows=RssHelper().parse(url,bool(site.get('proxy')),timeout=timeout,ua=site.get('ua') or None)
        if rows is None:raise ValueError('SITE_RSS_EXPIRED')
        if rows is False:raise ValueError('SITE_RSS_FAILED')
        if not isinstance(rows,list) or len(rows)>1000:raise ValueError('SITE_RSS_INVALID')
        # Raw URLs remain only in CandidateService.runtime. Missing RSS fields
        # stay missing; authenticated exact search supplies richer TorrentInfo.
        return [dict(row,site=site['id'],page_url=row.get('link'),
            pubdate=row['pubdate'].isoformat() if hasattr(row.get('pubdate'),'isoformat') else row.get('pubdate')) for row in rows]

    @staticmethod
    def search(site,word,page):
        from app.chain.search import SearchChain
        return SearchChain().search_site_torrents(site=site,keyword=word,mtype=None,page=page)

    @staticmethod
    def page_size(site,word):
        from app.chain.search import SearchChain
        return SearchChain().get_search_page_size(site=site,keyword=word)

    @staticmethod
    def recognize(meta,declared,*,media_type=None):
        from app.chain.media import MediaChain
        from app.sdk.media import normalize_media_source
        from app.schemas.types import MediaType
        # Public module dispatch avoids NameRecognize / MediaRecognize auxiliary
        # events and shared-recognition submission used by the high-level method.
        return MediaChain().run_module('recognize_media',meta=meta,
            mtype=MediaType(media_type) if media_type else None,
            media_source=normalize_media_source(declared[0]) if declared else None,
            media_id=str(declared[1]) if declared and declared[1] is not None else None,episode_group=None)

    @staticmethod
    def dump_media(media):
        from fastapi.encoders import jsonable_encoder
        data = media.to_dict()
        # The REST serializer removes raw provider facts needed for seasons and rating.
        for field in ('tmdb_info', 'douban_info'):
            data[field] = getattr(media, field, None)
        return jsonable_encoder(data)

    @staticmethod
    def load_media(data):
        from app.sdk.media import MediaInfo
        media = MediaInfo()
        media.from_dict(data)
        return media

    @staticmethod
    def identity(media):
        from app.sdk.media import resolve_media_identity
        source,mid=resolve_media_identity(media=media)
        return (source.value,str(mid)) if source and mid is not None else None

    @staticmethod
    def source_identity(media, source):
        """Return only provider IDs already carried by the recognized media payload."""
        fields = {"douban": ("douban_id", "douban_info"),
                  "themoviedb": ("tmdb_id", "tmdb_info")}.get(str(source).casefold())
        if fields is None:
            return {"state": "UNKNOWN", "media_id": None}
        values = []
        direct = value(media, fields[0])
        details = value(media, fields[1])
        if direct not in (None, ""):
            values.append(str(direct))
        if isinstance(details, dict) and details.get("id") not in (None, ""):
            values.append(str(details["id"]))
        values = sorted(set(values))
        if len(values) == 1:
            return {"state": "VERIFIED", "media_id": values[0]}
        return {"state": "CONFLICT" if values else "UNKNOWN", "media_id": None}

    @staticmethod
    def acquire(raw):
        from app.chain.download import DownloadChain
        content,_,_=DownloadChain().download_torrent(raw)
        return content

    @staticmethod
    def classify(media):
        from .mp_adapter import NativeAdapter
        return NativeAdapter.classify(media)


def value(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def identity_matches(target, candidate):
    if not candidate or not all(candidate):
        return None
    return tuple(map(str, target)) == tuple(map(str, candidate))


def recognized_identity_matches(target, identity, mapping=None):
    """Match canonical identity or separately persisted, typed provider evidence."""
    if not isinstance(identity,(tuple,list)) or len(identity)!=2 or not all(identity):return None
    matched=identity_matches((target.media_source,target.media_id),identity)
    if matched or identity[0]==target.media_source:return matched
    if not isinstance(mapping,dict) or mapping.get('state')=='UNKNOWN':return None
    if (mapping.get('state')!='VERIFIED' or mapping.get('source')!=target.media_source
            or mapping.get('media_id')!=target.media_id):return False
    if not mapping.get('media_type'):return None
    return mapping['media_type']==target.media_type


def candidate_key(raw):
    site = value(raw, 'site')
    if type(site) is not int or site < 1:
        raise ValueError('configured site ID required')
    tid = value(raw, 'torrent_id')
    url = value(raw, 'page_url')
    if not tid and url:
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        tid = next((query[k][0] for k in ('id', 'tid', 'torrentid', 'torrent_id') if k in query), None)
    if tid is not None and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', str(tid)):
        return f'site:{site}:{tid}'
    if url:
        parts = urlsplit(url)
        if parts.scheme in ('http', 'https') and parts.hostname and parts.path.strip('/'):
            # Persist only a digest: even a detail path can contain a passkey.
            reference=parts.hostname.casefold()+parts.path+'?'+urlencode(sorted((k,v) for k,v in parse_qsl(parts.query) if k.casefold() not in {'passkey','token','auth','authorization','apikey','api_key','cookie','signature','sig','expires'}))
            return f'site:{site}:detail:' + sha256(reference.encode()).hexdigest()
    raise ValueError('resource identity missing; title and size are not identity')


def sample_key(kind, candidate, task_id, index=None):
    scoped = candidate if task_id is None else candidate + ':task:' + str(task_id)
    if index is not None:scoped += ':' + str(index)
    return kind + ':' + sha256(scoped.encode()).hexdigest()


@dataclass(frozen=True)
class SearchBudget:
    keywords: int
    pages: int
    concurrency: int
    results: int
    requests: int
    interval: float

    def __post_init__(self):
        for name, ceiling in (('keywords',8), ('pages',5), ('concurrency',4), ('results',1000), ('requests',64)):
            if type(getattr(self,name)) is not int or not 1 <= getattr(self,name) <= ceiling:
                raise ValueError('invalid search budget')
        if type(self.interval) not in (int,float) or not 0 <= self.interval <= 10:
            raise ValueError('invalid request interval')


class CandidateService:
    def __init__(self, repository, adapter, ai=None):
        self.repository, self.adapter, self.ai = repository, adapter, ai
        self.runtime = {}  # Host credential-bearing objects never enter SQLite.
        self.search_errors=[]
        self.deadline=None
        self.checkpoint=lambda:None

    def observe(self, raw, *, source='search'):
        key = candidate_key(raw)
        fields = ('title','description','labels','size','seeders','pubdate','downloadvolumefactor','media_source','media_id')
        data = {k: value(raw,k) for k in fields}
        data['media_source'] = getattr(data['media_source'],'value',data['media_source'])
        data['missing_fields'] = [k for k in ('description','labels') if value(raw,k) is None]
        if source == 'rss' and value(raw,'description') is None:
            data['missing_fields'] = sorted(set(data['missing_fields']+['description']))
        data.update(candidate_key=key,site=value(raw,'site'),source=source,status='DEFER' if data['missing_fields'] else 'OBSERVED')
        data = _stored(data)
        now = utcnow()
        with self.repository.connection(write=True) as db:
            db.execute('INSERT INTO candidates VALUES(?,?,?,?) ON CONFLICT(candidate_key) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at', (key,encoded(data),now,now))
        self.runtime[key] = raw
        while len(self.runtime)>1000:self.runtime.pop(next(iter(self.runtime)))
        return data

    def records(self, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('bounded candidate page required')
        with self.repository.connection() as db:
            return [dict(json.loads(r['data']),first_seen=r['first_seen']) for r in db.execute('SELECT * FROM candidates ORDER BY first_seen,candidate_key LIMIT ? OFFSET ?', (limit,offset))]

    def search(self, selected_sites, keywords, budget,*,deadline=None):
        deadline=deadline if deadline is not None else self.deadline
        def expired():return deadline is not None and time.monotonic()>=deadline
        self.search_errors=[]
        if expired():raise ValueError('TICK_DEADLINE')
        if not isinstance(selected_sites,(list,tuple)) or len(selected_sites)>32 or any(type(i) is not int or i<1 for i in selected_sites):
            raise ValueError('explicit bounded site IDs required')
        if not selected_sites:
            return []
        if not isinstance(keywords,(list,tuple)) or any(not isinstance(w,str) or not 1<=len(w)<=256 for w in keywords):
            raise ValueError('trusted bounded keywords required')
        words = list(dict.fromkeys(keywords))[:budget.keywords]
        # ponytail: expose one candidate per trusted name at minimum; raise results for noisy sites.
        explore_limit = max(budget.results, len(words))
        # Reserve raw-result slots for later trusted aliases before querying a noisy first term.
        base, extra = divmod(budget.results, len(words)) if words else (0, 0)
        allocated = {word: base + (index >= len(words) - extra) for index, word in enumerate(words)}
        word_limits = {word: max(1, allocated[word]) for word in words}
        query_words = (sorted(words, key=lambda word: allocated[word] == 0)
                       if budget.requests >= 2 * len(words) else words)
        word_counts = {word: 0 for word in words}
        self.checkpoint()
        sites = [s for s in self.adapter.sites() if s.get('id') in set(selected_sites)]
        lock, output, seen = Lock(), [], set()
        buckets = {word: [] for word in words}
        remaining = [budget.requests]
        def run(site):
            for word in query_words:
                with lock:
                    if expired() or remaining[0]<2 or len(output)>=explore_limit:return
                    if word_counts[word]>=word_limits[word]:continue
                    remaining[0]-=1
                self.checkpoint()
                try:
                    size = self.adapter.page_size(site,word)
                except Exception:
                    size = None
                for page in range(budget.pages if type(size) is int and size>0 else 1):
                    with lock:
                        if expired() or remaining[0] <= 0 or len(output)>=explore_limit:return
                        if word_counts[word]>=word_limits[word]:break
                        remaining[0] -= 1
                    self.checkpoint()
                    try:
                        rows = self.adapter.search(site,word,page)
                    except Exception:
                        with lock:self.search_errors.append('SITE_SEARCH_FAILED:'+str(site['id']))
                        rows = []
                    for raw in (rows or [])[:budget.results]:
                        if value(raw,'site') != site['id']:
                            continue
                        try:
                            with lock:
                                if len(output)>=explore_limit:return
                                if word_counts[word]>=word_limits[word]:break
                                row = self.observe(raw)
                                if row['candidate_key'] not in seen:
                                    output.append(row); buckets[word].append(row); seen.add(row['candidate_key']); word_counts[word]+=1
                        except (ValueError,TypeError):
                            continue
                    if budget.interval:
                        time.sleep(min(budget.interval,max(0,deadline-time.monotonic())) if deadline else budget.interval)
                    if type(size) is not int or len(rows or [])<size:
                        break
        with ThreadPoolExecutor(max_workers=budget.concurrency) as pool:
            list(pool.map(run,sites))
        ordered = [row for group in zip_longest(*(buckets[word] for word in words)) for row in group if row is not None]
        return ordered[:explore_limit]

    def refresh(self,key,budget):
        """Cold recovery uses only saved site/resource identity, never old cookies."""
        with self.repository.connection() as db:
            row=db.execute('SELECT data FROM candidates WHERE candidate_key=?',(key,)).fetchone()
        if not row:raise ValueError('CANDIDATE_REFRESH_REQUIRED')
        saved=json.loads(row[0]);site=saved.get('site');title=saved.get('title')
        if type(site)is not int or not isinstance(title,str) or not title:raise ValueError('CANDIDATE_REFRESH_REQUIRED')
        found=self.search([site],[title],budget)
        if not any(r['candidate_key']==key for r in found):raise ValueError('EXACT_CANDIDATE_UNAVAILABLE')
        return self.runtime[key]

    def supplement(self,key,budget):
        """Bounded exact-resource refresh using its configured authenticated site."""
        raw=self.runtime.get(key)
        if raw is None:return {'status':'DEFER','reason':'RESOURCE_REFRESH_REQUIRED'}
        rows=self.search([value(raw,'site')],[value(raw,'title') or ''],budget)
        return next((r for r in rows if r['candidate_key']==key),{'status':'DEFER','reason':'DETAILS_NOT_CONFIRMED'})

    def recognize(self, key, target, meta_service, *, custom_words=None, task_id=None, checkpoint=None, assistance=True):
        def check():
            self.checkpoint()
            if checkpoint:checkpoint()
            if self.deadline is not None and time.monotonic()>=self.deadline:raise ValueError('TICK_DEADLINE')
        raw = self.runtime.get(key)
        if raw is None:
            return {'status':'DEFER','reason':'RESOURCE_REFRESH_REQUIRED'}
        declared = (getattr(value(raw,'media_source'),'value',value(raw,'media_source')),value(raw,'media_id'))
        if all(declared) and declared[0] == target.media_source and identity_matches((target.media_source,target.media_id),declared) is False:
            return {'status':'REJECT','reason':'PROVIDER_ID_CONFLICT'}
        check()
        correction = meta_service.parse(sample_key('candidate',key,task_id), value(raw,'title') or '', value(raw,'description'), custom_words, task_id=task_id)
        allow_ai=assistance;assistance=None
        if self.ai is not None and allow_ai:
            check()
            correction,assistance=self.ai.assist(value(raw,'title') or '',value(raw,'description') or '',
                correction,corrector=meta_service.corrector,custom_words=custom_words)
        evidence=({k:getattr(assistance,k) for k in ('reason','source','attempts','usage','request_digest','generation')}
                  if assistance is not None else None)
        if correction.status != 'OK':
            result={'status':correction.status,'reason':'META_UNCONFIRMED','parse':correction.record()}
            if evidence is not None:result['ai']=evidence
            self._save_recognition(key,result)
            return result
        check()
        try:
            if assistance is not None and assistance.identity:self.ai.count('candidate_submitted')
            media = self.adapter.recognize(correction.meta,declared if all(declared) else (target.media_source,None))
        except Exception:
            return {'status':'ERROR','reason':'PROVIDER_UNAVAILABLE'}
        check()
        identity = self.adapter.identity(media) if media is not None else None
        matched = identity_matches((target.media_source,target.media_id),identity)
        mapping=None
        media_type=value(value(media,'type'),'value',value(media,'type'))
        if matched is False and identity[0]!=target.media_source:
            mapping=HostCandidateAdapter.source_identity(media,target.media_source)
            mapping=dict(mapping,source=target.media_source,media_type=media_type)
        matched=recognized_identity_matches(target,identity,mapping)
        if media_type and media_type!=target.media_type:matched=False
        if assistance is not None and assistance.identity and matched is True:self.ai.count('identity_matched')
        result = {'status':'OK' if matched is True else 'REJECT' if matched is False else 'DEFER',
                  'reason':'IDENTITY_VERIFIED' if matched is True else 'IDENTITY_UNCONFIRMED',
                  'identity':identity,'parse':correction.record()}
        if mapping is not None:result['identity_mapping']=mapping
        if assistance is not None:
            result['ai']=evidence
        self._save_recognition(key,result)
        return dict(result,media=media,meta=correction.meta)

    def _save_recognition(self,key,result):
        with self.repository.connection(write=True) as db:
            row = db.execute('SELECT data FROM candidates WHERE candidate_key=?',(key,)).fetchone()
            data = json.loads(row[0]); data['recognition'] = _stored(result)
            db.execute('UPDATE candidates SET data=?,updated_at=? WHERE candidate_key=?',(encoded(data),utcnow(),key))


def torrent_table(content):
    """Use the host's installed torrentool; reject unsupported v2/symlink metainfo."""
    if not isinstance(content,bytes) or not 1 <= len(content) <= 16*1024*1024:
        raise ValueError('bounded actual torrent bytes required; magnet is not metadata')
    from torrentool.api import Torrent, Bencode
    torrent = Torrent.from_string(content)
    decoded=Bencode.decode(content)
    if Bencode.encode(decoded)!=content:
        raise ValueError('noncanonical torrent metadata')
    info = decoded.get('info', {})
    if info.get('meta version') or any('symlink path' in f or 'l' in f.get('attr','') for f in info.get('files',[])):
        raise ValueError('unsupported v2 or symlink torrent')
    name=info.get('name.utf-8',info.get('name'))
    entries=info.get('files')
    if not isinstance(name,str) or any(x in name for x in ('/','\\',':','\x00')) or name in ('','.','..'):
        raise ValueError('unsafe torrent root')
    if entries is not None:
        table=[]
        for entry in entries:
            parts=entry.get('path.utf-8',entry.get('path'))
            if not isinstance(parts,list) or not parts or any(not isinstance(p,str) or p in ('','.','..') or any(x in p for x in ('/','\\',':','\x00')) for p in parts):
                raise ValueError('unsafe raw torrent path')
            table.append(('/'.join([name,*parts]),entry.get('length')))
    else:
        table=[(name,info.get('length'))]
    if not table or len(table)>10000 or not re.fullmatch('[a-f0-9]{40}',torrent.info_hash):
        raise ValueError('invalid torrent identity/table')
    if any(type(n)is not int or n<0 for _,n in table) or len({p for p,_ in table})!=len(table):
        raise ValueError('invalid torrent bytes or duplicate paths')
    piece_size=info.get('piece length');pieces=info.get('pieces')
    if type(piece_size)is not int or piece_size<=0 or not isinstance(pieces,(bytes,str)) or len(pieces)!=20*((sum(n for _,n in table)+piece_size-1)//piece_size):
        raise ValueError('invalid v1 piece table')
    return torrent.info_hash, table


def bind_files(table, target, *, dependencies=None, video_scopes=None):
    """Whole physical video scopes plus uniquely bound multi-language sidecars.

    Dependencies are reviewed evidence between managed assets, not a filename
    guess. Unknown video scope or ambiguous text subtitles defer the torrent.
    """
    files=[]
    for index,(path,size) in enumerate(table):
        suffix=PurePosixPath(path).suffix.casefold()
        role='video' if suffix in VIDEO else 'subtitle' if suffix in SUBTITLE else 'other'
        targets=[]
        if role=='video':
            if _sample_video(path):
                targets=[]
            elif video_scopes is not None:
                targets=video_scopes.get(index,[])
                if not targets:
                    raise ValueError('corrected physical video scope required')
            elif target.media_type=='电影':
                targets=[TargetUnit(target).key]
            else:
                match=re.search(r'(?i)(?<![A-Za-z0-9])S(\d{1,3})E(\d{1,4})(?:-E?(\d{1,4}))?(?![A-Za-z0-9])',PurePosixPath(path).stem)
                if not match:
                    raise ValueError('video scope unconfirmed')
                season,start,end=int(match[1]),int(match[2]),int(match[3] or match[2])
                if end<start or end-start>1000:
                    raise ValueError('invalid physical episode range')
                identity=Target(target.media_type,target.media_source,target.media_id,season,target.episode_group)
                targets=[TargetUnit(identity,n).key for n in range(start,end+1)]
        files.append(dict(index=index,path=path,size=size,role=role,targets=targets,requires=[]))
    # Validate every unselected path too. Temporary bindings do not leave this function.
    validate_files([dict(f,targets=f['targets'] or ['validation']) for f in files],list(range(len(files))))
    for item in files:
        if item['role']!='subtitle':
            continue
        path=PurePosixPath(item['path'])
        videos=[f for f in files if f['role']=='video' and PurePosixPath(f['path']).parent==path.parent]
        explicit=re.search(r'(?i)S\d{1,3}E\d{1,4}(?:-E?\d{1,4})?',path.stem)
        if explicit:
            videos=[f for f in videos if re.search(r'(?i)(?<![A-Za-z0-9])'+re.escape(explicit.group())+r'(?![A-Za-z0-9])',PurePosixPath(f['path']).stem)]
        if len(videos)!=1:
            raise ValueError('subtitle cannot bind uniquely')
        item['targets']=list(videos[0]['targets'])
    if dependencies is None:
        dependencies={}
    if not isinstance(dependencies,dict) or len(dependencies)>10000:
        raise ValueError('invalid dependency evidence')
    for source,required in dependencies.items():
        if type(source) is not int or not 0<=source<len(files) or not isinstance(required,list) or any(type(i)is not int or not 0<=i<len(files) or i==source or files[i]['role']=='video' for i in required):
            raise ValueError('invalid explicit dependency')
        if files[source]['role'] != 'other':
            files[source]['requires']=sorted(set(files[source]['requires']+[i for i in required if files[i]['role'] != 'other']))
    for _ in range(len(files)):
        changed=False
        for item in files:
            for dep in item['requires']:
                targets=sorted(set(files[dep]['targets'])|set(item['targets']))
                if targets!=files[dep]['targets']:
                    files[dep]['targets']=targets;changed=True
        if not changed:
            break
    validate_files(files,[f['index'] for f in files if f['targets']])
    validate_new_asset_scope(files)
    return files


class CandidatePipeline:
    """Actual acquisition → corrected physical Meta → W02/W04, with fresh gates.

    current_provider returns W06's measured current snapshots; absent facts never
    become MISSING. Credential-bearing acquisition and media objects live only
    for this bounded round, and must be refreshed after process restart/expiry.
    """
    def __init__(self,service,meta_service,policy,current_provider,client_factory,*,current=None,locked=None):
        self.service,self.meta,self.policy=service,meta_service,policy
        self.current,self.clients=current_provider,client_factory
        self.rounds={}
        self.active=current;self.locked=locked;self.archive=getattr(current_provider,'__self__',None)

    def evaluate(self,key,target,scope,*,simulation=False,opportunity_id=None,assistance=True,**kwargs):
        from .evidence import append
        output=self._evaluate_live(key,target,scope,assistance=assistance,**kwargs)
        with self.service.repository.connection() as db:
            row=db.execute('SELECT data FROM candidates WHERE candidate_key=?',(key,)).fetchone()
        context=getattr(self,'context',lambda:{})()
        context.update(policy=self.policy.semantic_hash,parse=self.meta.corrector.revision)
        reference=append(self.service.repository,key,scope,output,task_id=kwargs.get('task_id'),
            opportunity_id=opportunity_id,simulation=simulation,mode=kwargs.get('mode','episode'),
            observed=json.loads(row[0]) if row else {},context=context,sanitize=getattr(self,'sanitize',None))
        output.update(reference)
        for plan in output.get('plans',[]):plan.update(reference)
        return output

    def _evaluate_live(self,key,target,scope,*,downloader,save_path,dependencies=None,custom_words=None,task_id=None,mode='episode',assistance=True,locks=None):
        from .planner import Planner
        if self.active:self.active()
        result=self.service.recognize(key,target,self.meta,custom_words=custom_words,task_id=task_id,checkpoint=self.active,assistance=assistance)
        if self.active:self.active()
        if result['status']!='OK':
            return dict(plans=[],reason=result['reason'],status=result['status'])
        if not isinstance(save_path,str) or not PurePosixPath(save_path).is_absolute() or '..' in PurePosixPath(save_path).parts or '\\' in save_path:
            raise ValueError('explicit absolute download layout required')
        if self.clients(downloader) is None:
            raise ValueError('configured downloader unavailable')
        try:
            content=self.service.adapter.acquire(self.service.runtime[key])
            infohash,table=torrent_table(content)
        except Exception:
            return dict(plans=[],reason='TORRENT_METADATA_UNAVAILABLE')
        if self.active:self.active()
        scopes={};parse_evidence={}
        for index,(path,size) in enumerate(table):
            if PurePosixPath(path).suffix.casefold() not in VIDEO or _sample_video(path):
                continue
            corrected=self.meta.parse(sample_key('file',key,task_id,index),path,custom_words=custom_words,task_id=task_id,is_path=True,force_video=True)
            if self.active:self.active()
            if corrected.status=='DEFER' and corrected.reasons==('BRACKET_NAME_AMBIGUOUS',):
                brackets=list(BRACKET.finditer(PurePosixPath(path).name))
                alias=value(result['meta'],'en_name');parsed=corrected.native.get('en_name')
                if (len(brackets)==1 and brackets[0].start()==0
                        and (brackets[0][1] or brackets[0][2]).strip()==value(result['media'],'title')
                        and isinstance(alias,str) and isinstance(parsed,str) and alias
                        and re.sub(r'\W+','',alias.casefold())==re.sub(r'\W+','',parsed.casefold())):
                    corrected=self.meta.parse(sample_key('file',key,task_id,index),path,
                                              custom_words=custom_words,locks=('name',),task_id=task_id,
                                              is_path=True,force_video=True)
            if corrected.status!='OK':
                return dict(plans=[],reason='PHYSICAL_META_UNCONFIRMED')
            if target.media_type=='电影':
                scopes[index]=[TargetUnit(target).key]
            else:
                m=corrected.meta;season=value(m,'begin_season');endseason=value(m,'end_season');start=value(m,'begin_episode');end=value(m,'end_episode')
                end=start if end is None else end
                if type(season)is not int or type(start)is not int or type(end)is not int or start<0 or end<start or end-start>1000 or endseason not in (None,season):
                    return dict(plans=[],reason='PHYSICAL_SCOPE_UNCONFIRMED')
                t=Target(target.media_type,target.media_source,target.media_id,season,target.episode_group)
                scopes[index]=[TargetUnit(t,e).key for e in range(start,end+1)]
            parse_evidence[str(index)]=corrected.record()
        try:
            files=bind_files(table,target,dependencies=dependencies,video_scopes=scopes)
        except ValueError:
            return dict(plans=[],reason='ASSET_BINDING_UNCONFIRMED')
        raw=self.service.runtime[key]
        facts={}
        for index,keys in scopes.items():
            data={k:value(raw,k) for k in ('title','description','labels','size','seeders','downloadvolumefactor')}
            data['title']=PurePosixPath(table[index][0]).name+' '+(data['title'] or '')
            data['missing_fields']=[k for k in ('description','labels') if data[k] is None]
            data['description']=data['description'] or '';data['labels']=data['labels'] or []
            for field in ('original_language','production_countries','origin_country','genre_ids'):
                item=value(result['media'],field)
                if item is not None:data[field]=item
            fact=self.policy.normalize(data)
            for unit in keys:facts[unit]=fact
        candidate=dict(candidate_key=key,infohash=infohash,downloader=downloader,save_path=save_path,parse_revision=self.meta.corrector.revision,facts=facts,classification=self.service.adapter.classify(result['media']),torrent_files=files,available=True,identity_ok=True,scope_ok=True,parse_status='OK',files_verified=True,configuration_verified=True)
        projection=self.service.repository.setting('subtitle-candidate:'+key)
        if projection:
            from .archive import digest
            if projection['source_digest']==digest([infohash,files,downloader,save_path]):
                candidate.update(local_assets=projection['assets'],source_plan=projection['source_plan'])
        self.rounds[key]=dict(candidate=candidate,media=result['media'],meta=result['meta'],content=content,scope=list(scope),mode=mode,locks=locks,acquired=time.monotonic())
        while len(self.rounds)>1000:self.rounds.pop(next(iter(self.rounds)))
        output=self._evaluate(candidate,scope,mode,locks=locks)
        with self.service.repository.connection(write=True) as db:
            row=db.execute('SELECT data FROM candidates WHERE candidate_key=?',(key,)).fetchone()
            data=json.loads(row[0]);data.update(infohash=infohash,torrent_files=files,file_parse=parse_evidence,classification=candidate['classification'],last_decision=_stored(output))
            db.execute('UPDATE candidates SET data=?,updated_at=? WHERE candidate_key=?',(encoded(_stored(data)),utcnow(),key))
        return output

    def _evaluate(self,candidate,scope,mode,*,locks=None):
        from .execution import Exclusions
        from .planner import Planner
        exclusions=Exclusions(self.service.repository)
        excluded={k for k in scope if exclusions.matches(dict(candidate,targets=[k]),facts=dict(candidate['facts'][k].raw) if k in candidate['facts'] else {})}
        proof=self.archive.candidate_evidence(candidate,scope) if self.archive and hasattr(self.archive,'candidate_evidence') else {}
        candidate=dict(candidate,same_video_verified=proof.get('same_video_verified',{}))
        result=Planner(self.policy).evaluate(candidate,proof.get('current',self.current(scope)),scope,mode=mode,excluded=excluded,locked=self.locked if locks is None else locks,
            same_assets_verified=proof.get('same_assets_verified',frozenset()),consumed=proof.get('consumed',frozenset()))
        if result['enrichments']:result['evidence_manifest']=proof['manifest']
        return result

    def revalidate(self,plan):
        if self.active:self.active()
        s=plan['snapshot'];round=self.rounds.get(s['candidate_key'])
        if round is None or time.monotonic()-round['acquired']>300:
            raise ValueError('CANDIDATE_REFRESH_REQUIRED')
        if self.meta.corrector.revision!=s['parse_revision'] or self.policy.semantic_hash!=s['policy_revision']:
            raise ValueError('POLICY_PARSE_CHANGED')
        candidate=dict(round['candidate'],classification=self.service.adapter.classify(round['media']))
        if s.get('local_assets'):
            from .execution import safe_local,asset_hashes
            for asset in s['local_assets']:
                path=safe_local(s['save_path'],Path(s['save_path'])/asset['file']['path'])
                if path.stat().st_size!=asset['file']['size'] or path.stat().st_mtime_ns!=asset['mtime_ns'] or asset_hashes(path)!=(asset['sha256'],asset['sha1']):raise ValueError('LOCAL_ASSET_CHANGED')
            if not candidate.get('local_assets'):
                s={k:v for k,v in s.items() if k not in ('local_assets','source_plan')}
                s['selected_indices']=[i for i in s['selected_indices'] if i<len(s['torrent_files'])]
        from .planner import active_snapshot
        expected=active_snapshot(dict(plan,snapshot=s));active=set(expected['targets'])
        expected={k:v for k,v in expected.items() if k not in ('decision_id','decision_digest')}
        fresh=self._evaluate(candidate,sorted(active),round['mode'] if active==set(s['targets']) else 'episode',locks=round.get('locks'))
        if expected not in fresh['plans']:
            raise ValueError('CANDIDATE_POLICY_OR_CURRENT_CHANGED')
        return candidate

    def executor(self):
        from .execution import StrictExecutor
        return StrictExecutor(self.service.repository,self.clients,revalidate=self.revalidate,dispatch_gate=self.active)

    def execute(self,plan_id,*,resume=False):
        executor=self.executor();plan=executor.authority.plan(plan_id)
        self.revalidate(plan)
        round=self.rounds[plan['snapshot']['candidate_key']]
        result=executor.execute(plan_id,round['content'],resume=resume)
        if result['state']=='RUNNING':result['subtitles']=self.subtitles(plan_id)
        return result

    def resume(self,plan_id):
        result=self.executor().resume(plan_id)
        if result['state']=='RUNNING':result['subtitles']=self.subtitles(plan_id)
        return result

    def subtitles(self,plan_id):
        """Preserve host workflows explicitly after add; completion is not inferred."""
        from app.chain.download import DownloadChain
        from app.sdk.media import Context
        executor=self.executor();plan,s,indices,vector=executor._plan(plan_id)
        if s.get('local_assets'):return dict(assets_state='VERIFIED',fresh_asset_plan_required=False)
        owned=executor._owned(s)
        if not owned or owned['state'] not in ('PAUSED_VERIFIED','RUNNING'):
            raise ValueError('MANAGED_DOWNLOAD_NOT_READY')
        round=self.rounds[s['candidate_key']]
        context=Context(meta_info=round['meta'],media_info=round['media'],torrent_info=self.service.runtime[s['candidate_key']])
        chain=DownloadChain();out=[];noops=[]
        for method in ('download_added','download_site_subtitles'):
            if self.active:self.active()
            if method=='download_site_subtitles':
                configured=getattr(getattr(chain,'runtime_config',None),'download_subtitle',None)
                if configured is False or not round['content'] or not value(self.service.runtime[s['candidate_key']],'page_url'):
                    noops.append(method);continue
            if method=='download_added':
                try:
                    from app.sdk.plugin import ModuleManager,PluginManager
                    from collections.abc import Mapping
                    handlers=list(ModuleManager().get_running_modules('download_added'))
                    plugins=PluginManager().get_plugin_modules()
                    if not isinstance(plugins,dict) or any(not isinstance(methods,Mapping) for methods in plugins.values()):raise TypeError('unknown plugin callback projection')
                    callbacks=[methods.get('download_added') for methods in plugins.values() if callable(methods.get('download_added'))]
                    if not handlers and not callbacks:noops.append(method);continue
                except (ImportError,AttributeError,TypeError):pass
            kwargs=dict(context=context,download_dir=Path(s['save_path']),torrent_content=round['content'])
            if method=='download_site_subtitles':kwargs.update(download_hash=s['infohash'],downloader=s['downloader'])
            def dispatch():
                getattr(chain,method)(**kwargs)
                return {'dispatch_completed':True,'assets_verified':False}
            ok,action,_=executor._mutation(plan,indices,vector,method,'ORGANIZE',dispatch,{'workflow':method})
            out.append({'workflow':method,'action_id':action,'state':'DISPATCHED' if ok else 'UNKNOWN'})
            if not ok:break
        state={'workflows':out,'noops':noops,'assets_state':'UNVERIFIED','fresh_asset_plan_required':True}
        self.service.repository.setting('subtitle:'+plan_id,state)
        return state

    def organize(self,plan_id,target_root):
        from .execution import HostOrganization,Organizer
        executor=self.executor();plan=executor.authority.plan(plan_id);self.revalidate(plan)
        media=self.rounds[plan['snapshot']['candidate_key']]['media']
        return Organizer(executor,HostOrganization(media,repository=self.service.repository)).organize(plan_id,target_root)
