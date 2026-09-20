"""Bounded local observations of host sidecars; no asynchronous completion guess."""
from pathlib import Path, PurePosixPath
import os
from .execution import safe_local, asset_hashes
from .planner import TEXT_SUBTITLE_SUFFIXES, encoded
from .scheduler import instant, parse
from .archive import digest


def observe(pipeline,plan,*,seconds=10,entries=1000):
    s=plan['snapshot'];root=Path(s['save_path']).absolute();repo=pipeline.service.repository
    videos=[f for f in s['torrent_files'] if f['role']=='video' and set(f['targets'])<=set(s['targets']) and f['targets']]
    paths={};count=0;total=0
    for parent in sorted({str(PurePosixPath(f['path']).parent) for f in videos}):
        if pipeline.active:pipeline.active()
        directory=safe_local(root,root/parent,exists=False)
        if not directory.is_dir():raise ValueError('SUBTITLE_DIRECTORY_UNAVAILABLE')
        with os.scandir(directory) as scan:
            for item in scan:
                count+=1
                if count>entries:raise ValueError('SUBTITLE_SCAN_LIMIT')
                path=Path(item.path)
                if path.suffix.casefold() not in TEXT_SUBTITLE_SUFFIXES|{'.zip','.rar','.7z'}:continue
                if pipeline.active:pipeline.active()
                path=safe_local(root,path);relative=path.relative_to(root).as_posix()
                if relative in {f['path'] for f in s['torrent_files']} and path.suffix.casefold() not in {'.zip','.rar','.7z'}:continue
                # Only an exact video stem plus optional language/track suffix
                # associates a host sidecar; unique-directory coincidence is not proof.
                matches=[f for f in videos if PurePosixPath(f['path']).parent==PurePosixPath(relative).parent and
                    (path.stem.casefold()==PurePosixPath(f['path']).stem.casefold() or path.stem.casefold().startswith(PurePosixPath(f['path']).stem.casefold()+'.'))]
                if not matches:
                    outside=[f for f in s['torrent_files'] if f['role']=='video' and PurePosixPath(f['path']).parent==PurePosixPath(relative).parent and
                        (path.stem.casefold()==PurePosixPath(f['path']).stem.casefold() or path.stem.casefold().startswith(PurePosixPath(f['path']).stem.casefold()+'.'))]
                    if outside:continue
                    raise ValueError('SUBTITLE_BINDING_UNCONFIRMED')
                if len(matches)!=1:raise ValueError('SUBTITLE_BINDING_AMBIGUOUS')
                if path.suffix.casefold() in {'.zip','.rar','.7z'}:raise ValueError('SUBTITLE_ARCHIVE_PENDING')
                size=path.stat().st_size;total+=size
                if not 0<size<=104857600 or total>268435456:raise ValueError('SUBTITLE_BYTES_LIMIT')
                before=path.stat();strong,sha=asset_hashes(path);after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('SUBTITLE_ASSET_CHANGED')
                paths[relative]=dict(file=dict(index=0,path=relative,size=size,role='subtitle',targets=matches[0]['targets'],requires=[]),sha256=strong,sha1=sha,mtime_ns=after.st_mtime_ns)
    assets=[paths[k] for k in sorted(paths)]
    for index,asset in enumerate(assets,len(s['torrent_files'])):asset['file']['index']=index
    fingerprint=digest(assets);key='subtitle-observation:'+plan['id'];old=repo.setting(key);now=instant()
    if not old or old['fingerprint']!=fingerprint:
        repo.setting(key,dict(fingerprint=fingerprint,since=now.isoformat(),assets=assets));return dict(state='WAITING_ASSETS',reason='SUBTITLE_STABILITY_WINDOW')
    if (now-parse(old['since'])).total_seconds()<max(1,seconds):return dict(state='WAITING_ASSETS',reason='SUBTITLE_STABILITY_WINDOW')
    observed_at=old.get('verified_at') or now.isoformat()
    repo.setting(key,dict(old,verified_at=observed_at))
    for asset in assets:asset.update(stable_since=old['since'],observed_at=observed_at)
    return dict(state='VERIFIED',assets=assets,observation=dict(fingerprint=fingerprint,since=old['since'],at=observed_at))
