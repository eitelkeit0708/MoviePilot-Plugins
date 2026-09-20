"""Scoped observed media facts, separate from the existing delivery authority.

No network operations occur in SQLite write transactions. Observations are not
remote fencing tokens: bounded scans and fresh before-commit checks are explicit.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from uuid import uuid4
from urllib.parse import urlencode
from http.cookies import SimpleCookie

from .planner import Authority, BARRIERS, TargetUnit, encoded, asset_table
from .policy import Version
from .repository import Target, utcnow
from .scheduler import instant, parse, stamp


def digest(value):
    return sha256(encoded(value).encode('utf-8')).hexdigest()


def posix(value):
    if not isinstance(value, str) or not value.startswith('/') or any(c in value for c in ('\\', '\x00', '\n', '\r')):
        raise ValueError('UNSUPPORTED_TARGET')
    if any(part in ('.', '..', '') for part in value.split('/')[1:]):
        raise ValueError('PATH_ESCAPE')
    return value.rstrip('/') or '/'


def within(path, prefix):
    return path == prefix or path.startswith(prefix.rstrip('/') + '/')


def content(value):
    h, size = value.get('sha1'), value.get('size')
    if not isinstance(h, str) or not re.fullmatch('[0-9a-fA-F]{40}', h) or type(size) is not int or size < 0:
        raise ValueError('HASH_UNAVAILABLE')
    return {'sha1': h.lower(), 'size': size}


def item_projection(item):
    """Persist only media facts, never Emby playback URLs or session fields."""
    result = {k: item[k] for k in ('Id', 'Type', 'Name', 'Path', 'ProviderIds', 'ParentId', 'SeriesId',
                                  'ParentIndexNumber', 'IndexNumber', 'IndexNumberEnd') if k in item}
    def streams(rows):
        fields = {'Type', 'Codec', 'Height', 'Width', 'Language', 'Profile', 'VideoRange', 'VideoRangeType', 'DvProfile', 'IsExternal', 'Path', 'Index'}
        output = [{k: v for k, v in s.items() if k in fields} for s in rows]
        for s in output:
            if s.get('Path'):
                posix(s['Path'])
        return output
    if result.get('Path'):
        posix(result['Path'])
    if isinstance(item.get('MediaStreams'), list):
        result['MediaStreams'] = streams(item['MediaStreams'])
    if isinstance(item.get('MediaSources'), list):
        result['MediaSources'] = []
        for source in item['MediaSources']:
            entry = {k: source[k] for k in ('Id', 'Path') if k in source}
            if entry.get('Path'):
                posix(entry['Path'])
            if isinstance(source.get('MediaStreams'), list):
                entry['MediaStreams'] = streams(source['MediaStreams'])
            result['MediaSources'].append(entry)
    return result


def snapshot(s):
    # Python 3.12 Windows stat/fstat expose different ctime semantics after writes.
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns if os.name != 'nt' else None]


def read_strm(path, root, maximum):
    """Resolve under the allowed root, then pin/check the opened regular file."""
    try:
        root = Path(root).resolve(strict=True)
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError('PATH_ESCAPE')
        before = resolved.stat()
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(resolved, flags)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or snapshot(before) != snapshot(opened):
                raise ValueError('STRM_CHANGED')
            if opened.st_size > maximum:
                raise ValueError('STRM_TOO_LARGE')
            # Linux closes ancestor-symlink races by checking the actual descriptor.
            fdpath = Path('/proc/self/fd') / str(stream.fileno())
            if fdpath.exists() and not fdpath.resolve().is_relative_to(root):
                raise ValueError('PATH_ESCAPE')
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise ValueError('STRM_TOO_LARGE')
            if snapshot(os.fstat(stream.fileno())) != snapshot(opened):
                raise ValueError('STRM_CHANGED')
        if Path(path).resolve(strict=True) != resolved or snapshot(resolved.stat()) != snapshot(opened):
            raise ValueError('STRM_CHANGED')
        lines = data.decode('utf-8-sig').strip('\r\n').splitlines()
        if len(lines) != 1 or not lines[0]:
            raise ValueError('STRM_MULTILINE')
        return lines[0], {'path': str(path), 'resolved': str(resolved), 'root': str(root),
                          'stat': snapshot(opened), 'bytes_hash': sha256(data).hexdigest(), 'maximum': maximum}
    except (OSError, UnicodeError):
        raise ValueError('STRM_UNREADABLE') from None


class Mappings:
    def __init__(self, rules):
        self.rules = json.loads(encoded(rules))
        if not self.rules or len(self.rules) > 1000:
            raise ValueError('MAPPINGS_REQUIRED')
        seen = set()
        for rule in self.rules:
            required = ('id', 'revision', 'emby_service', 'library_id', 'cloud_scope_id', 'local_strm_prefix')
            if any(not isinstance(rule.get(k), str) or not rule[k] for k in required) or rule['id'] in seen:
                raise ValueError('INVALID_MAPPING')
            seen.add(rule['id'])
            for key in ('emby_prefix', 'playback_prefix', 'cd2_prefix'):
                rule[key] = posix(rule[key])
            if not Path(rule['local_strm_prefix']).is_absolute():
                raise ValueError('INVALID_MAPPING')
            rule.setdefault('max_strm_bytes', 65536)
            if type(rule['max_strm_bytes']) is not int or not 1 <= rule['max_strm_bytes'] <= 1048576:
                raise ValueError('INVALID_MAPPING')
        self.revision = digest(self.rules)

    def scoped(self, service, library):
        rules = [r for r in self.rules if (r['emby_service'], r['library_id']) == (service, str(library))]
        if not rules:
            raise ValueError('OUTSIDE_LIBRARY')
        return rules

    @staticmethod
    def match(rules, path, field):
        path = posix(path)
        matches = [r for r in rules if within(path, r[field])]
        if not matches:
            raise ValueError('NO_MAPPING')
        length = max(len(r[field]) for r in matches)
        matches = [r for r in matches if len(r[field]) == length]
        if len(matches) != 1:
            raise ValueError('AMBIGUOUS_MAPPING')
        return matches[0]

    def mounts(self, service, library):
        for rule in self.scoped(service, library):
            root = Path(rule['local_strm_prefix'])
            if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
                raise ValueError('STRM_UNREADABLE')

    def resolve(self, service, library, item_path, source_path):
        rules = self.scoped(service, library)
        check = None
        if str(item_path).lower().endswith('.strm'):
            rule = self.match(rules, item_path, 'emby_prefix')
            suffix = posix(item_path)[len(rule['emby_prefix']):].lstrip('/')
            target, check = read_strm(Path(rule['local_strm_prefix']) / suffix,
                                      rule['local_strm_prefix'], rule['max_strm_bytes'])
            second = self.match([r for r in rules if r['cloud_scope_id'] == rule['cloud_scope_id']], target, 'playback_prefix')
            internal = second['cd2_prefix'] + target[len(second['playback_prefix']):]
            if source_path and source_path != item_path:
                if str(source_path).lower().endswith('.strm'):
                    raise ValueError('PLAYBACK_SOURCE_CONFLICT')
                actual = self.match([r for r in rules if r['cloud_scope_id'] == rule['cloud_scope_id']], source_path, 'playback_prefix')
                source_internal = actual['cd2_prefix'] + source_path[len(actual['playback_prefix']):]
                if source_internal != internal:
                    raise ValueError('PLAYBACK_SOURCE_CONFLICT')
                second, internal = actual, source_internal
        else:
            target = source_path or item_path
            second = self.match([r for r in rules if r.get('allow_direct') is True], target, 'playback_prefix')
            internal = second['cd2_prefix'] + target[len(second['playback_prefix']):]
        return second, posix(internal), check


def provider_id(ids, source):
    aliases = {'themoviedb': ('tmdb', 'themoviedb'), 'douban': ('douban',), 'tvdb': ('tvdb',), 'imdb': ('imdb',)}
    names = aliases.get(source, (source,))
    values = {str(v) for k, v in (ids or {}).items() if k.casefold() in names and v not in (None, '', '0')}
    if len(values) != 1:
        raise ValueError('IDENTITY_CONFLICT' if values else 'IDENTITY_UNKNOWN')
    return values.pop()


def units(item, rules, series, *, scope=None):
    source = rules[0].get('media_source', 'themoviedb')
    group = rules[0].get('episode_group', '')
    if item.get('Type') == 'Movie':
        return [TargetUnit(Target('电影', source, provider_id(item.get('ProviderIds'), source))).key]
    if item.get('Type') != 'Episode':
        raise ValueError('UNSUPPORTED_ITEM')
    parent = series.get(str(item.get('SeriesId')))
    if not parent:
        raise ValueError('SERIES_IDENTITY_UNKNOWN')
    if group:
        if not scope:raise ValueError('GROUP_SCOPE_REQUIRED')
        target=Target(*json.loads(scope['target_key']))
        if (target.media_source,target.media_id,target.episode_group)!=(source,provider_id(parent.get('ProviderIds'),source),group):
            raise ValueError('GROUP_SCOPE_CONFLICT')
        episode_id=provider_id(item.get('ProviderIds'),source)
        if not episode_id.isascii() or not episode_id.isdecimal():raise ValueError('GROUP_EPISODE_UNVERIFIED')
        matches=[r for r in scope['provider_rows'] if str(r.get('id'))==episode_id]
        if not matches:return []
        if len(matches)!=1 or matches[0].get('season_number')!=item.get('ParentIndexNumber'):
            raise ValueError('GROUP_EPISODE_UNVERIFIED')
        if item.get('IndexNumberEnd',item.get('IndexNumber'))!=item.get('IndexNumber'):
            raise ValueError('GROUP_EPISODE_RANGE_UNVERIFIED')
        return [TargetUnit(target,matches[0]['episode_number']).key]
    season, episode = item.get('ParentIndexNumber'), item.get('IndexNumber')
    end = item.get('IndexNumberEnd', episode)
    if any(type(x) is not int or x < 0 for x in (season, episode, end)) or not episode <= end <= episode + 100:
        raise ValueError('EPISODE_IDENTITY_UNKNOWN')
    target = Target('电视剧', source, provider_id(parent.get('ProviderIds'), source), season, group)
    return [TargetUnit(target, n).key for n in range(episode, end + 1)]


def stream_facts(item, media_source):
    streams = media_source.get('MediaStreams')
    if not isinstance(streams, list):
        streams = item.get('MediaStreams') if len(item.get('MediaSources', [])) == 1 else None
    if not isinstance(streams, list):
        raise ValueError('STREAMS_INCOMPLETE')
    video = [s for s in streams if s.get('Type') == 'Video']
    audio = [s for s in streams if s.get('Type') == 'Audio']
    if len(video) != 1 or not audio:
        raise ValueError('STREAMS_INCOMPLETE')
    v = video[0]
    width, height = v.get('Width'), v.get('Height')
    resolution = None
    if type(width) is int and type(height) is int and width > 0 and height > 0:
        resolution = 4320 if width > 5000 or height > 2500 else 2160 if width > 2560 or height > 1440 else 1080 if width > 1280 or height > 720 else 720
    vr = str(v.get('VideoRangeType') or v.get('VideoRange') or '').casefold()
    picture = 2 if 'dovi' in vr or 'dolbyvision' in vr or v.get('DvProfile') else 1 if 'hdr' in vr or 'hlg' in vr else 0 if vr == 'sdr' else None
    codecs = {str(a.get('Codec', '')).casefold() for a in audio}
    audio_rank = 3 if codecs & {'truehd', 'flac', 'dts-hd', 'dtshd', 'pcm_s16le', 'pcm_s24le'} else 2 if any('atmos' in str(a.get('Profile', '')).lower() for a in audio) else 1 if codecs & {'eac3', 'e-ac-3'} else 0 if all(codecs) else None
    zh = {'chi', 'zho', 'zh', 'zh-cn', 'zh-tw', 'cmn', 'mandarin'}
    pgs = any(s.get('Type') == 'Subtitle' and str(s.get('Codec', '')).lower() in ('pgssub', 'hdmv_pgs_subtitle', 'pgs') and str(s.get('Language', '')).lower() in zh for s in streams)
    return {'title': PurePosixPath(media_source.get('Path') or item.get('Path') or '').name,
            'technical': {'resolution': resolution, 'picture': picture, 'audio': audio_rank},
            'chinese_pgs': pgs, 'missing_fields': ['description', 'labels'], 'description': '', 'labels': []}, streams


class HostArchiveSources:
    """Owned bounded raw clients; public host configuration and SDK methods only."""
    def __init__(self, plugin, *, cloud_scopes, libraries):
        self.plugin, self.scopes = plugin, json.loads(encoded(cloud_scopes))
        self.libraries = {s: {str(i) for i in ids} for s, ids in libraries.items()}
        self.clients = {}
        self.accounts = {}
        self.checkpoint=lambda:None

    def close(self):
        for client, _, _ in self.clients.values():
            client.channel.close()
        self.clients.clear()
        self.accounts.clear()

    def _emby(self, service, library, query):
        self.checkpoint()
        if str(library) not in self.libraries.get(service, set()):
            raise ValueError('OUTSIDE_LIBRARY')
        from app.sdk.services import MediaServerHelper
        wrapper = MediaServerHelper().get_service(service, type_filter='emby')
        if not wrapper or not wrapper.instance:
            raise ValueError('EMBY_SERVICE_UNAVAILABLE')
        params = dict(ParentId=str(library), Recursive='true', IncludeItemTypes='Movie,Episode,Series',
                      Fields='Path,ProviderIds,MediaSources,MediaStreams,ParentId')
        params.update(query)
        self.checkpoint()
        response = wrapper.instance.get_data('[HOST]emby/Users/[USER]/Items?' + urlencode(params) + '&api_key=[APIKEY]')
        if response is None:
            raise ValueError('EMBY_UNAVAILABLE')
        try:
            if response.status_code != 200:
                raise ValueError('EMBY_HTTP_' + str(response.status_code))
            try:
                data = response.json()
            except Exception:
                raise ValueError('EMBY_INVALID_JSON') from None
            if not isinstance(data, dict) or not isinstance(data.get('Items'), list) or type(data.get('TotalRecordCount')) is not int or data['TotalRecordCount'] < 0:
                raise ValueError('EMBY_INVALID_PAGE')
            return data
        finally:
            response.close()

    def emby_page(self, service, library, start, limit):
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('INVALID_SCAN_LIMITS')
        return self._emby(service, library, dict(StartIndex=start, Limit=limit, SortBy='SortName', SortOrder='Ascending'))

    def emby_item(self, service, library, item_id):
        if not isinstance(item_id, str) or not item_id or len(item_id) > 256:
            raise ValueError('INVALID_ITEM_ID')
        page = self._emby(service, library, dict(Ids=item_id, StartIndex=0, Limit=2))
        if page['TotalRecordCount'] != 1 or len(page['Items']) != 1 or str(page['Items'][0].get('Id')) != item_id:
            raise ValueError('EMBY_ITEM_NOT_CONFIRMED')
        return page['Items'][0]

    def emby_target_page(self,service,library,target,start,limit):
        """Library → verified Series → children; never a whole-library RSS scan."""
        if not isinstance(target,Target) or type(start)is not int or start<0 or type(limit)is not int or not 1<=limit<=1000:
            raise ValueError('INVALID_SCAN_LIMITS')
        provider={'themoviedb':'tmdb','tvdb':'tvdb','imdb':'imdb'}.get(target.media_source)
        if not provider:raise ValueError('EMBY_PROVIDER_QUERY_UNSUPPORTED')
        kind='Movie' if target.media_type=='电影' else 'Series'
        query=dict(IncludeItemTypes=kind,AnyProviderIdEquals=provider+'.'+target.media_id,
            StartIndex=start if kind=='Movie' else 0,Limit=limit if kind=='Movie' else 2,SortBy='SortName',SortOrder='Ascending')
        page=self._emby(service,library,query)
        if not isinstance(page.get('Items'),list) or type(page.get('TotalRecordCount'))is not int:
            raise ValueError('EMBY_INVALID_PAGE')
        for item in page['Items']:
            if item.get('Type')!=kind or provider_id(item.get('ProviderIds'),target.media_source)!=target.media_id:
                raise ValueError('IDENTITY_CONFLICT')
        if kind=='Movie':return page
        if page['TotalRecordCount']==0 and not page['Items']:return dict(Items=[],TotalRecordCount=0)
        if page['TotalRecordCount']!=1 or len(page['Items'])!=1:raise ValueError('SERIES_IDENTITY_AMBIGUOUS')
        series=page['Items'][0]
        if not isinstance(series.get('Id'),str) or not series['Id']:raise ValueError('SERIES_IDENTITY_UNKNOWN')
        page=self._emby(service,library,dict(ParentId=series['Id'],IncludeItemTypes='Episode',
            StartIndex=start,Limit=limit,SortBy='SortName',SortOrder='Ascending'))
        if start==0 and not page['Items']:raise ValueError('SERIES_EPISODES_UNVERIFIED')
        if any(i.get('Type')!='Episode' or i.get('SeriesId')!=series['Id'] for i in page['Items']):
            raise ValueError('SERIES_IDENTITY_CONFLICT')
        return dict(page,Series=item_projection(series))

    def classify_target(self, key):
        self.checkpoint()
        from .candidates import HostCandidateAdapter
        from app.chain.media import MediaChain
        from app.sdk.media import normalize_media_source
        from app.schemas.types import MediaType
        media_type, source, media_id, _, group, _ = json.loads(key)
        media = MediaChain().run_module('recognize_media', meta=None, mtype=MediaType(media_type), media_source=normalize_media_source(source), media_id=media_id, episode_group=group or None)
        actual_type = getattr(media, 'type', None)
        if media is None or HostCandidateAdapter.identity(media) != (source, media_id) or getattr(actual_type, 'value', actual_type) != media_type:
            raise ValueError('IDENTITY_CONFLICT')
        self.checkpoint()
        return HostCandidateAdapter.classify(media)

    def _client(self, scope_id, timeout):
        self.checkpoint()
        if scope_id not in self.scopes:
            raise ValueError('CLOUD_SCOPE_UNKNOWN')
        if scope_id in self.clients:
            client, pb, metadata = self.clients[scope_id]
            try:
                root = client.stub.FindFileByPath(pb.FindFileByPathRequest(parentPath='', path=self.scopes[scope_id]['root']), metadata=metadata, timeout=timeout)
            except Exception:
                raise ValueError('CLOUD_CONNECTION_FAILED') from None
            if root.fullPathName != self.scopes[scope_id]['root'] or not root.isDirectory or str(root.CloudAPI.userName) != self.accounts[scope_id]:
                raise ValueError('ACCOUNT_MISMATCH')
            return client, pb, metadata
        from clouddrive2_client import CloudDriveClient
        from clouddrive2_client.proto import clouddrive_pb2 as pb
        scope = self.scopes[scope_id]
        config = self.plugin.get_config(scope.get('cd2_plugin', 'CloudDriveDisk')) or {}
        pconfig = self.plugin.get_config(scope.get('p115_plugin', 'P115Disk')) or {}
        if not config.get('enabled') or not all(config.get(k) for k in ('host', 'port', 'username', 'password')):
            raise ValueError('CLOUD_CONFIG_MISSING')
        cookie = SimpleCookie()
        try:
            cookie.load(pconfig.get('cookie', ''))
            uid = cookie['UID'].value.split('_', 1)[0]
        except Exception:
            raise ValueError('ACCOUNT_BINDING_MISSING') from None
        if not uid.isdecimal():
            raise ValueError('ACCOUNT_BINDING_MISSING')
        client = CloudDriveClient(str(config['host']) + ':' + str(config['port']))
        try:
            self.checkpoint()
            token = client.stub.GetToken(pb.GetTokenRequest(userName=config['username'], password=config['password']), timeout=timeout)
            if not token.success or not token.token:
                raise ValueError('CLOUD_AUTH_FAILED')
            client.jwt_token = token.token
            metadata = [('authorization', 'Bearer ' + client.jwt_token)]
            self.checkpoint()
            root = client.stub.FindFileByPath(pb.FindFileByPathRequest(parentPath='', path=posix(scope['root'])), metadata=metadata, timeout=timeout)
            if not root.isDirectory or root.fullPathName != scope['root'] or str(root.CloudAPI.userName) != uid:
                raise ValueError('ACCOUNT_MISMATCH')
            self.clients[scope_id] = (client, pb, metadata)
            self.accounts[scope_id] = uid
            return self.clients[scope_id]
        except Exception as error:
            client.channel.close()
            if isinstance(error, ValueError):
                raise
            raise ValueError('CLOUD_CONNECTION_FAILED') from None

    def cloud_stat(self, scope_id, path, *, refresh=False, timeout=20):
        if type(timeout) not in (int, float) or not 0 < timeout <= 60:
            raise ValueError('INVALID_TIMEOUT')
        path = posix(path)
        scope = self.scopes.get(scope_id, {})
        if not any(within(path, posix(prefix)) for prefix in scope.get('allowed_prefixes', [])):
            raise ValueError('CLOUD_PATH_UNAUTHORIZED')
        client, pb, metadata = self._client(scope_id, timeout)
        try:
            if refresh:
                self.checkpoint()
                call = client.stub.GetSubFiles(pb.ListSubFileRequest(path=str(PurePosixPath(path).parent), forceRefresh=True), metadata=metadata, timeout=timeout)
                try:
                    count = 0
                    for reply in call:
                        count += len(reply.subFiles)
                        if count > 10000:
                            raise ValueError('CLOUD_LIST_LIMIT')
                finally:
                    if hasattr(call, 'cancel'):
                        call.cancel()
            self.checkpoint()
            raw = client.stub.FindFileByPath(pb.FindFileByPathRequest(parentPath='', path=path), metadata=metadata, timeout=timeout)
            if raw.fullPathName != path or raw.isDirectory:
                raise ValueError('CLOUD_PATH_CONFLICT')
            result = dict(cloud_scope_id=scope_id, account_ref=digest([scope_id, self.accounts[scope_id]]), path=path, sha1=raw.fileHashes.get(2, ''), size=int(raw.size), cd2_id=str(raw.id), p115_id='')
            # CD2 placeholders expose client-declared hashes before any upload.
            # Every file needs independent account/path-bound provider proof.
            result.update(self._fallback(scope_id, path, result['size'], timeout))
            result.update(content(result))
            return result
        except ValueError:
            raise
        except Exception:
            raise ValueError('CLOUD_STAT_FAILED') from None

    def _fallback(self, scope_id, path, size, timeout):
        """Independent 115 object proof; CD2 IDs never imply provider IDs."""
        scope=self.scopes[scope_id];parent=str(PurePosixPath(path).parent)
        relative='/' + str(PurePosixPath(parent).relative_to(PurePosixPath(scope['root'])))
        if relative=='/.':relative='/'
        from p115client import P115Client
        self.checkpoint()
        client=P115Client((self.plugin.get_config(scope.get('p115_plugin','P115Disk')) or {})['cookie'])
        matches,offset,total=[],0,None;deadline=time.monotonic()+min(30,timeout)
        def remaining():
            self.checkpoint()
            value=deadline-time.monotonic()
            if value<=0:raise ValueError('P115_LIST_TIMEOUT')
            return min(timeout,value)
        try:
            if str(client.user_id)!=self.accounts[scope_id]:raise ValueError('ACCOUNT_MISMATCH')
            response=client.fs_dir_getid(relative,timeout=remaining());cid=str(response.get('id',''))
            if response.get('state') is not True or not cid.isdecimal() or (cid=='0' and relative!='/'):raise ValueError('P115_PARENT_UNKNOWN')
            configured=scope.get('p115_parents',{}).get(parent)
            if configured is not None and configured!=cid:raise ValueError('P115_PARENT_UNKNOWN')
            for _ in range(100):
                page = client.fs_files({'cid': cid, 'offset': offset, 'limit': 1000, 'cur': 1, 'record_open_time': 0}, timeout=remaining())
                rows, count = page.get('data'), page.get('count')
                chain = page.get('path') or []
                actual = str(page.get('cid') if page.get('cid') is not None else chain[-1].get('cid') if chain else '')
                if page.get('state') is not True or actual != cid or not isinstance(rows, list) or type(count) is not int or count < 0 or (total is not None and count != total):
                    raise ValueError('P115_LIST_INCOMPLETE')
                names=[str(x.get('name',x.get('n',''))) for x in chain if str(x.get('cid'))!='0']
                if '/'+ '/'.join(names)!=relative:raise ValueError('P115_PARENT_UNKNOWN')
                total = count
                for row in rows:
                    if 'fid' in row and str(row.get('cid')) == cid and row.get('n') == PurePosixPath(path).name:
                        matches.append(row)
                offset += len(rows)
                if offset == total:
                    break
                if not rows or offset > total:
                    raise ValueError('P115_LIST_INCOMPLETE')
            else:
                raise ValueError('P115_LIST_LIMIT')
            if len(matches) != 1 or matches[0].get('s') != size or not isinstance(matches[0].get('fid'), str) or not matches[0]['fid'].isdecimal():
                raise ValueError('P115_OBJECT_AMBIGUOUS')
            result = dict(sha1=matches[0].get('sha'), size=size)
            return dict(content(result), p115_id=matches[0]['fid'])
        finally:
            if hasattr(client, 'close'):
                client.close()


class Archive:
    def __init__(self, repository, policy, sources, *, mappings,scope_provider=None):
        self.repository, self.policy, self.sources = repository, policy, sources
        self.mappings = Mappings(mappings)
        self.authority = Authority(repository)
        self.scope_provider=scope_provider

    def resolve_item(self, service, library, item, *, series=None, replacements=(), ignored=(), scope=None):
        item = item_projection(item)
        rules = self.mappings.scoped(service, library)
        keys = units(item, rules, series or {},scope=scope)
        sources = item.get('MediaSources')
        if not isinstance(sources, list) or not sources:
            raise ValueError('MEDIA_SOURCES_INCOMPLETE')
        result = []
        for media_source in sources:
            source_path = media_source.get('Path')
            item_path = source_path if str(source_path).lower().endswith('.strm') else item.get('Path')
            rule, path, check = self.mappings.resolve(service, library, item_path, source_path)
            remote = self.sources.cloud_stat(rule['cloud_scope_id'], path, refresh=True)
            if (remote.get('cloud_scope_id'), remote.get('path')) != (rule['cloud_scope_id'], path):
                raise ValueError('CLOUD_SCOPE_CONFLICT')
            remote = dict(remote, **content(remote))
            raw, streams = stream_facts(item, media_source)
            for key in keys:
                identity = [key, rule['cloud_scope_id'], remote.get('account_ref'), path, content(remote)]
                version_id = digest([service, str(library), *identity])
                assets = []
                with self.repository.connection() as db:
                    # Keep a pre-fix ID only when its relational AND observed scope
                    # agree. A legacy cross-scope collision cannot donate claims.
                    legacy = db.execute('SELECT service,library,data FROM archive_versions WHERE id=?', (digest(identity),)).fetchone()
                    if legacy and (legacy['service'], legacy['library']) == (service, str(library)):
                        old = json.loads(legacy['data'])
                        if (old['service'], old['library']) == (service, str(library)):
                            version_id = digest(identity)
                    previous = db.execute('SELECT data FROM archive_versions WHERE id=?', (version_id,)).fetchone()
                if version_id in ignored:
                    continue
                if previous:
                    for asset in json.loads(previous[0]).get('assets', []):
                        location = asset['location']
                        current = self.sources.cloud_stat(location['cloud_scope_id'], location['path'], refresh=True)
                        if self._location_key(current) != self._location_key(location):
                            raise ValueError('ASSET_SCOPE_CONFLICT')
                        if content(current) != content(location):
                            replacement = next((a for a in replacements if key in a.get('targets', []) and self._location_key(a['location']) == self._location_key(current) and content(a['location']) == content(current)), None)
                            if replacement is None:
                                raise ValueError('ASSET_CONTENT_CONFLICT')
                            asset = replacement
                        assets.append(dict(asset, location=current))
                self._subtitle_association(assets, streams, item_path)
                result.append({'version_id': version_id, 'target_key': key, 'service': service, 'library': str(library),
                               'item_id': str(item.get('Id')), 'source_id': str(media_source.get('Id')),
                               'item_path': item_path,
                               'mapping_revision': self.mappings.revision, 'mapping_id': rule['id'],
                               'strm': check, 'video': remote, 'raw': raw, 'streams': streams,
                               'identity': json.loads(key), 'reliable': True, 'observed_at': utcnow(),
                               'assets': assets, 'source_evidence': []})
        if len({(r['target_key'], r['version_id']) for r in result}) != len(result):
            raise ValueError('AMBIGUOUS_MEDIA_SOURCE')
        return result

    @staticmethod
    def _subtitle_association(assets, streams, item_path):
        external = {s['Path'] for s in streams if s.get('Type') == 'Subtitle' and s.get('IsExternal') is True and isinstance(s.get('Path'), str)}
        for asset in assets:
            if asset['role'] != 'subtitle':
                continue
            dest = PurePosixPath(asset['location']['path'])
            names = {dest.name}
            if dest.suffix.lower() == '.sub':
                names.add(dest.with_suffix('.idx').name)
            valid = {str(PurePosixPath(item_path).parent / name) for name in names} | {str(dest.parent / name) for name in names}
            if not external & valid:
                raise ValueError('SUBTITLE_ASSOCIATION_MISSING')

    def validate_observation(self, observation):
        if observation['mapping_revision'] != self.mappings.revision:
            raise ValueError('STALE_MAPPING')
        if (instant() - parse(observation['observed_at'])).total_seconds() > 900:
            raise ValueError('STALE_OBSERVATION')
        check = observation.get('strm')
        if check:
            _, current = read_strm(check['path'], check['root'], check['maximum'])
            if current != check:
                raise ValueError('STRM_CHANGED')

    def current(self, keys):
        if len(keys) > 10000:
            raise ValueError('TARGET_LIMIT')
        with self.repository.connection() as db:
            result = {}
            for key in keys:
                row = db.execute('SELECT * FROM archive_targets WHERE target_key=?', (key,)).fetchone()
                managed = db.execute('SELECT current_revision,current_facts FROM target_units WHERE target_key=?', (key,)).fetchone()
                data = json.loads(row['data']) if row else {}
                state = row['state'] if row else 'UNKNOWN'
                diagnostics = data.get('diagnostics', [])
                if data.get('mapping_revision') != self.mappings.revision:
                    state, diagnostics = 'UNKNOWN', ['STALE_MAPPING' if row else 'UNOBSERVED']
                if row and (instant() - parse(row['updated_at'])).total_seconds() > 900:
                    state, diagnostics = 'UNKNOWN', ['STALE_OBSERVATION']
                versions = [];sidecars=[]
                for v in self._live_versions(db, key):
                    observed = json.loads(v['data'])
                    sidecars.append(any(s.get('Type')=='Subtitle' and s.get('IsExternal') is True for s in observed.get('streams',[])))
                    versions.append(Version(v['id'], self.policy.normalize(observed['raw'], current=True), reliable=observed['reliable']))
                    if (instant() - parse(observed['observed_at'])).total_seconds() > 900:
                        state, diagnostics = 'UNKNOWN', ['STALE_OBSERVATION']
                if state == 'PRESENT' and not versions:
                    state, diagnostics = 'UNKNOWN', ['REPLACED_REQUIRED_ASSET']
                if managed and (not managed['current_facts'] or json.loads(managed['current_facts']).get('archive_revision') != (row['revision'] if row else None)):
                    state, diagnostics = 'UNKNOWN', ['CURRENT_BINDING_REQUIRED']
                result[key] = dict(state=state, revision=managed['current_revision'] if managed else None,
                                   archive_revision=row['revision'] if row else None, versions=versions,
                                   evidence_ref=data.get('evidence_ref'), diagnostics=diagnostics,
                                   sidecar_missing=state=='PRESENT' and bool(sidecars) and not any(sidecars))
            return result

    def candidate_evidence(self,candidate,scope):
        """Join recorded source hashes to fresh associated current assets only."""
        key=candidate['candidate_key'];table=candidate['torrent_files'];proven=[];versions=[]
        with self.repository.connection() as db:
            for row in db.execute("SELECT data FROM archive_sources WHERE json_extract(data,'$.candidate_key')=? AND json_extract(data,'$.infohash')=?",(key,candidate['infohash'])):
                source=json.loads(row[0]);proven+=source.get('source_assets',source.get('assets',[]))
            for row in db.execute("SELECT r.evidence,p.snapshot FROM action_receipts r JOIN plan_actions a ON a.id=r.action_id JOIN plans p ON p.id=a.plan_id WHERE r.outcome='SUCCEEDED' AND a.kind IN ('ORGANIZE','RAPID','CD2_UPLOAD') AND json_extract(p.snapshot,'$.candidate_key')=?",(key,)):
                snapshot=json.loads(row['snapshot'])
                if snapshot['infohash']==candidate['infohash'] and snapshot['torrent_files']==table:proven+=json.loads(row['evidence']).get('asset_manifest',{}).get('assets',[])
            for target in scope:
                versions += [json.loads(r['data']) for r in self._live_versions(db,target)]
            consumed={r[0] for r in db.execute('SELECT evidence_key FROM evidence_consumption')}
        assets={}
        for item in table:
            matching=[a for a in proven if a.get('file_index')==item['index'] and a.get('relative_path')==item['path'] and all(a.get(k)==item[k] for k in ('role','targets','requires')) and a.get('content',{}).get('size')==item['size']]
            hashes={encoded(a['content']) for a in matching}
            if len(hashes)==1:assets[item['index']]={k:matching[0][k] for k in ('file_index','relative_path','role','targets','requires','content')}
        current=self.current(scope);same_video={};same_assets=set();association=[];emby=[];used={}
        for observed in versions:
            target=observed['target_key']
            if current.get(target,{}).get('state')!='PRESENT':continue
            self.validate_observation(observed)
            videos=[f for f in table if f['role']=='video' and target in f['targets']]
            if len(videos)!=1 or videos[0]['index'] not in assets or assets[videos[0]['index']]['content']!=content(observed['video']):continue
            same_video[target]=True
            if candidate.get('local_assets'):
                missing=[a for a in candidate['local_assets'] if target in a['file']['targets'] and not any(old['role']=='subtitle' and old['content']==dict(sha1=a['sha1'],size=a['file']['size']) for old in observed.get('assets',[]))]
                if missing:current[target]['sidecar_missing']=True
                else:current[target]['sidecar_missing']=False
            bound=[f for f in table if target in f['targets'] and f['role'] in ('video','subtitle')]
            locations=[]
            for item in bound:
                asset=assets.get(item['index'])
                if not asset:break
                choices=[observed['video']] if item['role']=='video' else [a['location'] for a in observed.get('assets',[]) if a['role']=='subtitle' and a['content']==asset['content']]
                if len(choices)!=1:break
                loc=choices[0];locations.append(dict(file_index=item['index'],cloud_scope_id=loc['cloud_scope_id'],path=loc['path']))
            else:
                if not candidate.get('local_assets'):same_assets.add(observed['version_id'])
                association+=locations
                used.update({f['index']:assets[f['index']] for f in bound})
                emby.append(dict(service=observed['service'],library_id=observed['library'],item_id=observed['item_id']))
        manifest=None
        if used:
            with self.repository.connection() as db:
                self._candidate_asset_proof(db,key,candidate,list(used.values()))
                raw=json.loads(db.execute('SELECT data FROM candidates WHERE candidate_key=?',(key,)).fetchone()[0])
            manifest=dict(candidate_key=key,manifest_ref='source:'+digest([key,list(used.values())]),selected_indices=sorted(used),assets=list(used.values()),
                publication={k:dict(raw=raw,classification=candidate['classification']) for k in scope},
                association=dict(assets=list({encoded(a):a for a in association}.values()),emby=list({encoded(e):e for e in emby}.values())))
        return dict(current=current,same_video_verified=same_video,same_assets_verified=same_assets,consumed=consumed,manifest=manifest)

    def discovery_inventory(self, target):
        """Project indexed archive facts to a work/season without inventing episodes."""
        if not isinstance(target, Target):
            raise ValueError('TARGET_REQUIRED')
        if target.media_type == '电影':
            key = TargetUnit(target).key
            value = self.current([key])[key]
            return {name: value.get(name) for name in ('state', 'evidence_ref', 'diagnostics')}
        with self.repository.connection() as db:
            rows = db.execute(
                "SELECT target_key FROM archive_targets WHERE json_valid(target_key) "
                "AND json_extract(target_key,'$[0]')=? AND json_extract(target_key,'$[1]')=? "
                "AND json_extract(target_key,'$[2]')=? AND json_extract(target_key,'$[3]')=? "
                "AND json_extract(target_key,'$[4]')=? ORDER BY target_key LIMIT 10001",
                (target.media_type, target.media_source, target.media_id, target.season, target.episode_group),
            ).fetchall()
        keys = [row['target_key'] for row in rows]
        if not keys:
            return {'state': 'UNKNOWN', 'evidence_ref': None, 'diagnostics': ['UNOBSERVED_SEASON']}
        if len(keys) > 10000:
            return {'state': 'UNKNOWN', 'evidence_ref': None, 'diagnostics': ['SEASON_INVENTORY_LIMIT']}
        facts = self.current(keys)
        states = {value['state'] for value in facts.values()}
        evidence = 'archive-season:' + digest([[key, facts[key].get('evidence_ref')] for key in keys])
        if states == {'MISSING'}:
            return {'state': 'MISSING', 'evidence_ref': evidence, 'diagnostics': []}
        if 'PRESENT' not in states:
            diagnostics = sorted({code for value in facts.values() for code in value.get('diagnostics', [])})
            return {'state': 'UNKNOWN', 'evidence_ref': evidence,
                    'diagnostics': diagnostics or ['SEASON_INVENTORY_UNCERTAIN']}
        if states != {'PRESENT'}:
            return {'state': 'PARTIAL', 'evidence_ref': evidence,
                    'diagnostics': ['PARTIAL_SEASON_ARCHIVE']}
        with self.repository.connection() as db:
            lifecycle = db.execute(
                "SELECT l.scope,l.scope_closed FROM task_lifecycle l JOIN tasks t ON t.id=l.task_id "
                "WHERE t.target_key=?", (target.key,)).fetchone()
            receipts = {row[0] for row in db.execute(
                "SELECT DISTINCT target_key FROM ingest_receipts WHERE target_key IN (%s)" %
                ','.join('?' for _ in keys), keys)}
        if lifecycle and lifecycle['scope_closed'] and set(json.loads(lifecycle['scope'])) == set(keys) and receipts == set(keys):
            return {'state': 'INGESTED', 'evidence_ref': evidence, 'diagnostics': []}
        return {'state': 'PARTIAL', 'evidence_ref': evidence,
                'diagnostics': ['SEASON_COMPLETENESS_UNPROVEN']}

    @staticmethod
    def _live_versions(db, key):
        return db.execute("SELECT v.* FROM archive_versions v WHERE v.target_key=? AND v.active=1 AND NOT EXISTS(SELECT 1 FROM archive_assets a JOIN archive_locations l ON l.id=a.location_id WHERE a.version_id=v.id AND l.state!='PRESENT') ORDER BY v.id", (key,))

    @staticmethod
    def _location_key(location):
        return location['cloud_scope_id'], location.get('account_ref'), posix(location['path'])

    @staticmethod
    def _required_assets(previous, current, version):
        # file_index/requires belong to their source, not a global current file
        # table. Current requirements are the complete set of scoped locations.
        required = {}
        for asset in [*previous, *current]:
            actual = content(asset['location'])
            if 'content' in asset and content(asset['content']) != actual:
                raise ValueError('ASSET_CONTENT_CONFLICT')
            value = dict(asset, content=actual)
            value.setdefault('source_ref', 'archive:' + version)
            required[Archive._location_key(asset['location'])] = value
        return list(required.values())

    def _store_version(self, db, observed):
        key, version = observed['target_key'], observed['version_id']
        db.execute("INSERT OR IGNORE INTO archive_targets VALUES(?,'UNKNOWN','',?,?)", (key, '{}', utcnow()))
        observed = self._prepared_version(db, observed)
        db.execute('INSERT INTO archive_versions VALUES(?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET active=1,data=excluded.data',
                   (version, key, observed['service'], observed['library'], encoded(observed)))
        # These are the current required links; immutable archive_sources retains
        # each historical asset set when this same video acquires new sidecars.
        db.execute('DELETE FROM archive_assets WHERE version_id=?', (version,))
        affected = set()
        for asset in observed['assets']:
            loc = asset['location']
            c = content(loc)
            cid = digest(c)
            lid = digest([loc['cloud_scope_id'], loc.get('account_ref'), loc['path'], cid])
            db.execute('INSERT OR IGNORE INTO archive_contents VALUES(?,?,?)', (cid, c['sha1'], c['size']))
            old_locations = db.execute('SELECT id,data FROM archive_locations WHERE scope=? AND path=? AND content_id!=?', (loc['cloud_scope_id'], loc['path'], cid)).fetchall()
            for old in old_locations:
                if json.loads(old['data']).get('account_ref') != loc.get('account_ref'):
                    continue
                affected.update(r[0] for r in db.execute('SELECT DISTINCT v.target_key FROM archive_versions v JOIN archive_assets a ON a.version_id=v.id WHERE v.active=1 AND a.location_id=?', (old['id'],)))
                db.execute("UPDATE archive_locations SET state='REPLACED' WHERE id=?", (old['id'],))
                db.execute('UPDATE archive_versions SET active=0 WHERE id IN (SELECT version_id FROM archive_assets WHERE location_id=?)', (old['id'],))
            db.execute("INSERT INTO archive_locations VALUES(?,?,?,?,'PRESENT',?) ON CONFLICT(id) DO UPDATE SET state='PRESENT',data=excluded.data", (lid, cid, loc['cloud_scope_id'], loc['path'], encoded(loc)))
            db.execute('INSERT INTO archive_assets VALUES(?,?,?,?) ON CONFLICT(version_id,file_index,location_id) DO UPDATE SET data=excluded.data', (version, asset['file_index'], lid, encoded(asset)))
        source = {k: v for k, v in observed.items() if k != 'observed_at'}
        db.execute('INSERT OR IGNORE INTO archive_sources VALUES(?,?,?,?)', (digest(source), version, encoded(source), utcnow()))
        for other in affected - {key}:
            remaining = bool(self._live_versions(db, other).fetchone())
            self._sync(db, other, 'PRESENT' if remaining else 'UNKNOWN', 'replaced:' + version,
                       diagnostics=() if remaining else ('REPLACED_REQUIRED_ASSET',))

    def _prepared_version(self, db, observed):
        """Same merge for direct ingest and a scan's private finalization stage."""
        version = observed['version_id']
        prior = db.execute('SELECT data FROM archive_versions WHERE id=?', (version,)).fetchone()
        if prior:
            prior = json.loads(prior[0])
            # Claims belong to this exact associated version, never just its hash.
            observed = dict(observed, source_evidence=list(dict.fromkeys(prior.get('source_evidence', []) + observed.get('source_evidence', []))),
                            assets=self._required_assets(prior.get('assets', []), observed['assets'], version))
            if 'source_assets' not in observed:
                observed['source_assets'] = prior.get('source_assets', prior.get('assets', []))
            if prior.get('publication_raw') and not observed.get('publication_raw'):
                observed['publication_raw'] = prior['publication_raw']
                observed['raw'] = dict(prior['publication_raw'], technical=observed['raw']['technical'], chinese_pgs=observed['raw']['chinese_pgs'])
        return dict(observed, assets=self._required_assets([dict(file_index=-1, role='video', location=observed['video'])], observed['assets'], version))

    def _sync(self, db, key, state, evidence_ref, diagnostics=(), **extra):
        versions = [json.loads(r['data']) for r in self._live_versions(db, key)]
        if state == 'PRESENT' and not versions:
            state, diagnostics = 'UNKNOWN', ('REPLACED_REQUIRED_ASSET',)
        data = {'mapping_revision': self.mappings.revision, 'diagnostics': list(diagnostics), 'evidence_ref': evidence_ref, **extra}
        # Observed-at/watermark changes alone must not invalidate a frozen plan.
        stable = [{k: v for k, v in o.items() if k != 'observed_at'} for o in versions]
        revision = digest([state, self.mappings.revision, stable, list(diagnostics)])
        db.execute('INSERT INTO archive_targets VALUES(?,?,?,?,?) ON CONFLICT(target_key) DO UPDATE SET state=excluded.state,revision=excluded.revision,data=excluded.data,updated_at=excluded.updated_at', (key, state, revision, encoded(data), utcnow()))
        managed = db.execute('SELECT current_revision,publish_phase FROM target_units WHERE target_key=?', (key,)).fetchone()
        if managed:
            facts = dict(state=state, archive_revision=revision, evidence_ref='archive:' + revision,
                         versions=[{'version_id': o['version_id'], 'raw': o['raw'], 'reliable': o['reliable']} for o in versions])
            self.authority.update_current(key, facts, expected_revision=managed['current_revision'], db=db)

    def _verify_assets(self, manifest, table, selected, consumer):
        from .planner import validate_files
        files = validate_files(table, selected)
        assets = manifest.get('assets')
        locations = consumer.get('assets')
        if not isinstance(assets, list) or not isinstance(locations, list):
            raise ValueError('ASSETS_INCOMPLETE')
        if len({a.get('file_index') for a in assets}) != len(assets) or {a.get('file_index') for a in assets} != set(selected):
            raise ValueError('ASSETS_INCOMPLETE')
        if len({a.get('file_index') for a in locations}) != len(locations) or {a.get('file_index') for a in locations} != set(selected):
            raise ValueError('ASSET_LOCATIONS_INCOMPLETE')
        paths = {a['file_index']: a for a in locations}
        verified = {}
        for asset in assets:
            index = asset['file_index']
            frozen = files[index]
            if (asset.get('relative_path'), asset.get('role'), asset.get('targets'), asset.get('requires')) != (frozen['path'], frozen['role'], frozen['targets'], frozen['requires']):
                raise ValueError('ASSET_PLAN_CONFLICT')
            expected = content(asset.get('content', {}))
            if expected['size'] != frozen['size']:
                raise ValueError('ASSET_SIZE_CONFLICT')
            location = paths[index]
            rule_scopes = {r['cloud_scope_id'] for r in self.mappings.rules if within(posix(location['path']), r['cd2_prefix'])}
            if location['cloud_scope_id'] not in rule_scopes:
                raise ValueError('CLOUD_PATH_UNAUTHORIZED')
            actual = self.sources.cloud_stat(location['cloud_scope_id'], location['path'], refresh=True)
            if (actual.get('cloud_scope_id'), actual.get('path')) != (location['cloud_scope_id'], location['path']) or content(actual) != expected:
                raise ValueError('ASSET_CONTENT_CONFLICT')
            verified[index] = dict(asset, location=dict(actual, **content(actual)))
        if len({(v['location']['cloud_scope_id'], v['location']['path']) for v in verified.values()}) != len(verified):
            raise ValueError('ASSET_LOCATION_COLLISION')
        return verified

    def _verify_final(self, manifest, table, selected, consumer, target_keys):
        verified = self._verify_assets(manifest, table, selected, consumer)
        source_ref = digest([manifest.get('plan_id'), manifest.get('candidate_key'), manifest['manifest_ref']])
        verified = {i: dict(a, source_ref=source_ref) for i, a in verified.items()}
        refs = consumer.get('emby')
        if not isinstance(refs, list) or not 1 <= len(refs) <= 1000:
            raise ValueError('EMBY_ASSOCIATION_REQUIRED')
        observations = []
        for ref in refs:
            service, library = ref['service'], str(ref['library_id'])
            self.mappings.scoped(service, library)
            item = self.sources.emby_item(service, library, ref['item_id'])
            series = {}
            if item.get('Type') == 'Episode':
                sid = str(item.get('SeriesId', ''))
                series[sid] = self.sources.emby_item(service, library, sid)
            grouped={encoded(json.loads(k)[:5]) for k in target_keys if json.loads(k)[4]}
            if grouped:
                if not callable(self.scope_provider):raise ValueError('GROUP_SCOPE_REQUIRED')
                for target in grouped:
                    scope=self.scope_provider(Target(*json.loads(target)))
                    observations.extend(self.resolve_item(service,library,item,series=series,replacements=verified.values(),scope=scope))
            else:observations.extend(self.resolve_item(service, library, item, series=series, replacements=verified.values()))
        result = []
        for key in target_keys:
            videos = [a for a in verified.values() if a['role'] == 'video' and key in a['targets']]
            if not videos:
                # Sidecar-only plans bind against the already verified current video.
                videos = [dict(location=o['video']) for o in observations if o['target_key'] == key]
            if not videos:
                raise ValueError('VIDEO_ASSOCIATION_MISSING')
            for video in videos:
                matches = [o for o in observations if o['target_key'] == key and
                           (o['video']['cloud_scope_id'], o['video']['path'], content(o['video'])) ==
                           (video['location']['cloud_scope_id'], video['location']['path'], content(video['location']))]
                if len(matches) != 1:
                    raise ValueError('VIDEO_ASSOCIATION_AMBIGUOUS')
                observed = matches[0]
                related = [a for a in verified.values() if key in a['targets']]
                parent = str(PurePosixPath(observed['video']['path']).parent)
                if any(a['location']['cloud_scope_id'] != observed['video']['cloud_scope_id'] or
                       not within(a['location']['path'], parent) for a in related):
                    raise ValueError('ASSET_ASSOCIATION_CONFLICT')
                required = self._required_assets(observed['assets'], related, observed['version_id'])
                self._subtitle_association(required, observed['streams'], observed['item_path'])
                publication = manifest.get('publication', {}).get(key)
                if not isinstance(publication, dict) or not isinstance(publication.get('raw'), dict):
                    raise ValueError('PUBLICATION_EVIDENCE_MISSING')
                classification = self.sources.classify_target(key)
                if classification != publication.get('classification'):
                    raise ValueError('CLASSIFICATION_CHANGED')
                raw = dict(publication['raw'], technical=observed['raw']['technical'], chinese_pgs=observed['raw']['chinese_pgs'])
                observed.update(raw=raw, publication_raw=publication['raw'], classification=classification,
                                assets=required, source_assets=related, source_evidence=[manifest['manifest_ref']])
                result.append(observed)
        return result

    def _publication_baseline(self, action, baseline, observed):
        """Fresh scoped snapshots are comparison evidence until Authority commits.

        The frozen pre-ingest versions remain historical comparison facts. They
        are never relabelled fresh or written back as still-present versions.
        """
        keys = sorted(baseline)
        published = {o['version_id'] for o in observed}
        replacements = [a for o in observed for a in o['assets']]
        replacement_paths = {(a['location']['cloud_scope_id'], a['location'].get('account_ref'), a['location']['path']): content(a['location']) for a in replacements}
        ignored = []
        with self.repository.connection() as db:
            for key in keys:
                row = db.execute('SELECT * FROM archive_targets WHERE target_key=?', (key,)).fetchone()
                managed = db.execute('SELECT current_facts FROM target_units WHERE target_key=?', (key,)).fetchone()
                if not row or row['state'] not in ('PRESENT', 'MISSING', 'INVALID') or json.loads(row['data']).get('mapping_revision') != self.mappings.revision or not managed or json.loads(managed[0] or '{}').get('archive_revision') != row['revision']:
                    raise ValueError('CURRENT_UNCONFIRMED')
                baseline[key] = dict(baseline[key], state=row['state'])
                for old in db.execute('SELECT id,data FROM archive_versions WHERE target_key=? AND active=1', (key,)):
                    value = json.loads(old['data'])
                    locations = [a['location'] for a in value['assets']] or [value['video']]
                    if old['id'] not in published and any((loc['cloud_scope_id'], loc.get('account_ref'), loc['path']) in replacement_paths and replacement_paths[loc['cloud_scope_id'], loc.get('account_ref'), loc['path']] != content(loc) for loc in locations):
                        ignored.append(old['id'])
        fresh = []
        identities={tuple(json.loads(key)[:5]) for key in keys}
        scope=None
        if self.scope_provider:
            if len(identities)!=1:raise ValueError('PUBLICATION_SCOPE_AMBIGUOUS')
            target=Target(*next(iter(identities)));scope=self.scope_provider(target)
            if not set(keys)<=set(scope['units']):raise ValueError('PUBLICATION_SCOPE_CHANGED')
            scopes=sorted({(r['emby_service'],r['library_id']) for r in self.mappings.rules
                if r.get('media_source','themoviedb')==target.media_source and r.get('episode_group','')==target.episode_group})
        else:scopes = sorted({(r['emby_service'], r['library_id']) for r in self.mappings.rules})
        # Keep a positive association seen in either pass. Only two complete
        # independent snapshots may retire an absent old independent copy.
        for service, library, absence_pass in [(s, l, p) for s, l in scopes for p in (1, 2)]:
            publication = dict(id=action['id'], ignored=sorted(ignored), replacements=replacements, absence_pass=absence_pass)
            scan_id = None
            with self.repository.connection() as db:
                for scan in db.execute("SELECT * FROM archive_scans WHERE service=? AND library=? AND state IN ('INCOMPLETE','COMPLETE') AND json_extract(data,'$.publication.id')=? ORDER BY rowid DESC", (service, library, action['id'])):
                    data = json.loads(scan['data'])
                    if data['mapping'] == self.mappings.revision and data['targets'] == keys and data.get('publication') == publication and (instant() - parse(data['started_at'])).total_seconds() <= 900:
                        scan_id = scan['id']
                        break
            result = self.reconcile(service, library, target_keys=keys, scan_id=scan_id,
                                    limits=dict(page_size=100, pages=2, items=1000), _publication=publication,scope=scope)
            if result['status'] != 'COMPLETE':
                raise ValueError('BASELINE_REFRESH_' + (result['diagnostics'][0] if result['diagnostics'] else result['status']))
            with self.repository.connection() as db:
                fresh.extend(o for r in db.execute('SELECT resolved FROM archive_scan_items WHERE scan_id=?', (result['scan_id'],)) for o in json.loads(r[0]))
        fresh = list({o['version_id']: o for o in fresh}.values())
        if not published <= {o['version_id'] for o in fresh}:
            raise ValueError('BASELINE_REFRESH_PUBLICATION_MISSING')
        for o in fresh:
            self.validate_observation(o)
            if o['version_id'] not in published:
                # Both the pre-ingest comparison and newer independent copy
                # constrain admission; fresh metadata cannot erase old evidence.
                baseline[o['target_key']]['versions'].append(Version(o['version_id'], self.policy.normalize(o['raw'], current=True), reliable=o['reliable']))
        return baseline, fresh

    def confirm_ingest(self, action_id, manifest, consumer_receipt, *, now=None):
        manifest = json.loads(encoded(manifest))
        consumer = json.loads(encoded(consumer_receipt))
        with self.repository.connection() as db:
            action = db.execute("SELECT * FROM plan_actions WHERE id=? AND kind='PUBLISH'", (action_id,)).fetchone()
            if not action or manifest.get('plan_id') != action['plan_id']:
                raise ValueError('PUBLICATION_MISSING')
            action = dict(action)
            unknown = action['state'] == 'PUBLISH_OUTCOME_UNKNOWN'
            if unknown:
                row = db.execute('SELECT data FROM delivery_bundles WHERE id=? AND plan_id=?', (manifest.get('manifest_ref'), action['plan_id'])).fetchone()
                bound = json.loads(row[0]) if row else {}
                if bound.get('publication_action') != action_id or bound.get('manifest') != manifest:
                    raise ValueError('PUBLICATION_MANIFEST_UNBOUND')
            # UNKNOWN evidence is only a claim until the full independent verifier
            # and authority fence succeed in the final write transaction.
            receipts = [json.loads(r[0]) for r in db.execute("SELECT evidence FROM action_receipts WHERE action_id=? AND outcome=?", (action_id, 'UNKNOWN' if unknown else 'HANDED_OFF'))]
            if not any(r.get('asset_manifest') == manifest and r.get('consumer_receipt') == consumer for r in receipts):
                raise ValueError('DURABLE_CONSUMER_RECEIPT_REQUIRED')
            if consumer.get('action_id') != action_id or consumer.get('settled') is not True or not consumer.get('evidence_ref') or not manifest.get('manifest_ref'):
                raise ValueError('CONSUMER_UNSETTLED')
            if action['state'] == 'INGEST_CONFIRMED':
                confirmations = {r['target_key']: json.loads(r['evidence']) for r in db.execute('SELECT * FROM ingest_receipts WHERE plan_id=?', (action['plan_id'],)) if r['target_key'] in json.loads(action['targets'])}
                return self.authority.confirm_ingest(action_id, confirmations, now=now)
        if action['state'] != 'HANDED_OFF' and not unknown:
            raise ValueError('PUBLICATION_NOT_HANDED_OFF')
        plan = self.authority.plan(action['plan_id'])
        vector = json.loads(action['targets'])
        baseline = self.current(list(vector))
        expected = self.authority.vector(list(vector))
        selected = json.loads(action['files'])
        from .execution import Exclusions
        exclusions = Exclusions(self.repository)
        exclusion_token = exclusions.token()
        observed = self._verify_final(manifest, asset_table(plan['snapshot']), selected, consumer, vector)
        for o in observed:
            o.update(candidate_key=plan['snapshot']['candidate_key'], infohash=plan['snapshot']['infohash'])
        confirmations = {}
        for key in vector:
            versions = [o for o in observed if o['target_key'] == key]
            confirmations[key] = dict(receipt_id=digest([action_id, key]), version_id=versions[0]['version_id'] if len(versions) == 1 else digest(sorted(o['version_id'] for o in versions)),
                                      association_verified=True, all_assets_verified=True, improvement_verified=False,
                                      consumer_settled=True, evidence_ref=digest([manifest, consumer, key]))
        with self.repository.connection() as db:
            try:
                self.authority._match(db, vector, owner=plan['id'], allow_barrier=True)
                self.authority._task_active(db, plan)
                stale = False
            except ValueError:
                stale = True
        if stale:
            if unknown:
                return dict(accepted=False, reason='STALE_AUTHORITY')
            # Shared Authority rechecks the fence in its write transaction. If
            # authority changed again, the missing improvement proof fails closed.
            return self.authority.confirm_ingest(action_id, confirmations, now=now)
        if any(baseline[k]['revision'] != expected[k]['current_revision'] for k in vector):
            raise ValueError('STALE_CURRENT')
        refreshed = None
        if any(b['state'] == 'UNKNOWN' and b['diagnostics'] == ['STALE_OBSERVATION'] for b in baseline.values()):
            baseline, refreshed = self._publication_baseline(action, baseline, observed)
        for key in vector:
            before = baseline[key]
            if before['state'] not in ('PRESENT', 'MISSING', 'INVALID'):
                raise ValueError('CURRENT_UNCONFIRMED')
            versions = [o for o in observed if o['target_key'] == key]
            target = plan['snapshot']['targets'][key]
            for o in versions:
                candidate = self.policy.normalize(o['raw'])
                if exclusions.matches(dict(candidate_key=plan['snapshot']['candidate_key'], infohash=plan['snapshot']['infohash'], targets=[key], content_sha1=[a['content']['sha1'] for a in o['assets']]), facts=dict(candidate.raw)):
                    raise ValueError('EXCLUDED')
                decision = self.policy.compare(candidate, before['versions'], o['classification'], identity_ok=True, scope_ok=True)
                accompanying = target['action'] in ('UNCHANGED', 'SIDECAR_SUPPLEMENT') and decision.reason == 'EQUIVALENT'
                if decision.status != 'ALLOW' and not accompanying:
                    raise ValueError('CURRENT_POLICY_' + decision.reason)
                if len(decision.rank) != len(target['quality']) or any(v is None for v in decision.rank) or tuple(decision.rank) < tuple(target['quality']):
                    raise ValueError('PLANNED_QUALITY_CONFLICT')
                if target['action'] == 'SIDECAR_SUPPLEMENT' and o['version_id'] not in {v.version_id for v in before['versions']}:
                    raise ValueError('SIDECAR_VIDEO_CHANGED')
            confirmations[key]['improvement_verified'] = True
        with self.repository.connection(write=True) as db:
            actual = self.authority._vector(db, vector)
            if any(actual[k]['current_revision'] != expected[k]['current_revision'] for k in vector):
                raise ValueError('STALE_CURRENT')
            self.authority._revisions(db, plan['snapshot'])
            if digest([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]) != exclusion_token:
                raise ValueError('EXCLUSIONS_CHANGED')
            for o in (refreshed or []) + observed:
                self.validate_observation(o)
            if unknown:
                self.authority._match(db, vector, owner=plan['id'], allow_barrier=True)
                self.authority._task_active(db, plan)
                self.authority.record_result(action_id, 'HANDED_OFF', dict(asset_manifest=manifest, consumer_receipt=consumer), now=now, db=db)
            result = self.authority.confirm_ingest(action_id, confirmations, now=now, db=db)
            if result['accepted']:
                if refreshed is not None:
                    for key in vector:
                        db.execute('UPDATE archive_versions SET active=0 WHERE target_key=?', (key,))
                    published = {o['version_id'] for o in observed}
                    for o in refreshed:
                        if o['version_id'] not in published:
                            self._store_version(db, o)
                for o in observed:
                    self._store_version(db, o)
                for key in vector:
                    self._sync(db, key, 'PRESENT', confirmations[key]['evidence_ref'])
                result['current_revisions'] = {k: self.authority._vector(db, [k])[k]['current_revision'] for k in vector}
            return result

    @staticmethod
    def _candidate_asset_proof(db, candidate_key, candidate, assets):
        fields = ('file_index', 'relative_path', 'role', 'targets', 'requires', 'content')
        wanted = {digest({k: a[k] for k in fields}) for a in assets}
        covered, references = set(), []
        rows = db.execute("SELECT r.id,r.evidence,a.files,p.snapshot FROM action_receipts r JOIN plan_actions a ON a.id=r.action_id JOIN plans p ON p.id=a.plan_id WHERE a.kind IN ('ORGANIZE','RAPID','CD2_UPLOAD') AND r.outcome='SUCCEEDED' AND json_extract(p.snapshot,'$.candidate_key')=?", (candidate_key,))
        for row in rows:
            plan = json.loads(row['snapshot'])
            if plan['infohash'] != candidate.get('infohash') or plan['torrent_files'] != candidate.get('torrent_files'):
                continue
            indices = json.loads(row['files'])
            proven = json.loads(row['evidence']).get('asset_manifest', {}).get('assets', [])
            matched = {digest({k: a[k] for k in fields}) for a in proven if all(k in a for k in fields) and a['file_index'] in indices} & wanted
            if matched:
                covered.update(matched)
                references.append('action-receipt:' + str(row['id']))
        for row in db.execute("SELECT id,data FROM archive_sources WHERE json_extract(data,'$.candidate_key')=? AND json_extract(data,'$.infohash')=?", (candidate_key, candidate.get('infohash'))):
            source = json.loads(row['data'])
            proven = source.get('source_assets', source.get('assets', []))
            matched = {digest({k: a[k] for k in fields}) for a in proven if all(k in a for k in fields)} & wanted
            if matched:
                covered.update(matched)
                references.append('archive-source:' + row['id'])
        if not wanted or covered != wanted:
            raise ValueError('SOURCE_ASSETS_UNVERIFIED')
        return sorted(references)

    def enrich_evidence(self, opportunity_id, candidate_key, enrichments, manifest, *, now=None):
        if not isinstance(enrichments, list) or not enrichments or len({e['target_key'] for e in enrichments}) != len(enrichments):
            raise ValueError('EXACT_ENRICHMENT_REQUIRED')
        keys = [e['target_key'] for e in enrichments]
        manifest = json.loads(encoded(manifest))
        request_digest = digest([opportunity_id, candidate_key, enrichments, manifest])
        with self.repository.connection() as db:
            prior = [db.execute('SELECT evidence FROM ingest_receipts WHERE id=?', (digest([request_digest, k]),)).fetchone() for k in keys]
            if all(prior):
                return {'accepted': False, 'duplicate': True, 'reason': 'ALREADY_CONFIRMED'}
            row = db.execute('SELECT data FROM candidates WHERE candidate_key=?', (candidate_key,)).fetchone()
            opportunity = db.execute('SELECT * FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if not row or not opportunity:
                raise ValueError('EVIDENCE_SOURCE_MISSING')
            candidate_text = row[0]
            candidate = json.loads(candidate_text)
            source_proof = self._candidate_asset_proof(db, candidate_key, candidate, manifest.get('assets', []))
            task = db.execute('SELECT * FROM tasks WHERE id=?', (opportunity['task_id'],)).fetchone()
            revisions = db.execute("SELECT value FROM settings WHERE key='planner_revisions'").fetchone()
            consumed = {r[0] for r in db.execute('SELECT evidence_key FROM evidence_consumption')}
        if not revisions or json.loads(revisions[0])[0] != self.policy.semantic_hash:
            raise ValueError('STALE_POLICY')
        expected = dict(task_generation=task['generation'], policy_revision=self.policy.semantic_hash,
                        parse_revision=json.loads(revisions[0])[1], targets=self.authority.vector(keys))
        if any(e['current_revision'] != expected['targets'][e['target_key']]['current_revision'] or e['policy_revision'] != self.policy.semantic_hash for e in enrichments):
            raise ValueError('STALE_CURRENT')
        if manifest.get('candidate_key') != candidate_key or not manifest.get('manifest_ref'):
            raise ValueError('EVIDENCE_SOURCE_CONFLICT')
        from .execution import Exclusions
        exclusions = Exclusions(self.repository)
        exclusion_token = exclusions.token()
        for key in keys:
            identity = json.loads(key)
            recognition = candidate.get('recognition', {})
            if recognition.get('status') != 'OK' or recognition.get('identity') != identity[1:3]:
                raise ValueError('IDENTITY_CONFLICT')
            raw = manifest.get('publication', {}).get(key, {}).get('raw', {})
            if any(raw.get(k) != candidate.get(k) for k in ('title', 'description', 'labels')):
                raise ValueError('PUBLICATION_EVIDENCE_CONFLICT')
        observed = self._verify_final(manifest, candidate['torrent_files'], manifest['selected_indices'], manifest['association'], keys)
        for o in observed:
            o.update(candidate_key=candidate_key, infohash=candidate['infohash'], source_evidence=source_proof)
        before = self.current(keys)
        confirmations = {}
        for enrichment in enrichments:
            key = enrichment['target_key']
            if before[key]['state'] != 'PRESENT':
                raise ValueError('CURRENT_UNCONFIRMED')
            associated = [o for o in observed if o['target_key'] == key]
            actual_versions = {o['version_id'] for o in associated}
            if not actual_versions <= {v.version_id for v in before[key]['versions']}:
                raise ValueError('SAME_VIDEO_UNCONFIRMED')
            for o in associated:
                facts = self.policy.normalize(o['raw'])
                if exclusions.matches(dict(candidate_key=candidate_key,targets=[key],content_sha1=[a['content']['sha1'] for a in o['assets']]), facts=dict(facts.raw)):
                    raise ValueError('EXCLUDED')
                decision = self.policy.compare(facts, before[key]['versions'], o['classification'], consumed=consumed,
                                               same_assets_verified=actual_versions, identity_ok=True, scope_ok=True)
                if decision.action != 'ENRICH_EVIDENCE' or set(decision.evidence_keys) != set(enrichment['evidence_keys']):
                    raise ValueError('EVIDENCE_POLICY_CHANGED')
            confirmations[key] = dict(receipt_id=digest([request_digest, key]), version_id=associated[0]['version_id'],
                                      opportunity_id=opportunity_id, evidence_keys=enrichment['evidence_keys'],
                                      association_verified=True, all_assets_verified=True, improvement_verified=True, evidence_ref=request_digest)
        with self.repository.connection(write=True) as db:
            fresh = db.execute('SELECT data FROM candidates WHERE candidate_key=?', (candidate_key,)).fetchone()
            if not fresh or fresh[0] != candidate_text:
                raise ValueError('EVIDENCE_SOURCE_CHANGED')
            if self._candidate_asset_proof(db, candidate_key, candidate, manifest['assets']) != source_proof:
                raise ValueError('EVIDENCE_SOURCE_CHANGED')
            if digest([tuple(r) for r in db.execute('SELECT * FROM exclusions ORDER BY id')]) != exclusion_token:
                raise ValueError('EXCLUSIONS_CHANGED')
            for o in observed:
                self.validate_observation(o)
            result = self.authority.confirm_evidence(opportunity_id, confirmations, expected=expected, now=now, db=db)
            if result['accepted']:
                for o in observed:
                    self._store_version(db, o)
                for key in keys:
                    self._sync(db, key, 'PRESENT', request_digest)
                result['current_revisions'] = {k: self.authority._vector(db, [k])[k]['current_revision'] for k in keys}
            return result

    def reconcile(self, service, library, *, target_keys=None, limits=None, scan_id=None, _publication=None, scope=None,deadline=None):
        limits = {**dict(page_size=100, pages=10, items=100), **(limits or {})}
        if any(type(v) is not int or v < 1 or v > 1000 for v in limits.values()) or set(limits) != {'page_size', 'pages', 'items'}:
            raise ValueError('INVALID_SCAN_LIMITS')
        library = str(library)
        self.mappings.scoped(service, library)
        keys = sorted(set(target_keys)) if target_keys is not None else None
        target=Target(*json.loads(scope['target_key'])) if scope else None
        if scope and (keys is None or not set(keys)<=set(scope['units']) or len(keys)>1000):raise ValueError('INVALID_SCAN_SCOPE')
        with self.repository.connection(write=True) as db:
            row = db.execute('SELECT * FROM archive_scans WHERE id=?', (scan_id,)).fetchone() if scan_id else None
            if scan_id and not row:
                raise ValueError('SCAN_UNKNOWN')
            if row:
                scan = json.loads(row['data'])
                if (row['service'], row['library'], scan['targets'], scan['mapping'], scan.get('publication'),scan.get('scope')) != (service, library, keys, self.mappings.revision, _publication,scope):
                    raise ValueError('STALE_SCAN')
                if row['state'] in ('COMPLETE', 'ERROR'):
                    return dict(status=row['state'], scan_id=scan_id, **scan)
            else:
                scan_id = uuid4().hex
                scan = dict(targets=keys, mapping=self.mappings.revision, start=0, total=None, phase='COLLECT', started_at=utcnow(), diagnostics=[])
                if _publication is not None:
                    scan['publication'] = _publication
                if scope is not None:scan['scope']=scope
                db.execute("INSERT INTO archive_scans VALUES(?,?,?,'INCOMPLETE',?)", (scan_id, service, library, encoded(scan)))
                db.execute('INSERT INTO archive_scan_baselines SELECT ?,u.target_key,u.current_revision,u.generation,t.generation FROM target_units u JOIN tasks t ON t.id=u.task_id WHERE ? IS NULL OR u.target_key IN (SELECT value FROM json_each(?))',
                    (scan_id,encoded(keys) if keys is not None else None,encoded(keys or [])))
        try:
            self.mappings.mounts(service, library)
            if (instant() - parse(scan['started_at'])).total_seconds() > 86400:
                raise ValueError('SCAN_EXPIRED')
            for _ in range(limits['pages']):
                if deadline is not None and time.monotonic()>=deadline:break
                if scan['phase'] != 'COLLECT':
                    break
                page = self.sources.emby_target_page(service,library,target,scan['start'],limits['page_size']) if target else self.sources.emby_page(service, library, scan['start'], limits['page_size'])
                items, total = page.get('Items'), page.get('TotalRecordCount')
                if not isinstance(items, list) or type(total) is not int or total < 0 or len(items) > limits['page_size']:
                    raise ValueError('EMBY_INVALID_PAGE')
                if scan['total'] is not None and total != scan['total']:
                    raise ValueError('SCAN_TOTAL_CHANGED')
                if scan['start'] + len(items) > total or (not items and scan['start'] < total):
                    raise ValueError('SCAN_INCOMPLETE')
                scan['total'] = total
                with self.repository.connection(write=True) as db:
                    if page.get('Series'):
                        parent=item_projection(page['Series'])
                        prior=db.execute('SELECT data FROM archive_scan_items WHERE scan_id=? AND item_id=?',(scan_id,parent['Id'])).fetchone()
                        if prior and json.loads(prior[0])!=parent:raise ValueError('SERIES_IDENTITY_CHANGED')
                        db.execute('INSERT OR IGNORE INTO archive_scan_items VALUES(?,?,?,?)',(scan_id,parent['Id'],encoded(parent),'[]'))
                    for item in items:
                        if not isinstance(item.get('Id'), str) or item.get('Type') not in ('Movie', 'Episode', 'Series'):
                            raise ValueError('EMBY_INVALID_ITEM')
                        if db.execute('SELECT 1 FROM archive_scan_items WHERE scan_id=? AND item_id=?', (scan_id, item['Id'])).fetchone():
                            raise ValueError('SCAN_DUPLICATE_ITEM')
                        db.execute('INSERT INTO archive_scan_items VALUES(?,?,?,NULL)', (scan_id, item['Id'], encoded(item_projection(item))))
                    scan['start'] += len(items)
                    if scan['start'] == total:
                        scan['phase'] = 'RESOLVE'
                    db.execute('UPDATE archive_scans SET data=? WHERE id=?', (encoded(scan), scan_id))
            if scan['phase'] == 'RESOLVE':
                with self.repository.connection() as db:
                    rows = [dict(r) for r in db.execute('SELECT * FROM archive_scan_items WHERE scan_id=? AND resolved IS NULL ORDER BY item_id LIMIT ?', (scan_id, limits['items']))]
                    parents={json.loads(r['data']).get('SeriesId') for r in rows}-{None}
                    series={r['item_id']:json.loads(r['data']) for parent in parents for r in db.execute('SELECT item_id,data FROM archive_scan_items WHERE scan_id=? AND item_id=?',(scan_id,parent))}
                resolved=[]
                for row in rows:
                    if deadline is not None and time.monotonic()>=deadline:break
                    item = json.loads(row['data'])
                    observed = []
                    if item['Type'] != 'Series':
                        identity = units(item, self.mappings.scoped(service, library), series,scope=scope)
                        if keys is None or set(identity) & set(keys):
                            observed = self.resolve_item(service, library, item, series=series,
                                                         replacements=(_publication or {}).get('replacements', ()),
                                                         ignored=(_publication or {}).get('ignored', ()),scope=scope)
                            observed = [o for o in observed if keys is None or o['target_key'] in keys]
                    resolved.append((encoded(observed),scan_id,row['item_id']))
                with self.repository.connection(write=True) as db:
                    db.executemany('UPDATE archive_scan_items SET resolved=? WHERE scan_id=? AND item_id=?',resolved)
                with self.repository.connection() as db:
                    pending = db.execute('SELECT 1 FROM archive_scan_items WHERE scan_id=? AND resolved IS NULL', (scan_id,)).fetchone()
                if not pending:
                    scan.update(phase='FINALIZE',finalize_after='')
                    with self.repository.connection(write=True) as db:
                        db.execute('UPDATE archive_scans SET data=? WHERE id=?',(encoded(scan),scan_id))
            if scan['phase']=='FINALIZE':
                return self._finish_scan(service, library, scan_id, scan, limits['items'],deadline=deadline)
            return dict(status='INCOMPLETE', scan_id=scan_id, **scan)
        except (ValueError, OSError) as error:
            code = str(error) if re.fullmatch('[A-Z_0-9]{1,80}', str(error)) else 'SCAN_FAILED'
            scan['diagnostics'] = [code]
            with self.repository.connection(write=True) as db:
                db.execute("UPDATE archive_scans SET state='ERROR',data=? WHERE id=?", (encoded(scan), scan_id))
                # Keep all old versions. A failed watermark cannot replace facts;
                # expose its uncertainty without materializing the whole library.
                if _publication is None and code not in ('STALE_SCAN','SCAN_SUPERSEDED','SCAN_AUTHORITY_CHANGED','SHARED_ASSET_PUBLISH_BARRIER'):
                    db.create_function('error_revision',1,lambda previous:digest(['ERROR',previous,code,scan_id]))
                    db.execute("UPDATE archive_targets SET state='ERROR',revision=error_revision(revision),data=json_set(data,'$.diagnostics',json(?),'$.evidence_ref',?),updated_at=? WHERE updated_at<=? AND target_key IN (SELECT value FROM json_each(?) UNION SELECT target_key FROM archive_versions WHERE ? IS NULL AND service=? AND library=?) AND NOT EXISTS(SELECT 1 FROM target_units u WHERE u.target_key=archive_targets.target_key AND u.publish_phase IN (SELECT value FROM json_each(?))) AND NOT EXISTS(SELECT 1 FROM target_units u JOIN tasks t ON t.id=u.task_id LEFT JOIN archive_scan_baselines b ON b.scan_id=? AND b.target_key=u.target_key WHERE u.target_key=archive_targets.target_key AND (b.target_key IS NULL OR b.current_revision!=u.current_revision OR b.unit_generation!=u.generation OR b.task_generation!=t.generation))",
                        (encoded([code]),scan_id,utcnow(),scan['started_at'],encoded(keys or []),encoded(keys) if keys is not None else None,service,library,encoded(sorted(BARRIERS)),scan_id))
                    db.execute("UPDATE target_units SET current_facts=json_set(COALESCE(current_facts,'{}'),'$.state','ERROR','$.archive_revision',a.revision,'$.evidence_ref',?),current_revision=current_revision+1 FROM archive_targets a WHERE target_units.target_key=a.target_key AND json_extract(a.data,'$.evidence_ref')=? AND a.state='ERROR'",('archive-scan:'+scan_id,scan_id))
            return dict(status='ERROR', scan_id=scan_id, **scan)

    def _finish_scan(self, service, library, scan_id, scan, limit,*,deadline=None):
        with self.repository.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT item_id,resolved FROM archive_scan_items WHERE scan_id=? AND item_id>? ORDER BY item_id LIMIT ?', (scan_id,scan['finalize_after'],limit))]
        prepared=[]
        for row in rows:
            if deadline is not None and time.monotonic()>=deadline:break
            observed=json.loads(row['resolved'])
            for item in observed:
                if item.get('strm'):
                    _,check=read_strm(item['strm']['path'],item['strm']['root'],item['strm']['maximum'])
                    if check!=item['strm']:raise ValueError('STRM_CHANGED')
            with self.repository.connection() as db:
                prepared.append((encoded([self._prepared_version(db,item) for item in observed]),scan_id,row['item_id']))
        if prepared:
            with self.repository.connection(write=True) as db:
                scan['finalize_after']=prepared[-1][2]
                db.executemany('UPDATE archive_scan_items SET resolved=? WHERE scan_id=? AND item_id=?',prepared)
                db.execute('UPDATE archive_scans SET data=? WHERE id=?',(encoded(scan),scan_id))
        with self.repository.connection() as db:
            pending=db.execute('SELECT 1 FROM archive_scan_items WHERE scan_id=? AND item_id>? LIMIT 1',(scan_id,scan['finalize_after'])).fetchone()
        if pending:return dict(status='INCOMPLETE',scan_id=scan_id,finalize_processed=len(prepared),**scan)
        self.mappings.mounts(service, library)
        from .archive_scan import publish
        before=time.monotonic()
        publish(self,service,library,scan_id,scan)
        return dict(status='COMPLETE', scan_id=scan_id,finalize_processed=len(prepared),final_sql_seconds=time.monotonic()-before, **scan)
