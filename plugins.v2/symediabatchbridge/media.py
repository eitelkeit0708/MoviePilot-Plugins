"""Notification metadata comes from the exact MP transfer records, without scraping."""

from urllib.parse import urlsplit


def history_media(row):
    result = {}
    for key in ("title", "type", "year", "seasons", "episodes", "image", "tmdbid"):
        value = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            result[key] = str(value).strip()
    return result


def poster_url(value):
    try:
        parts = urlsplit(value or "")
        if parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def notification_media(job, item):
    members = job.get("media_files", [])
    if item.get("file"):
        members = [member for member in members if member["file"] == item["file"]]
    groups = {}
    for member in members:
        if not member.get("title"):
            continue
        identity = tuple(member.get(key, "") for key in ("title", "year", "type", "tmdbid"))
        groups.setdefault(identity, []).append(member)
    if not groups:
        # Before a complete manifest exists, the first event identifies the work,
        # not the batch's episode range. Never report its single episode as all episodes.
        media = job.get("media", {})
        label = media.get("title") or job["title"]
        if media.get("year"):
            label += " (" + media["year"] + ")"
        if media.get("type"):
            label += " · " + media["type"]
        return [label], poster_url(media.get("image"))
    lines, image = [], None
    for (title, year, kind, _), rows in groups.items():
        label = title + (" (" + year + ")" if year else "")
        if kind:
            label += " · " + kind
        # Keep MP's per-file ranges and gaps; subtitles cannot inflate episode counts.
        scopes = sorted({r.get("seasons", "") + r.get("episodes", "") for r in rows} - {""})
        if scopes:
            label += " · " + "、".join(scopes[:12])
            if len(scopes) > 12:
                label += f" 等 {len(scopes)} 项"
        lines.append(label)
        if len(groups) == 1:
            image = next((poster_url(r.get("image")) for r in rows if poster_url(r.get("image"))), None)
    return lines, image
