"""Bounded site statements plus independent provider identity; no media mutation."""
from dataclasses import replace
from hashlib import sha256
import json
import re
import time
import unicodedata

from .candidates import HostCandidateAdapter, value
from .repository import utcnow
from .site_identity import extract_site_identity


def _name(value_):
    return ''.join(character for character in unicodedata.normalize('NFKC', value_).casefold()
                   if character.isalnum())


def resolve_title_identity(recognizer, meta_service, *, media, douban_id,
                           media_type, deadline, checkpoint):
    """Resolve a Douban TV item only when provider identity, names and year agree."""
    evidence = {'rule_version':'douban-linked-title-year-v2','observed_at':utcnow(),
                'douban_id':str(douban_id),'media_type':media_type,
                'canonical_queries':0,'provider_calls':0}
    def result(state, reason, recognized=None):
        evidence['reason']=reason
        return {'state':state,'media':recognized,'evidence':evidence}
    def check():
        checkpoint()
        if deadline is not None and time.monotonic() >= deadline:raise ValueError('TICK_DEADLINE')
    check()
    if media_type!='电视剧' or not re.fullmatch(r'[1-9][0-9]*',str(douban_id)):
        return result('UNKNOWN','TV_DOUBAN_ID_REQUIRED')
    native=recognizer.source_identity(media,'douban')
    if native.get('state')=='CONFLICT' or (native.get('state')=='VERIFIED' and native.get('media_id')!=str(douban_id)):
        return result('CONFLICT','SOURCE_ID_CONFLICT')
    year=str(value(media,'year') or '')
    if not re.fullmatch(r'[0-9]{4}',year):return result('UNKNOWN','YEAR_REQUIRED')
    detail=value(media,'douban_info') or {}
    aliases=value(detail,'aka',[]) if isinstance(detail,dict) else []
    names=list(dict.fromkeys(item.strip() for item in
        [value(media,'title'),value(media,'original_title'),*(aliases if isinstance(aliases,list) else [])]
        if isinstance(item,str) and item.strip()))[:8]
    trusted={_name(item) for item in names if _name(item)}
    if not trusted:return result('UNKNOWN','TRUSTED_NAMES_REQUIRED')
    found={};unlinked=False
    for name in names:
        check();evidence['canonical_queries']+=1
        correction=meta_service.parse('douban-title:'+_digest([douban_id,media_type,name,year]),f'{name} ({year})')
        if correction.status!='OK':continue
        evidence['provider_calls']+=1
        try:recognized=recognizer.recognize(correction.meta,('themoviedb',None),media_type=media_type)
        except Exception:recognized=None
        check()
        identity=recognizer.identity(recognized) if recognized is not None else None
        if not identity or identity[0]!='themoviedb' or not re.fullmatch(r'[1-9][0-9]*',str(identity[1])):continue
        output_type=value(value(recognized,'type'),'value',value(recognized,'type'))
        output_names={_name(item) for item in (value(recognized,'title'),value(recognized,'original_title'))
                      if isinstance(item,str) and _name(item)}
        output_year=str(value(recognized,'year') or '')
        canonical=recognizer.source_identity(recognized,'themoviedb')
        source=recognizer.source_identity(recognized,'douban')
        if (output_type!=media_type or output_year!=year or not trusted.intersection(output_names)
                or canonical.get('state')=='CONFLICT'
                or canonical.get('state')=='VERIFIED' and canonical.get('media_id')!=str(identity[1])
                or source.get('state')=='CONFLICT'
                or source.get('state')=='VERIFIED' and source.get('media_id')!=str(douban_id)):
            continue
        if source.get('state')!='VERIFIED':
            unlinked=True
            continue
        found[str(identity[1])]=recognized
    if len(found)>1:return result('CONFLICT','MULTIPLE_WORK_IDENTITIES')
    if not found:return result('UNKNOWN','CROSS_SOURCE_ID_REQUIRED' if unlinked else 'NO_VERIFIED_TITLE_YEAR_MAPPING')
    media_id,recognized=next(iter(found.items()))
    evidence['canonical']=['themoviedb',media_id]
    return result('VERIFIED','TITLE_YEAR_PROVIDER_MATCH',recognized)


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def resolve_site_identity(candidates, meta_service, *, media, douban_id,
                          selected_sites, budget, deadline, checkpoint):
    """Verify a movie mapping from candidate-owned statements and provider output.

    requests budgets site I/O; provider calls are independently bounded by the
    detail allowance. Neither target IDs nor site assertions enter recognition.
    """
    evidence = {'rule_version': 'site-imdb-v1', 'observed_at': utcnow(),
                'douban_id': str(douban_id), 'media_type': '电影',
                'declared': {'media_source': 'douban', 'media_id': str(douban_id)},
                'statements': [], 'provider_calls': 0, 'detail_calls': 0}

    def result(state, reason, recognized=None):
        evidence['reason'] = reason
        return {'state': state, 'media': recognized, 'evidence': evidence}

    def check():
        checkpoint()
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError('TICK_DEADLINE')

    check()
    media_type = value(value(media, 'type'), 'value', value(media, 'type'))
    if media_type != '电影' or not re.fullmatch(r'[1-9][0-9]*', str(douban_id)):
        return result('UNKNOWN', 'MOVIE_ID_REQUIRED')
    detail_limit = min(8, max(0, budget.requests - 2))
    if not detail_limit or not selected_sites:
        return result('UNKNOWN', 'SITE_BUDGET_REQUIRED')
    words = list(dict.fromkeys(word for word in
                 (value(media, 'title'), value(media, 'original_title'))
                 if isinstance(word, str) and 1 <= len(word) <= 256))
    if not words:
        return result('UNKNOWN', 'TRUSTED_NAMES_REQUIRED')
    search_budget = replace(budget, requests=budget.requests - detail_limit)
    evidence.update(search_request_limit=search_budget.requests, detail_request_limit=detail_limit)
    rows = candidates.search(selected_sites, words, search_budget, deadline=deadline)
    check()
    found = None
    canonical_ids, declared_imdbs = set(), set()
    conflicts = []
    for row in rows[:detail_limit]:
        check()
        key = row['candidate_key']
        raw = candidates.runtime.get(key)
        if raw is None or value(raw, 'site') not in selected_sites:
            continue
        entry = {'site_id': value(raw, 'site'), 'candidate_key': key,
                 'page_url_sha256': _digest(value(raw, 'page_url') or ''),
                 'candidate_text_sha256': _digest([value(raw, 'title'), value(raw, 'description')])}
        evidence['statements'].append(entry)
        evidence['detail_calls'] += 1
        try:
            html = candidates.adapter.site_description(raw, selected_sites, deadline=deadline)
        except Exception as exc:
            entry['error_type'] = type(exc).__name__
            check()
            continue
        check()
        statement = extract_site_identity(html, media_type='电影')
        entry['statement'] = statement
        if statement['state'] == 'CONFLICT':
            conflicts.append('SITE_STATEMENT_CONFLICT')
            continue
        if statement['state'] != 'DECLARED':
            continue
        ids = statement['provider_ids']
        if ids['douban'] != str(douban_id):
            entry['status'] = 'UNRELATED_DOUBAN'
            continue
        declared_imdbs.add(ids['imdb'])
        correction = meta_service.parse('site-identity:' + _digest(key),
                                        value(raw, 'title') or '', value(raw, 'description'))
        check()
        if correction.status != 'OK':
            entry['status'] = 'META_UNCONFIRMED'
            continue
        evidence['provider_calls'] += 1
        try:
            recognized = candidates.adapter.recognize(correction.meta, ('themoviedb', None), media_type='电影')
        except Exception as exc:
            entry['error_type'] = type(exc).__name__
            check()
            continue
        check()
        identity = candidates.adapter.identity(recognized) if recognized is not None else None
        if not identity or identity[0] != 'themoviedb':
            entry['status'] = 'CANONICAL_ID_UNKNOWN'
            continue
        output_type = value(value(recognized, 'type'), 'value', value(recognized, 'type'))
        known_year = str(value(media, 'year') or '')
        output_year = str(value(recognized, 'year') or '')
        native = HostCandidateAdapter.source_identity(recognized, 'douban')
        native_tmdb = HostCandidateAdapter.source_identity(recognized, 'themoviedb')
        payload = value(recognized, 'tmdb_info') or {}
        external = value(payload, 'external_ids') or {}
        imdb_values = [value(recognized, 'imdb_id'), value(payload, 'imdb_id'), value(external, 'imdb_id')]
        imdb_values = {str(item) for item in imdb_values if item not in (None, '')}
        entry['canonical'] = {'media_source': identity[0], 'media_id': str(identity[1])}
        entry['imdb_ids'] = sorted(imdb_values)
        raw_source = value(value(raw, 'media_source'), 'value', value(raw, 'media_source'))
        raw_id = value(raw, 'media_id')
        raw_expected = {'douban': str(douban_id), 'themoviedb': str(identity[1]), 'imdb': ids['imdb']}
        conflict = (output_type != '电影' or
                    bool(re.fullmatch(r'[0-9]{4}', known_year) and output_year and known_year != output_year) or
                    native['state'] == 'CONFLICT' or
                    native_tmdb['state'] == 'CONFLICT' or
                    native_tmdb['state'] == 'VERIFIED' and native_tmdb['media_id'] != str(identity[1]) or
                    native['state'] == 'VERIFIED' and native['media_id'] != str(douban_id) or
                    bool(imdb_values and imdb_values != {ids['imdb']}) or
                    raw_source in raw_expected and raw_id not in (None, '') and str(raw_id) != raw_expected[raw_source])
        if conflict:
            conflicts.append('PROVIDER_ID_CONFLICT')
            entry['status'] = 'CONFLICT'
            continue
        if imdb_values != {ids['imdb']} or not re.fullmatch(r'[1-9][0-9]*', str(identity[1])):
            entry['status'] = 'EXTERNAL_ID_UNKNOWN'
            continue
        canonical_ids.add(str(identity[1]))
        found = found or recognized
        entry['status'] = 'VERIFIED'
    check()
    if conflicts or len(canonical_ids) > 1 or len(declared_imdbs) > 1:
        return result('CONFLICT', conflicts[0] if conflicts else 'MULTIPLE_WORK_IDENTITIES')
    if found is None:
        return result('UNKNOWN', 'NO_VERIFIED_SITE_MAPPING')
    evidence['canonical'] = ['themoviedb', next(iter(canonical_ids))]
    return result('VERIFIED', 'SITE_IMDB_PROVIDER_MATCH', found)
