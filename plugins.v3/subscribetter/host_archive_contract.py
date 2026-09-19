"""Internal authenticated-probe target; never registers an HTTP route or timer."""
from .archive import Archive, HostArchiveSources
from .policy import Policy


def run_host_contract(plugin, *, phase, fixture):
    if phase not in ('sdk', 'resolve', 'reconcile', 'confirm'):
        raise ValueError('UNKNOWN_ARCHIVE_PHASE')
    service, libraries = fixture['emby_service'], fixture['library_ids']
    # This fixture contract is intentionally restricted to the approved isolation.
    if service != 'subscriBetter Emby test' or not libraries or not set(libraries) <= {'533548', '533550'}:
        raise ValueError('TEST_LIBRARIES_REQUIRED')
    scopes = fixture['cloud_scopes']
    for scope in scopes.values():
        if scope['root'] != '/115' or any(p != '/115/subscriBetter' and not p.startswith('/115/subscriBetter/') for p in scope['allowed_prefixes']):
            raise ValueError('TEST_CLOUD_SCOPE_REQUIRED')
    sources = HostArchiveSources(plugin, cloud_scopes=scopes, libraries={service: libraries})
    try:
        if phase == 'sdk':
            limits = {**dict(page_size=10, pages=2, items=10), **fixture.get('limits', {})}
            if any(type(v) is not int or not 1 <= v <= 100 for v in limits.values()):
                raise ValueError('INVALID_CONTRACT_LIMITS')
            results = []
            for library in libraries:
                start, total, ids = 0, None, set()
                complete = False
                for _ in range(limits['pages']):
                    page = sources.emby_page(service, library, start, limits['page_size'])
                    if total is not None and total != page['TotalRecordCount']:
                        raise ValueError('SCAN_TOTAL_CHANGED')
                    total = page['TotalRecordCount']
                    items = page['Items']
                    if (not items and start < total) or start + len(items) > total:
                        raise ValueError('SCAN_INCOMPLETE')
                    for item in items:
                        if item.get('Id') in ids or not isinstance(item.get('Id'), str):
                            raise ValueError('SCAN_DUPLICATE_ITEM')
                        ids.add(item['Id'])
                    start += len(items)
                    if start == total:
                        complete = True
                        break
                results.append(dict(library_id=library, items=start, total=total, complete=complete))
            objects = fixture.get('cloud_objects', [])
            if len(objects) > 10:
                raise ValueError('CONTRACT_OBJECT_LIMIT')
            cloud = []
            for item in objects:
                raw = sources.cloud_stat(item['scope'], item['path'], refresh=False)
                cloud.append(dict(scope=item['scope'], size=raw['size'], full_sha1=len(raw['sha1']) == 40,
                                  cd2_id_present=bool(raw['cd2_id']), p115_id_present=bool(raw['p115_id']), account_match=True))
            return dict(status='PASS' if all(r['complete'] for r in results) else 'INCOMPLETE', phase=phase,
                        libraries=results, cloud=cloud, final_ingest_confirmed=False, media_writes=False)
        policy = Policy(fixture['policy_bindings'], fixture['classification_revision'])
        archive = Archive(plugin.repository, policy, sources, mappings=fixture['mappings'])
        if phase == 'resolve':
            refs = fixture.get('items', [])
            if not 1 <= len(refs) <= 20:
                raise ValueError('EXPLICIT_REAL_ITEMS_REQUIRED')
            result = []
            for ref in refs:
                item = sources.emby_item(service, ref['library_id'], ref['item_id'])
                series = {}
                if item.get('Type') == 'Episode':
                    sid = str(item['SeriesId'])
                    series[sid] = sources.emby_item(service, ref['library_id'], sid)
                for observed in archive.resolve_item(service, ref['library_id'], item, series=series):
                    archive.validate_observation(observed)
                    result.append(dict(version_id=observed['version_id'], mapping_id=observed['mapping_id'],
                                       target_key=observed['target_key'], full_sha1=True, size=observed['video']['size'],
                                       streams_present=bool(observed['streams'])))
            return dict(status='PASS', phase=phase, observations=result, final_ingest_confirmed=False, media_writes=False)
        if phase == 'reconcile':
            reports = [archive.reconcile(service, library, target_keys=fixture.get('target_keys'), limits=fixture.get('limits'), scan_id=fixture.get('scan_id')) for library in libraries]
            return dict(status='PASS' if all(r['status'] == 'COMPLETE' for r in reports) else 'INCOMPLETE', phase=phase, scans=reports, final_ingest_confirmed=False, media_writes=False)
        result = archive.confirm_ingest(fixture['publish_action_id'], fixture['asset_manifest'], fixture['consumer_receipt'])
        return dict(result, phase=phase, final_ingest_confirmed=result['accepted'], media_writes=False)
    finally:
        sources.close()
