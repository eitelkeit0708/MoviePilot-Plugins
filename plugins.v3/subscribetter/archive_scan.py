"""Atomic publication of a validated scan. All I/O precedes this transaction.

SQLite materializes the scope, never a Python list of the library. JSON scalar
functions below only canonicalize one target/version and perform no I/O.
"""
import json
from .planner import encoded, BARRIERS
from .archive import digest, content
from .repository import utcnow


def publish(archive, service, library, scan_id, scan):
    started=scan['started_at'];now=utcnow();mapping=archive.mappings.revision
    def canonical(value):return encoded(json.loads(value))
    def source(value):return encoded({k:v for k,v in json.loads(value).items() if k!='observed_at'})
    def location_id(value):
        loc=json.loads(value)
        return digest([loc['cloud_scope_id'],loc.get('account_ref'),loc['path'],digest(content(loc))])
    def absence_extra(previous,present):
        data=json.loads(previous or '{}');scopes=data.get('absence_scopes',[])
        if data.get('absence_scope') and data['absence_scope'] not in scopes:scopes.append(data['absence_scope'])
        scopes=[s for s in scopes if s!=[service,library]]
        if not present:scopes.append([service,library])
        return encoded(dict(absence_scopes=sorted(scopes),**(dict(absence_scope=[service,library]) if not present else {})))
    def projection(state,versions,diagnostics,extra):
        versions=[json.loads(v) if isinstance(v,str) else v for v in json.loads(versions)]
        codes=json.loads(diagnostics);extra=json.loads(extra)
        stable=[{k:v for k,v in o.items() if k!='observed_at'} for o in versions]
        revision=digest([state,mapping,stable,codes])
        data=dict(mapping_revision=mapping,diagnostics=codes,evidence_ref=scan_id,**extra)
        facts=dict(state=state,archive_revision=revision,evidence_ref='archive:'+revision,
            versions=[dict(version_id=o['version_id'],raw=o['raw'],reliable=o['reliable']) for o in versions])
        return encoded(dict(revision=revision,data=encoded(data),facts=encoded(facts)))
    with archive.repository.connection(write=True) as db:
        row=db.execute('SELECT state,data FROM archive_scans WHERE id=?',(scan_id,)).fetchone()
        if not row or row['state']!='INCOMPLETE' or json.loads(row['data'])!=scan or mapping!=scan['mapping']:
            raise ValueError('STALE_SCAN')
        if scan.get('publication') is not None:
            scan['completed_at']=now
            db.execute("UPDATE archive_scans SET state='COMPLETE',data=? WHERE id=?",(encoded(scan),scan_id))
            return
        db.create_function('canonical_json',1,canonical,deterministic=True)
        db.create_function('json_digest',1,lambda s:digest(json.loads(s)),deterministic=True)
        db.create_function('source_json',1,source,deterministic=True)
        db.create_function('location_id',1,location_id,deterministic=True)
        db.create_function('projection',4,projection,deterministic=True)
        db.create_function('absence_extra',2,absence_extra,deterministic=True)
        db.execute("CREATE TEMP TABLE observed AS SELECT json_extract(j.value,'$.version_id') id, json_extract(j.value,'$.target_key') target_key,j.value data FROM archive_scan_items i,json_each(i.resolved) j WHERE i.scan_id=?",(scan_id,))
        db.execute('CREATE INDEX observed_target ON observed(target_key)')
        # Multiple Emby source rows may address the same exact content/location.
        db.execute('DELETE FROM observed WHERE rowid NOT IN (SELECT MAX(rowid) FROM observed GROUP BY id)')
        db.execute('CREATE UNIQUE INDEX observed_id ON observed(id)')
        db.execute('CREATE TEMP TABLE affected(target_key TEXT PRIMARY KEY, scanned INTEGER NOT NULL)')
        if scan['targets'] is not None:
            db.execute('INSERT INTO affected SELECT value,1 FROM json_each(?)',(encoded(scan['targets']),))
        else:
            db.execute('INSERT OR IGNORE INTO affected SELECT target_key,1 FROM archive_versions WHERE service=? AND library=?',(service,library))
            db.execute('INSERT OR IGNORE INTO affected SELECT target_key,1 FROM observed')
        if db.execute('SELECT 1 FROM affected f JOIN archive_targets t USING(target_key) WHERE t.updated_at>? LIMIT 1',(started,)).fetchone():
            raise ValueError('SCAN_SUPERSEDED')
        if db.execute('SELECT 1 FROM affected f JOIN target_units u USING(target_key) JOIN tasks t ON t.id=u.task_id LEFT JOIN archive_scan_baselines b ON b.scan_id=? AND b.target_key=u.target_key WHERE b.target_key IS NULL OR b.current_revision!=u.current_revision OR b.unit_generation!=u.generation OR b.task_generation!=t.generation LIMIT 1',(scan_id,)).fetchone():
            raise ValueError('SCAN_AUTHORITY_CHANGED')
        db.execute('DELETE FROM affected WHERE target_key IN (SELECT target_key FROM target_units WHERE publish_phase IN (SELECT value FROM json_each(?)))',(encoded(sorted(BARRIERS)),))
        db.execute('DELETE FROM observed WHERE target_key NOT IN (SELECT target_key FROM affected)')
        db.execute("CREATE TEMP TABLE absence_pending AS SELECT f.target_key FROM affected f LEFT JOIN archive_targets t USING(target_key) WHERE NOT EXISTS(SELECT 1 FROM observed o WHERE o.target_key=f.target_key) AND (json_extract(t.data,'$.absence_scope') IS NULL OR json_extract(t.data,'$.absence_scope')!=json(?)) AND NOT EXISTS(SELECT 1 FROM json_each(t.data,'$.absence_scopes') s WHERE s.value=json(?))",(encoded([service,library]),encoded([service,library])))
        db.execute("INSERT OR IGNORE INTO archive_targets SELECT target_key,'UNKNOWN','', '{}',? FROM affected",(now,))
        db.execute('UPDATE archive_versions SET active=0 WHERE service=? AND library=? AND target_key IN (SELECT target_key FROM affected EXCEPT SELECT target_key FROM absence_pending)',(service,library))
        db.execute("INSERT INTO archive_versions SELECT id,target_key,?,?,1,canonical_json(data) FROM observed WHERE 1 ON CONFLICT(id) DO UPDATE SET active=1,data=excluded.data",(service,library))
        db.execute("CREATE TEMP TABLE assets AS SELECT o.id version_id,json_extract(a.value,'$.file_index') file_index,canonical_json(a.value) data,canonical_json(json_extract(a.value,'$.location')) location FROM observed o,json_each(o.data,'$.assets') a")
        db.execute("ALTER TABLE assets ADD COLUMN content_id TEXT")
        db.execute("ALTER TABLE assets ADD COLUMN location_id TEXT")
        db.execute("UPDATE assets SET content_id=json_digest(json_object('sha1',lower(json_extract(location,'$.sha1')),'size',json_extract(location,'$.size'))),location_id=location_id(location)")
        if db.execute("SELECT 1 FROM assets GROUP BY json_extract(location,'$.cloud_scope_id'),json_extract(location,'$.account_ref'),json_extract(location,'$.path') HAVING COUNT(DISTINCT content_id)>1 LIMIT 1").fetchone():
            raise ValueError('SCAN_ASSET_CHANGED')
        db.execute('CREATE INDEX scan_asset_location ON assets(location_id)')
        db.execute("CREATE TEMP TABLE replaced AS SELECT DISTINCT l.id FROM archive_locations l JOIN assets a ON l.scope=json_extract(a.location,'$.cloud_scope_id') AND l.path=json_extract(a.location,'$.path') AND l.content_id!=a.content_id AND json_extract(l.data,'$.account_ref') IS json_extract(a.location,'$.account_ref')")
        db.execute('INSERT OR IGNORE INTO affected SELECT v.target_key,0 FROM archive_versions v JOIN archive_assets a ON a.version_id=v.id JOIN replaced r ON r.id=a.location_id WHERE v.active=1')
        # Newly affected shared assets obey the same publish barrier and freshness fence.
        if db.execute('SELECT 1 FROM affected f JOIN target_units u USING(target_key) WHERE f.scanned=0 AND u.publish_phase IN (SELECT value FROM json_each(?)) LIMIT 1',(encoded(sorted(BARRIERS)),)).fetchone():
            raise ValueError('SHARED_ASSET_PUBLISH_BARRIER')
        if db.execute('SELECT 1 FROM affected f JOIN archive_targets t USING(target_key) WHERE f.scanned=0 AND t.updated_at>? LIMIT 1',(started,)).fetchone():
            raise ValueError('SCAN_SUPERSEDED')
        if db.execute('SELECT 1 FROM affected f JOIN target_units u USING(target_key) JOIN tasks t ON t.id=u.task_id LEFT JOIN archive_scan_baselines b ON b.scan_id=? AND b.target_key=u.target_key WHERE f.scanned=0 AND (b.target_key IS NULL OR b.current_revision!=u.current_revision OR b.unit_generation!=u.generation OR b.task_generation!=t.generation) LIMIT 1',(scan_id,)).fetchone():
            raise ValueError('SCAN_AUTHORITY_CHANGED')
        db.execute("UPDATE archive_locations SET state='REPLACED' WHERE id IN (SELECT id FROM replaced)")
        db.execute('UPDATE archive_versions SET active=0 WHERE id IN (SELECT version_id FROM archive_assets WHERE location_id IN (SELECT id FROM replaced)) AND id NOT IN (SELECT id FROM observed)')
        db.execute('DELETE FROM archive_assets WHERE version_id IN (SELECT id FROM observed)')
        db.execute("INSERT OR IGNORE INTO archive_contents SELECT content_id,lower(json_extract(location,'$.sha1')),json_extract(location,'$.size') FROM assets")
        db.execute("INSERT INTO archive_locations SELECT location_id,content_id,json_extract(location,'$.cloud_scope_id'),json_extract(location,'$.path'),'PRESENT',location FROM assets WHERE 1 ON CONFLICT(id) DO UPDATE SET state='PRESENT',data=excluded.data")
        db.execute('INSERT INTO archive_assets SELECT version_id,file_index,location_id,data FROM assets WHERE 1 ON CONFLICT(version_id,file_index,location_id) DO UPDATE SET data=excluded.data')
        db.execute('INSERT OR IGNORE INTO archive_sources SELECT json_digest(source_json(data)),id,source_json(data),? FROM observed',(now,))
        db.execute("CREATE TEMP TABLE current_versions AS SELECT v.target_key,json_group_array(v.data) versions FROM (SELECT v.* FROM archive_versions v WHERE v.active=1 AND v.target_key IN (SELECT target_key FROM affected) AND NOT EXISTS(SELECT 1 FROM archive_assets a JOIN archive_locations l ON l.id=a.location_id WHERE a.version_id=v.id AND l.state!='PRESENT') ORDER BY v.id) v GROUP BY v.target_key")
        db.execute("CREATE TEMP TABLE projections AS SELECT f.target_key, CASE WHEN p.target_key IS NOT NULL THEN 'UNKNOWN' WHEN v.target_key IS NOT NULL THEN 'PRESENT' WHEN f.scanned=0 THEN 'UNKNOWN' ELSE 'MISSING' END state,COALESCE(v.versions,'[]') versions, CASE WHEN p.target_key IS NOT NULL THEN '[\"ABSENCE_PENDING\"]' WHEN v.target_key IS NULL AND f.scanned=0 THEN '[\"REPLACED_REQUIRED_ASSET\"]' ELSE '[]' END diagnostics, CASE WHEN f.scanned=1 THEN absence_extra(t.data,EXISTS(SELECT 1 FROM observed o WHERE o.target_key=f.target_key)) ELSE '{}' END extra FROM affected f LEFT JOIN archive_targets t USING(target_key) LEFT JOIN current_versions v USING(target_key) LEFT JOIN absence_pending p USING(target_key)")
        db.execute('ALTER TABLE projections ADD COLUMN data TEXT')
        db.execute('UPDATE projections SET data=projection(state,versions,diagnostics,extra)')
        db.execute("UPDATE archive_targets SET state=p.state,revision=json_extract(p.data,'$.revision'),data=json_extract(p.data,'$.data'),updated_at=? FROM projections p WHERE archive_targets.target_key=p.target_key",(now,))
        db.execute("UPDATE target_units SET current_facts=json_extract(p.data,'$.facts'),current_revision=current_revision+1 FROM projections p WHERE target_units.target_key=p.target_key AND current_facts IS NOT json_extract(p.data,'$.facts')")
        scan['completed_at']=now
        db.execute("UPDATE archive_scans SET state='COMPLETE',data=? WHERE id=?",(encoded(scan),scan_id))
