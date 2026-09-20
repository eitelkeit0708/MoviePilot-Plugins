"""Immutable bounded decision evidence; no resource objects or execution queue."""
import json
import re
from uuid import uuid4
from .planner import encoded
from .repository import utcnow
from .ai import digest


def public(value, depth=0):
    """Public JSON boundary: remove transport secrets, absolute paths and HTML."""
    if depth>24:raise ValueError('EVIDENCE_DEPTH_LIMIT')
    if isinstance(value,dict):
        return {str(k):public(v,depth+1) for k,v in value.items()
                if (k=='authorization' and v in ('PREPARED','ACTIVE','CANCELLED','SUPERSEDED','ARCHIVED')) or not re.search(r'cookie|passkey|password|authorization|credential(?!_ref)|api_key|token|^content$|^media$|^meta$|^endpoint$|url$',str(k),re.I)}
    if isinstance(value,(list,tuple)):
        if len(value)>10000:raise ValueError('EVIDENCE_ITEMS_LIMIT')
        return [public(v,depth+1) for v in value]
    if isinstance(value,str):
        if len(value)>32768:raise ValueError('EVIDENCE_TEXT_LIMIT')
        value=re.sub(r'(?:https?|ftp)://[^\s<>"\']+|magnet:\?[^\s<>]+','[URL]',value,flags=re.I)
        value=re.sub(r'<[^>]*>','',value)
        if value.startswith(('/','\\')) or re.match(r'^[A-Za-z]:[\\/]',value):return '[PATH]'
        return value
    if type(value)is int and abs(value)>9007199254740991:return str(value)
    if value is None or type(value) in (int,float,bool):return value
    raise ValueError('EVIDENCE_JSON_REQUIRED')


def append(repository,key,scope,output,*,task_id=None,opportunity_id=None,simulation=False,mode='episode',observed=None,context=None,sanitize=None):
    from .execution import Exclusions
    status='ACCEPT' if output.get('plans') else 'ENRICH' if output.get('enrichments') else 'DEFER'
    decisions=output.get('decisions',{})
    if decisions and all(v.get('status')=='REJECT' for v in decisions.values()):status='REJECT'
    if output.get('status') in ('ACCEPT','REJECT','DEFER','ENRICH'):status=output['status']
    data=public(dict(candidate_key=key,scope=list(scope),mode=mode,simulation=simulation,
        observed=observed or {},evaluation=output,plan_digests=[digest({k:v for k,v in p.items() if k not in ('decision_id','decision_digest')}) for p in output.get('plans',[])],
        revisions=dict(context or {},exclusion=Exclusions(repository).token())))
    if sanitize:data=sanitize(data)
    text=encoded(data)
    if len(text.encode())>2_000_000:raise ValueError('DECISION_SIZE_LIMIT')
    identity='decision:'+uuid4().hex;signature=digest(data)
    with repository.connection(write=True) as db:
        db.execute('INSERT INTO candidate_decisions VALUES(?,?,?,?,?,?,?,?,?)',
            (identity,key,task_id,opportunity_id,status,int(simulation),signature,text,utcnow()))
    return dict(decision_id=identity,decision_digest=signature)
