"""Completion policy for subscriptions created by this plugin instance."""

from datetime import date, datetime, timedelta

from app.schemas.types import MediaType
from app.sdk.media import resolve_media_identity


SUBSCRIPTION_DAYS = {"movie_subscription_days": 360, "full_subscription_days": 180, "episode_subscription_days": 90}
WASH_DAYS = {key.replace("_subscription_", "_wash_"): days // 2 for key, days in SUBSCRIPTION_DAYS.items()}


def wash_days(value, default):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def library_missing(subscribe, media, library, media_chain):
    """Return missing target episodes (0 for a movie), or None for unverified coverage."""
    identity = resolve_media_identity(media_source=subscribe.media_source, media_id=subscribe.media_id)
    media_type = MediaType(subscribe.type)
    if not all(identity) or not media or media.type != media_type or resolve_media_identity(media=media) != identity:
        return None
    if subscribe.episode_group:
        # This plugin creates standard seasons. An edited episode group needs its own mapping.
        return None
    expected = {0}
    if media_type == MediaType.TV:
        total = subscribe.total_episode or 0
        if subscribe.season is None or (subscribe.start_episode or 1) != 1 or total <= 0:
            return None
        expected = set(range(1, total + 1))

    exists = media_chain.media_exists(mediainfo=media)
    if not exists or exists.type != media_type or not exists.server or not exists.itemid:
        return None
    item = library.iteminfo(exists.server, exists.itemid)
    if not item or resolve_media_identity(media_source=item.media_source, media_id=item.media_id) != identity:
        return None
    if media_type == MediaType.MOVIE:
        return set()
    return expected.difference((exists.seasons or {}).get(subscribe.season, []))


def ready_to_finish(subscribe, media, library, media_chain, today):
    """Require a positive match in the media server and a closed release scope."""
    if library_missing(subscribe, media, library, media_chain) != set():
        return False
    return release_finished(subscribe, media, media_chain, today)


def release_finished(subscribe, media, media_chain, today):
    if subscribe.type == MediaType.MOVIE.value:
        released = parse_date(media.release_date)
        return bool(released and released <= today)

    if not media.tmdb_id:
        return False
    tmdb_id = int(media.tmdb_id)
    series = media_chain.tmdb_info(tmdb_id, MediaType.TV) or {}
    if series.get("id") != tmdb_id:
        return False
    season = subscribe.season
    next_episode = series.get("next_episode_to_air") or {}
    if next_episode and (next_episode.get("season_number") is None or next_episode["season_number"] <= season):
        return False
    last = series.get("last_episode_to_air") or {}
    last_season = last.get("season_number")
    last_aired = parse_date(last.get("air_date"))
    terminal_series = series.get("status") in {"Ended", "Canceled"} and series.get("in_production") is not True
    if not last_aired or last_aired > today or last_season is None or last_season < season:
        return False
    if last_season == season and last.get("episode_number") != subscribe.total_episode:
        return False
    if last_season == season and last.get("episode_type") == "mid_season":
        return False

    declared = next((row.get("episode_count") for row in (series.get("seasons") or [])
                     if row.get("season_number") == season), None)
    if declared != subscribe.total_episode:
        return False
    detail = media_chain.tmdb_info(tmdb_id, MediaType.TV, season=season) or {}
    expected = set(range(1, subscribe.total_episode + 1))
    rows = detail.get("episodes") or []
    episodes = {row.get("episode_number"): parse_date(row.get("air_date")) for row in rows}
    if (detail.get("season_number") != season or len(rows) != len(expected) or set(episodes) != expected
            or not all(aired and aired <= today for aired in episodes.values())):
        return False
    if any(row["episode_number"] == max(expected) and row.get("episode_type") == "mid_season" for row in rows):
        return False
    finales = {row["episode_number"] for row in rows if row.get("episode_type") == "finale"}
    return terminal_series or last_season > season or finales == {max(expected)}


def wash_due(ready_since, last_update, days, now):
    """Start at verified completeness, and extend after later download activity."""
    if not days:
        return False
    anchor = datetime.fromisoformat(ready_since)
    if last_update:
        updated = datetime.fromisoformat(last_update)
        anchor = max(anchor, updated)
    return now >= anchor + timedelta(days=days)
