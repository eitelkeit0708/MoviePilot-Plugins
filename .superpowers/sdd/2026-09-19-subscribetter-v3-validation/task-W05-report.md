# W05 implementation and validation report

Base: W04 final `1248f2a088ac5558e4d66d03a8e487a9426635d4`. Implementer used Ponytail full and bounded TDD. No child agents, NAS operations, external requests, downloads or AI calls were performed by this worker. Root owns the actual isolated host tests. This report does not promote synthetic policy fixtures or local fake clients to live acceptance.

## Delivered implementation

`candidates.py` implements explicit selected-site discovery through the public `SearchChain.search_site_torrents(site,keyword,mtype=None,page=...)` entry, configured `SitesHelper` snapshots, and bounded keywords/pages/concurrency/results/calls. Page-size capability calls also consume the request budget. Empty sites cause zero calls; unrelated first-keyword results do not terminate a later keyword. Native global quality processing is not invoked. Search credentials remain in bounded runtime objects; SQLite retains sanitized original title/description, missing-field state, first-seen time, identity/Meta/file/classification evidence and actual policy decisions. Keys use site plus resource ID, otherwise a digest of normalized detail reference; title and size are never identity. RSS detail refresh searches only the originating selected site and matches the exact candidate key; unavailable/incomplete details remain DEFER.

`CandidatePipeline` acquires real torrent bytes through public `DownloadChain.download_torrent`, recognizes corrected Meta through public media module dispatch, and compares the returned provider identity without filling unknown candidates with target IDs. Explicit same-provider conflicts reject before recognition. High-level NameRecognize/MediaRecognize auxiliary workflows and their implicit AI hooks are not invoked. Each physical video is separately parsed by W03 MetaService before scope binding. Actual current classification and W06-compatible current facts enter W02 Policy and W04 Planner; no native global rule or separate rank/authority implementation is introduced. Missing current facts remain unknown. Fresh classification/current/exclusion checks run again at dispatch. Runtime acquisition evidence expires after five minutes and must be refreshed after restart; stale evidence cannot authorize an action.

Torrent parsing uses installed public `torrentool` v1 byte parsing/Bencode APIs. It checks canonical metainfo/hash, full original file table, v1 piece count, duplicates, raw path components, unsupported v2/symlinks, file count and metadata size. Magnet-only or unknown metadata is blocked. The full table includes unselected/cross-season assets. Sidecars bind only to a unique matching video, including no-episode subtitles in a unique video directory; ambiguous association is deferred. IDX/SUB pairs are inseparable. Explicit reviewed ASS/font/license dependency edges propagate physical coverage. Multi-episode video is indivisible. Required dependencies may not introduce a target outside the safe selected scope.

`execution.py` adds configured qB/TR adapters and strict pause/select/readback/resume. qB uses actual priority and TR actual `selected`/completed bytes; an unsupported wanted interface is a capability failure. Parsed entries map to client IDs by exact path plus bytes, not position. Each pause, unwanted assignment, wanted assignment and resume obtains a separate durable W04 attempt. Only dispatch=True permits the RPC. Entire wanted readback must equal the still-active shared hash/layout union; target/task generation, current revision, exclusions and publication barriers are rechecked. Unknown same-hash tasks are untouched. The managed row records exact returned client identity; lost ADD only reconciles if the unique durable add marker, hash, save path, full table and paused state all match. Unknown responses never justify retransmission. Lost resume/selection outcomes can be settled by readback. Shared selection operation identity includes sibling authority, so removing E01 later can legitimately restore the original E02 wanted set without confusing it with an already completed old operation.

Progress uses exact selected-file completed bytes and preserves unknown speed/remaining time. Whole-torrent figures are separate. Old samples only append facts; they never activate a cancelled generation. Content SHA1 exclusions are checked when actual source bytes are available, before history/copy effects; candidate/hash/target/data-predicate exclusions are checked before planning and each dispatch.

`Organizer` accepts completed exact selected files only, checks local path components/symlinks, hashes source bytes, and dispatches one file at a time. Public `DownloadHistoryOper` writes/readbacks contain only authorized assets; older authorized sibling history is preserved by matching durable prior intents. Public `TransferChain.plan_transfer` freezes a destination; existing destinations are refused. Public `TransferChain.transfer` uses pre-recognized media and explicit copy/never-overwrite/no-scrape/no-notify/no-type-or-category-directory options. It never passes a source directory or invokes an extra-file sweep. Subtitle Meta derives from its verified video association; fonts retain names in the organized video's Fonts directory and licenses are delivered beside that video. Success requires exact public transfer history plus destination size/SHA256. Durable uncertain copy results can be reconciled from the pre-recorded destination, public history and actual bytes; no blind copy replay. Local COMPLETE explicitly reports `final_ingest_confirmed=False` and does not call W04 final ingest or publish.

The synchronous `TransferIntercept` listener is installed with the existing safety listeners, survives ordinary-work disablement, and rejects unmanaged files within a managed torrent root, stale/incomplete operations, changed policy/exclusion tokens and wrong destinations. A single-file torrent does not reserve unrelated manual files elsewhere in the shared download directory. The listener uses stat/authority checks rather than hashing large videos on the synchronous event path; source and destination content hashes are checked by the organizer worker. Cached managed paths preserve denial on warm storage failure.

The public native `download_added` and `download_site_subtitles` workflows remain available through `CandidatePipeline.subtitles`; `execute(resume=True)`/`resume` invoke them after accepted resume. They have separate durable intents and an explicit UNVERIFIED asset result, not a fabricated subtitle-complete result. Newly obtained auxiliary files require fresh asset association/planning; they cannot bypass the frozen torrent asset plan or admission guard. No site feature is disabled.

## Schema and product wiring

Schema 4→5 adds only `candidates`, `managed_downloads`, `exclusions`, and `organized_assets`. Existing W01–W04 tables/rows remain unchanged. A real SQLite reconstruction test compares all previous table rows through migration and retains active authority. Existing older-schema reconstruction tests now remove W05 tables before assigning an older schema revision. No W04 planner or scheduler product code changed.

Plugin initialization now exposes the actual `CandidateService` and safety `TransferGuard`; no new timer, public execution route or automatic production dispatcher is registered. W09/W10 still own task/UI/scheduling entrances. CandidatePipeline, StrictExecutor and Organizer are executable product implementations for those callers, not fake-host placeholders.

## Stable calls

- `CandidateService(repo, HostCandidateAdapter()).search(selected_site_ids, keywords, SearchBudget(...))`; `observe(raw, source='rss')`; `supplement(candidate_key, budget)`; bounded `records`.
- `CandidatePipeline(service, meta_service, policy, current_provider, client_factory)`. `current_provider(keys)` must supply actual W06 current snapshots and W02 Version objects. `client_factory(name)` is normally `ConfiguredDownloader.named`. `evaluate(key,target,scope,downloader=...,save_path=...,dependencies=...,custom_words=...,task_id=...,mode='episode'|'season')` returns W04 planner results. Caller retains W04 observation/claim responsibility. `revalidate(plan)`, `executor()`, `execute(plan_id,resume=False)`, `resume(plan_id)`, `subtitles(plan_id)`, `organize(plan_id,target_root)` are actual call paths.
- `StrictExecutor(repo, client_factory, revalidate=...)` requires a current candidate gate. `execute(plan_id, torrent_bytes, resume=False)`, `resume`, `sample`, `reconcile`. Reconcile performs client reads only and records facts.
- `Exclusions(repo).add(id, criteria, reason=..., expires_at=...)` / `revoke`. Criteria supports candidate_key, infohash, target keys, actual content_sha1 and W02 data predicates.
- `Organizer(executor, HostOrganization(media,repository=repo)).organize(plan_id,target_root)` / `reconcile(plan_id)`.

Minimum real raw-candidate read on one already configured authorized site (no download; do not print credential-bearing runtime objects):

```python
from importlib import import_module
candidate_module = import_module(type(plugin).__module__ + '.candidates')
rows = plugin.candidates.search(
    [selected_site_id], [single_keyword],
    candidate_module.SearchBudget(keywords=1, pages=1, concurrency=1,
                                  results=10, requests=2, interval=0))
safe_result = [{k: r.get(k) for k in
               ('candidate_key', 'site', 'title', 'status', 'missing_fields')}
              for r in rows]
```

Root must supply the actually selected site ID and keyword. This intentionally does not discover/download from arbitrary sites. A returned 1080 raw result despite a rejecting native global rule would be actual host evidence; the local stub test alone is not that evidence.

## Real isolated fixture contract

`host_execution_contract.run_host_contract(plugin, task_id, manifest_dict, client, phase, episode=2)` is an internal function, not an unauthenticated API. Root's authenticated temporary probe calls it. `client` is `qb` or `transmission`; phases are `prepare`, `resume`, `sample`, `organize`, with optional read-only-client `reconcile`. Root's chosen task 3 must already be ACTIVE with actual themoviedb/1396/season1/group-empty identity. The function never creates/fakes a native subscription handoff.

Use qB episode=1 and TR episode=2 to avoid competing ownership of the same target. It reads the fixed supplied manifest and real torrent bytes from `/test-data/fixtures/strict-download-20260919`, verifies full table/hash, uses the named `subscriBetter qB test` / `subscriBetter TR test` services, and fixed `/test-data/downloads/{client}` layouts. qB selects indices 2/3; TR selects 0 and 4–10. README and S02 video are excluded. The explicit dependency edges are 5→10→0; IDX/SUB pair edges are derived. The whole 12-file client table is still read back. Root alone starts opposite-client seeds and injects internal peers. No tracker or peer manipulation is performed by this module.

Each prepare/resume/sample call is bounded and does not wait for download completion. Organize verifies selected source hashes against the synthetic manifest and targets `/test-data/organized/{client}`. Fixture outputs expressly include `synthetic_contract=True` and `provider_policy_acceptance=False`; the manifest's explicit synthetic identity/admission is a mechanical test authorization, not evidence that the real PT provider/quality pipeline accepted Breaking Bad. The fixture refuses to overwrite non-fixture planner policy revisions.

## Verification evidence

Environment: Windows `.venv/Scripts/python.exe`, Python 3.12.3; installed `torrentool==1.2.0`; stdlib unittest/SQLite and existing regex. No local test imports host ORM or pretends to run the deployed SDK.

- Initial candidate/execution RED: expected missing W05 module assertions (5 candidate, 6 execution tests). First GREEN was 5/6 respectively.
- Real torrent bytes RED exposed Windows-separated paths returned by the library's convenience file list. Reading raw metainfo components through its public Bencode API fixed it; actual encoded bytes/infohash/full table regression passes.
- Raw native-entry RED: missing HostCandidateAdapter; GREEN proves the wrapper calls only raw per-site search and does not invoke the fake rejecting global processor.
- Organizer and transfer-guard RED: missing classes; GREEN checks exact completed E02+sidecar files, repeat idempotency and manual-path isolation.
- Plugin listener RED: 5 instead of required 6 listeners; GREEN verifies reload has one listener per event and ordinary stop preserves all 4 safety listeners.
- Actual candidate pipeline RED: missing pipeline; GREEN runs actual torrentool bytes, corrected physical scope, real W02 Policy/W04 Planner, provider ID non-injection and changed current classification blocking.
- Shared hash RED: restoring E02 after sibling cancellation stayed BLOCKED because the old select identity was reused; GREEN includes shared target generation in the operation identity.
- Lost-add reconciliation RED: missing reconcile; GREEN requires the exact durable marker plus full paused readback and proves one add only. Lost-resume test proves no second RPC after actual running readback.
- First full regression: 150 tests, 37.705 s; exactly two expected migration-test maintenance failures (schema expected 4 and reconstructed schema3 retaining newly created W05 tables). Fixed the fixtures/version expectation, no W04 product change.
- Subsequent full regression: 156 tests, 38.493 s, exit 0. Final affected focused commands after exclusion/path/current-scope hardening: execution 16 tests, 3.098 s; candidates 9 tests, 0.337 s, both exit 0. Final full command `.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests/v3/subscribetter -p 'test_*.py' -q`: **156 tests, 40.941 s, exit 0**.
- Meaningful additional checks include actual organizer→TransferGuard→SQLite IN_FLIGHT authorization, no calls on changed torrent/stopped generation, schema4→5 all-old-row preservation, selected-byte progress and stale callback non-reactivation, qB raw priority, TR missing-selected capability failure, whole wanted mismatch remaining paused, cross-season/ambiguous/unsafe paths, provider conflicts, duplicate-title IDs and empty-site zero calls.
- `py_compile` all five touched product modules and `git diff --check` passed. Git emits the repository's existing LF/CRLF conversion notices; no whitespace errors.

## Acceptance boundary

Pending root/independent review: actual host import/schema migration; real qB/TR paused add, exact complete wanted readback, accepted resume, byte completion; actual public MP download/transfer history and exact video/subtitle/font/license organization; raw configured-site global-filter bypass evidence. These have not been run by this worker. Actual AI, CD2/115 transport, final ingest/publication, Emby and performance acceptance are outside W05 and are not claimed. IDX/SUB fixtures establish opaque file/dependency delivery only, not decodable subtitle rendering. Configured SDK capability errors remain explicit blocked/unknown outcomes rather than silently widening selection.

Root owns docs and .gitignore; neither is included in the W05 task commit. No production/native task was mutated by this worker. Ready for immutable-commit deployment and scoped independent review; actual acceptance remains open until root records its evidence.
