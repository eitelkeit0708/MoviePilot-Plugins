# 豆瓣榜单订阅Plus优化版 2.1.0 (V3)

V3-only port of `DoubanRankPlusOptimized` 1.0.11. Preserves the accepted V2
workflow: RSS parsing/filtering, year/rating checks, movie wash with
`best_version=1`, per-episode TV wash with `best_version_full=0`, all-season
loops with season dedup, failure retry schedule, and history dedup.

## V3 changes
- Imports from stable `app.sdk`; subscribes with explicit
  `media_source`/`media_id` (never `tmdbid`/`doubanid` kwargs).
- Persisted identity uses a source-aware pair; TV season behavior remains
  deliberately TMDB-only. RSS subject IDs/URLs remain source-specific protocol.
- On init, history identities and success targets are migrated idempotently.
  The original history is backed up under `history_legacy_backup` before any
  replacement; a migration summary is stored under `history_migration`.
  Existing valid pairs win, conflicting legacy data is retained with an explicit
  marker, and invalid/unidentifiable rows are preserved.
  Only recognized legacy TMDB IDs backfill the primary pair. An RSS Douban subject
  ID stays RSS metadata, so an old unrecognized item can still be recognized and retried.
- JSON plugin APIs declare response models. Migration endpoints return the V3
  envelope; HTTP migration input unwraps an envelope only at the boundary and
  still accepts legacy raw V2 payloads.
- Class/plugin ID, config/data keys, auth modes (`bear`/`apikey`) and clone IDs
  are unchanged.

Minimum considered host: MoviePilot 3.2.4 (`dist/mpv3-audit-20261106/host-v3`).
No production deployment or network validation is performed here.

## 洗版管理

- 新订阅仍由 MoviePilot 创建和执行。电影默认洗版，剧集默认分集洗版。
- 按 MoviePilot 保存的创建者识别本插件已有订阅。分身分别署名，各自管理新建订阅。
- 电影已发行且入库，或目标季已确认完结且完整入库后，才允许自动完成。当前季须有明确完结状态，已有后续季播出的旧季也须核对官方集数和播出日期。媒体库条目还须匹配来源和 ID。
- 确认完结且完整入库后起算，后续下载会延长计时。默认电影 180 天、全集 60 天、分集 30 天，可设置为 0 关闭对应到期处理。到期与最高优先级自动完成使用同一核验。

## Test

```powershell
python -m unittest discover -s tests/v3/doubanrankplusoptimized -p '*_regression.py' -v
python -m unittest discover -s tests/doubanrankplusoptimized -p '*_regression.py' -v
```

The V3 workflow suite uses offline host fakes. The controller also verified the
real MP 3.2.4 SDK, initialization, native subscription-create argument boundary,
and legacy failed-history recovery in an isolated network-disabled container.
No real RSS refresh, media-provider request or subscription database write was performed.
