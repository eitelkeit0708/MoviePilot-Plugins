import {canonical} from './legacy.mjs';

const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const same = (a, b) => a === undefined || b === undefined ? a === b : canonical(a) === canonical(b);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keyed = value => Array.isArray(value) && value.every(row => object(row) && typeof row.id === 'string') && new Set(value.map(row => row.id)).size === value.length;

// Three-way edits: absence means deletion. Arrays with stable IDs merge by item;
// ordered lists (quality dimensions, priorities) remain one indivisible value.
export function mergeDraft(base, draft, saved, roots = Object.keys(draft), {keepLocal = false} = {}) {
  const conflicts = [], changed = [];
  function merge(before, local, remote, path) {
    if (same(before, local)) return copy(remote);
    if (same(before, remote) || same(local, remote)) { changed.push(path); return copy(local); }
    if (object(before) && object(local) && object(remote)) {
      const result = {};
      for (const key of new Set([...Object.keys(before), ...Object.keys(local), ...Object.keys(remote)])) {
        const value = merge(before[key], local[key], remote[key], [...path, key]);
        if (value !== undefined) result[key] = value;
      }
      return result;
    }
    if (keyed(before) && keyed(local) && keyed(remote)) {
      const rows = values => Object.fromEntries(values.map(row => [row.id, row]));
      const result = merge(rows(before), rows(local), rows(remote), path);
      return [...new Set([...remote.map(row => row.id), ...local.map(row => row.id)])].filter(id => result[id] !== undefined).map(id => result[id]);
    }
    conflicts.push(path);
    return copy(keepLocal ? local : remote);
  }
  const config = copy(saved);
  for (const root of roots.filter(key => key !== 'configuration_receipt')) {
    const value = merge(base[root], draft[root], saved[root], [root]);
    if (value === undefined) delete config[root]; else config[root] = value;
  }
  return {config, conflicts, changed};
}

// A scheme owns one destination and edits its referenced shared objects explicitly.
// Other scheme drafts remain in the editor but cannot ride along with this save.
export function planDraft(base, draft, id, edited = {}) {
  const result=copy(base),plans=[base,draft].map(c=>c.destination_templates.find(p=>p.id===id)).filter(Boolean);
  const rules=new Set(plans.map(p=>p.organized_rule).filter(Boolean));
  const scopes=new Set([base,draft].flatMap(c=>c.delivery.rules.filter(r=>rules.has(r.id)).map(r=>r.cloud_scope_id)));
  const categories=new Set(plans.map(p=>p.category_id).filter(Boolean));
  const owned=key=>new Set(edited[key]||[]);
  const references=(config,key,value)=>config.destination_templates.filter(plan=>{
    if(key==='category')return plan.category_id===value;
    const rule=config.delivery.rules.find(row=>row.id===plan.organized_rule);
    return key==='rule'?plan.organized_rule===value:rule?.cloud_scope_id===value;
  }).length;
  const exclusive=(key,value)=>Math.max(references(base,key,value),references(draft,key,value))<=1;
  const selectedRules=new Set([...rules].filter(value=>exclusive('rule',value)||owned('rules').has(value)));
  const selectedScopes=new Set([...scopes].filter(value=>exclusive('scope',value)||owned('scopes').has(value)));
  const selectedCategories=new Set([...categories].filter(value=>exclusive('category',value)||owned('categories').has(value)));
  const mappingIds=owned('mappings');
  for(const config of [base,draft])for(const row of config.delivery.mappings)
    if(scopes.has(row.cloud_scope_id)&&exclusive('scope',row.cloud_scope_id))mappingIds.add(row.id);
  const maps=[base,draft].flatMap(c=>c.delivery.mappings.filter(m=>mappingIds.has(m.id)));
  const replaceRows=(before,after,selected)=>{
    const replacements=new Map(after.filter(selected).map(row=>[row.id,row]));
    return [...before.flatMap(row=>!selected(row)?[row]:replacements.has(row.id)?[replacements.get(row.id)]:[]),...after.filter(row=>selected(row)&&!before.some(old=>old.id===row.id))].map(copy);
  };
  const replaceKeys=(before,after,keys)=>{const value=copy(before);for(const key of keys){if(Object.hasOwn(after,key))value[key]=copy(after[key]);else delete value[key]}return value};
  result.destination_templates=replaceRows(base.destination_templates,draft.destination_templates,p=>p.id===id);
  result.delivery.rules=replaceRows(base.delivery.rules,draft.delivery.rules,r=>selectedRules.has(r.id));
  result.delivery.cloud_scopes=replaceKeys(base.delivery.cloud_scopes,draft.delivery.cloud_scopes,selectedScopes);
  result.delivery.mappings=replaceRows(base.delivery.mappings,draft.delivery.mappings,m=>mappingIds.has(m.id));
  result.policy.bindings=replaceKeys(base.policy.bindings,draft.policy.bindings,selectedCategories);
  result.delivery.policy_bindings=replaceKeys(base.delivery.policy_bindings,draft.delivery.policy_bindings,selectedCategories);
  result.policy.classification_revision=draft.policy.classification_revision;
  result.delivery.classification_revision=draft.delivery.classification_revision;
  for(const service of new Set(maps.map(m=>m.emby_service).filter(Boolean))){
    const ids=new Set(maps.filter(m=>m.emby_service===service).map(m=>m.library_id));
    const values=[...(base.delivery.libraries[service]||[]).filter(id=>!ids.has(id)),...(draft.delivery.libraries[service]||[]).filter(id=>ids.has(id))];
    if(values.length)result.delivery.libraries[service]=values;else delete result.delivery.libraries[service];
  }
  return result;
}

export const settingScopes = {
  ownership: ['enabled', 'dry_run', 'auto_types', 'passive_libraries', 'lifecycle'],
  candidates: ['candidates', 'schedule'],
  ai: ['ai_assist', 'enhance_host_meta', 'meta_protected_names'],
  recovery: ['recovery', 'delivery'],
  safety: ['permissions', 'safety'],
  policy: ['policy'],
  plans: ['destination_templates', 'policy', 'delivery'],
  discovery: ['discovery', 'history_view'],
};
