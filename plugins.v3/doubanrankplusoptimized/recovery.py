"""Recover MP wash progress when an attributed download never made it to the library."""

import re
from dataclasses import replace
from datetime import datetime

from app.application.subscription.mutation import SubscriptionActor
from app.application.subscription.priority import (
    get_best_version_target_episodes,
    get_episode_priority,
    prepare_subscribe_progress_fields,
)
from app.schemas.types import MediaType
from app.sdk.logging import logger
from app.sdk.media import resolve_media_identity
from app.sdk.queries import list_download_history, list_transfer_history
from app.sdk.services import DownloaderHelper, MediaServerHelper


def recover_failed_downloads(chain, subscribe, media, media_chain, missing):
    is_movie = subscribe.type == MediaType.MOVIE.value
    per_episode = not is_movie and not subscribe.best_version_full
    target = set(get_best_version_target_episodes(subscribe)) if not is_movie else {0}
    priorities = get_episode_priority(subscribe) if per_episode else {}
    top = {episode for episode in target if
           (priorities.get(str(episode)) if per_episode else subscribe.current_priority) == 100}
    unseen = top if missing is None else top & missing
    if not unseen or not media or subscribe.episode_group or not subscribe.date:
        return
    identity = resolve_media_identity(media_source=subscribe.media_source, media_id=subscribe.media_id)
    if not all(identity) or resolve_media_identity(media=media) != identity:
        return

    # Read newest submissions first and use only records that can be scoped to this subscription.
    page_number = 1
    failed_tasks = {}
    failed_episodes = set()
    started = datetime.fromisoformat(subscribe.date)
    helper = DownloaderHelper()
    while unseen or failed_episodes:
        page = list_download_history(
            {"media_source": subscribe.media_source, "media_id": subscribe.media_id},
            {"page": page_number, "count": 100},
        )
        for row in page.items:
            if not row.date or datetime.fromisoformat(row.date) < started:
                continue
            note = row.note if isinstance(row.note, dict) else {}
            source = chain.parse_subscribe_source_keyword(note.get("source") or "") or {}
            submission = note.get("subscription_download") or {}
            if (source.get("id") != subscribe.id or source.get("season") != subscribe.season
                    or source.get("episode_group") != subscribe.episode_group):
                continue
            if submission and (submission.get("subscription_id") != subscribe.id
                               or bool(submission.get("best_version_full")) != bool(subscribe.best_version_full)):
                continue

            episodes = target
            if not is_movie:
                if submission:
                    episodes = set(submission.get("initial_episodes") or []) | set(submission.get("upgrade_episodes") or [])
                elif row.seasons == f"S{subscribe.season:02d}":
                    # Old MP records contain E01, E01-E05 or lists of selected episodes.
                    episodes = set()
                    for first, last in re.findall(r"E(\d+)(?:-E(\d+))?", row.episodes or ""):
                        episodes.update(range(int(first), int(last or first) + 1))
                    if not episodes and not per_episode and not row.episodes:
                        episodes = target
                else:
                    continue
            affected = (unseen | failed_episodes) & episodes
            if not affected:
                continue
            if not per_episode and not is_movie and not target.issubset(episodes):
                unseen.difference_update(affected)
                failed_episodes.difference_update(affected)
                continue

            task = (row.downloader, row.download_hash)
            if task not in failed_tasks:
                service = helper.get_service(name=row.downloader) if all(task) else None
                failed = False
                if service and service.type in {"qbittorrent", "transmission", "rtorrent"}:
                    torrents, error = service.instance.get_torrents(ids=row.download_hash)
                    failed = not error and (not torrents or (
                        service.type == "qbittorrent"
                        and all(str(torrent.get("state")).lower() == "missingfiles" for torrent in torrents)
                    ))
                if failed:
                    successful = list_transfer_history(
                        {"download_hash": row.download_hash, "status": True}, {"count": 200},
                    )
                    # Successful transfers may still be waiting for the cloud handoff.
                    # A movie, an ambiguous transfer or a truncated page protects the whole task.
                    transferred = set()
                    for transfer in successful.items:
                        if is_movie or not transfer.episodes:
                            transferred = target
                            break
                        if transfer.seasons == f"S{subscribe.season:02d}":
                            for first, last in re.findall(r"E(\d+)(?:-E(\d+))?", transfer.episodes):
                                transferred.update(range(int(first), int(last or first) + 1))
                        elif "-" in (transfer.seasons or "") or not transfer.seasons:
                            transferred = target
                            break
                    failed_tasks[task] = set() if successful.has_next else target - transferred
                else:
                    failed_tasks[task] = set()
            failed = affected & failed_tasks[task]
            failed_episodes.update(unseen & failed)
            failed_episodes.difference_update(affected - failed)
            unseen.difference_update(affected)
        if not page.has_next:
            break
        page_number += 1
    if not failed_episodes:
        return
    if missing is None:
        # MP also returns None when a provider cannot answer. Require a live server
        # and a repeated absence before treating that result as missing.
        services = [service for service in MediaServerHelper().get_services().values()
                    if service.type in {"emby", "jellyfin", "plex"}]
        if not services or not all(
            service.instance.get_librarys() if service.type == "plex" else service.instance.get_user()
            for service in services
        ):
            return
        if media_chain.media_exists(mediainfo=media) is not None:
            return
    if per_episode:
        # Keep explicit zeroes so legacy aggregate priority can't refill removed entries.
        priorities.update({str(episode): 0 for episode in failed_episodes})
        note = [episode for episode in (subscribe.note or []) if episode not in failed_episodes]
        updated = replace(subscribe, episode_priority=priorities, note=note)
        payload = {"episode_priority": priorities, "note": note, **prepare_subscribe_progress_fields(updated)}
    else:
        payload = {"current_priority": 0}
    if chain.subscription_repository.get(subscribe.id) != subscribe:
        return
    with chain.sync_subscription_mutation_scope() as command:
        result = command.update(subscribe.id, payload, SubscriptionActor(subscribe.username, False),
                                existing=subscribe, scene="豆瓣订阅恢复失败下载")
    if result:
        logger.info(f"豆瓣订阅 {subscribe.id} 的下载任务已缺失且未整理成功，恢复对应范围的候选搜索")
