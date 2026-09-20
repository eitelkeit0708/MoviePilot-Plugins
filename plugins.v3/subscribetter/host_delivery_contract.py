"""Host wiring and explicit isolated contract. No routes, credentials or timers."""
import json

from .archive import Archive,HostArchiveSources
from .delivery import Delivery,LocalReconciler,PublicationGate,PERMISSIONS
from .delivery_cloud import HostDeliveryCloud
from .planner import Authority
from .policy import Policy


def build_delivery(plugin,config):
    """Public source claims live in immutable manifests; no alternate current."""
    sources=HostArchiveSources(plugin,cloud_scopes=config['cloud_scopes'],libraries=config['libraries'])
    policy=Policy(config['policy_bindings'],config['classification_revision'])
    archive=Archive(plugin.repository,policy,sources,mappings=config['mappings'])
    cloud=HostDeliveryCloud(sources)
    def gate(plan,publication):
        return PublicationGate(archive,publication)(plan)
    return Delivery(plugin.repository,Authority(plugin.repository),archive,cloud,rules=config['rules'],publication_validator=gate)


def close_delivery(worker):
    try:worker.cloud.close()
    finally:worker.archive.sources.close()


def run_host_contract(plugin,*,phase,fixture):
    """Invoke through the existing authenticated temporary test probe only.

    This never creates a task/plan/current, rewrites a snapshot, runs a download,
    or manufactures a consumer result. Each phase is bounded and replay-aware.
    """
    if phase not in ('preflight','prepare','reconcile','publish','consumer','cancel','cleanup','scan','status'):raise ValueError('UNKNOWN_DELIVERY_PHASE')
    config=fixture['config'];rules=config['rules']
    if len(rules)!=1:raise ValueError('EXACT_TEST_RULE_REQUIRED')
    rule=rules[0]
    if (rule['local_root']!='/test-data/organized/open-film' or rule['staging_root']!='/115/subscriBetter/staging' or rule['incoming_root']!='/115/subscriBetter/incoming' or rule['cloud_scope_id']!='test-115'):raise ValueError('TEST_DELIVERY_SCOPE_REQUIRED')
    if config['libraries']!={'subscriBetter Emby test':['533548']} or set(config['cloud_scopes'])!={'test-115'}:raise ValueError('TEST_ARCHIVE_SCOPE_REQUIRED')
    scope=config['cloud_scopes']['test-115']
    if scope['root']!='/115' or scope['allowed_prefixes']!=['/115/subscriBetter'] or scope.get('cd2_plugin')!='CloudDriveDisk' or scope.get('p115_plugin')!='P115Disk':raise ValueError('TEST_CLOUD_SCOPE_REQUIRED')
    if any(rule.get(p,False) for p in PERMISSIONS) and phase!='cleanup':raise ValueError('CLEANUP_REQUIRES_SEPARATE_EXPLICIT_PHASE')
    plan=Authority(plugin.repository).plan(fixture['plan_id']);s=plan['snapshot']
    if s['infohash']!='f3119b64f20f22769552e98bf650ac3da20bd10a' or s['save_path']!='/test-data/downloads/open-film' or s['selected_indices']!=list(range(10)) or any(json.loads(k)!=['电影','themoviedb','10378',None,'',None] for k in s['targets']):raise ValueError('REAL_OPEN_FILM_PLAN_REQUIRED')
    worker=build_delivery(plugin,config)
    try:
        if phase=='preflight':
            publication=worker.validate_publication(plan,config['publication'])
            current=worker.archive.current(list(s['targets']))
            stale=any(v['revision']!=s['current'][k]['revision'] for k,v in current.items())
            result={'state':'CURRENT_REAUTHORIZE_REQUIRED' if stale else 'READY','current':{k:{x:v[x] for x in ('state','revision','diagnostics')} for k,v in current.items()},'publication':publication}
        elif phase=='prepare':result=worker.prepare(plan['id'],rule['id'],publication=worker.validate_publication(plan,config['publication']),source_plan_id=fixture.get('source_plan_id'))
        elif phase=='scan':result=LocalReconciler(plugin.repository,rules).scan(rule['id'],limits=fixture.get('limits'))
        else:
            bid=fixture['bundle_id'];bundle=worker.bundle(bid)
            if bundle['plan_id']!=plan['id'] or bundle['rule_id']!=rule['id']:raise ValueError('BUNDLE_SCOPE_MISMATCH')
            if phase=='status':
                result=dict(worker._result(bundle),publish_action_id=bundle['publication_action'],manifest=bundle['manifest'],files=[{k:f.get(k) for k in ('file_index','relative_path','size','sha1','state','misses','due','upload_id','reader_stopped','local_reader_stopped','progress','refresh_pending')} for f in bundle['files']])
            elif phase=='reconcile':result=worker.reconcile(bid,limits=fixture.get('limits'))
            elif phase=='publish':result=worker.publish(bid)
            elif phase=='consumer':result=worker.confirm(bid,fixture['consumer_receipt'])
            elif phase=='cancel':result=worker.cancel(bid,reason=fixture['reason'],exclusion_id=fixture['exclusion_id'],criteria=fixture['criteria'])
            else:result=worker.cleanup(bid,scope=fixture['cleanup_scope'])
        return dict(result,phase=phase,plan_id=plan['id'],provider_policy_acceptance=phase in ('preflight','prepare'),final_ingest_confirmed=result.get('state')=='CONFIRMED',live_contract=True)
    finally:close_delivery(worker)
