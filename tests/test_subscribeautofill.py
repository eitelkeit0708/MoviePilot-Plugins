"""Offline regression tests. MoviePilot adapters are fakes; the plugin code is real.
Run: python -m unittest discover -s tests -v
"""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins.v2/subscribeautofill/__init__.py'


def load_plugin():
    names = ['app', 'app.core', 'app.core.event', 'app.db',
             'app.db.downloadhistory_oper', 'app.db.site_oper', 'app.db.subscribe_oper',
             'app.helper', 'app.helper.rule', 'app.log', 'app.plugins',
             'app.schemas', 'app.schemas.types']
    modules = {n: types.ModuleType(n) for n in names}
    modules['app.core.event'].eventmanager = types.SimpleNamespace(register=lambda _: lambda fn: fn)
    modules['app.core.event'].Event = object
    for module, name in [('downloadhistory_oper', 'DownloadHistoryOper'), ('site_oper', 'SiteOper'),
                         ('subscribe_oper', 'SubscribeOper')]:
        setattr(modules['app.db.' + module], name, Mock)
    modules['app.log'].logger = Mock()
    modules['app.plugins']._PluginBase = type('_PluginBase', (), {})
    modules['app.helper.rule'].RuleHelper = Mock
    modules['app.schemas.types'].EventType = types.SimpleNamespace(DownloadAdded='DownloadAdded')
    modules['app.schemas.types'].SystemConfigKey = types.SimpleNamespace(
        RssSites='RssSites', SubscribeFilterRuleGroups='SubscribeFilterRuleGroups',
        BestVersionFilterRuleGroups='BestVersionFilterRuleGroups')
    spec = importlib.util.spec_from_file_location('tested_subscribeautofill', PLUGIN)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


MOD = load_plugin()
TITLE = 'Crew Girl S01 2160p NF WEB-DL DDP5.1 Atmos H.264-MWeb'
DETAILS = ['资源质量', '分辨率', '视觉特效', '音频特效', '视频源', '制作组', '站点']


def make_fixture(**changes):
    plugin = MOD.SubscribeAutofill()
    store = {}
    plugin.get_data = lambda key: copy.deepcopy(store.get(key))
    plugin.save_data = lambda key, value: store.__setitem__(key, copy.deepcopy(value))
    plugin.del_data = lambda key: store.pop(key, None)
    plugin.update_config = Mock()
    system = {'RssSites': [1], 'SubscribeFilterRuleGroups': ['前置过滤', '欧美剧'],
              'BestVersionFilterRuleGroups': ['洗版']}
    plugin.systemconfig = types.SimpleNamespace(get=lambda k: copy.deepcopy(system.get(k)))
    plugin.init_plugin({'enabled': True, 'update_details': DETAILS,
                        'source_patterns': 'CR|Crunchyroll\nNetflix|NF', **changes})
    sub = types.SimpleNamespace(id=7, name='示例电视剧', type='电视剧', tmdbid=101,
        season=1, resolution=None, quality=None, effect=None, include=None, exclude=None,
        sites=[], best_version=0, filter_groups=[], media_category=None)
    torrent = types.SimpleNamespace(title=TITLE, site=1, description='内封简繁字幕', labels=['中字'], pri_order=55)
    media = types.SimpleNamespace(type='电视剧', category='欧美剧')
    meta = types.SimpleNamespace(resource_pix='2160p', resource_type='WEB-DL', resource_team='MWeb', begin_season=1)
    context = types.SimpleNamespace(torrent_info=torrent, meta_info=meta, media_info=media)
    event = types.SimpleNamespace(event_data={'hash': 'fakehash', 'context': context})
    history = types.SimpleNamespace(type='电视剧', tmdbid=101, title='示例电视剧', seasons='S01')
    plugin._downloadhistoryoper.get_by_hash.return_value = history
    plugin._subscribeoper.list_by_tmdbid.return_value = [sub]
    plugin._siteoper.list_active.return_value = [types.SimpleNamespace(name='馒头', id=1)]
    groups = [types.SimpleNamespace(name='前置过滤', media_type='', category='', rule_string='Subtitles'),
              types.SimpleNamespace(name='欧美剧', media_type='电视剧', category='欧美剧', rule_string='Resolution4K'),
              types.SimpleNamespace(name='洗版', media_type='电视剧', category='欧美剧', rule_string='Resolution4K')]
    plugin._rulehelper.get_rule_groups.return_value = groups
    plugin._rulehelper.get_rule_group_by_media.side_effect = lambda media, group_names: [
        g for g in groups if g.name in group_names and (not g.category or g.category == media.category)]
    plugin.chain = types.SimpleNamespace(filter_torrents=Mock(side_effect=lambda **kw: [
        t for t in kw['torrent_list'] if '2160p' in t.title]))
    return types.SimpleNamespace(p=plugin, sub=sub, torrent=torrent, meta=meta, media=media,
                                 event=event, history=history, system=system, groups=groups, store=store)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.f = make_fixture()
        self.p = self.f.p

    def source(self, title):
        return self.p._SubscribeAutofill__extract_source_from_title(title)

    def test_crew_is_not_cr(self):
        self.assertEqual(self.source(TITLE), 'NF')

    def test_long_alias_backtracks(self):
        self.assertEqual(self.source('Show Crunchyroll WEB-DL'), 'Crunchyroll')

    def test_short_code_boundaries(self):
        for title in ['Crew Girl WEB-DL', 'NFoo WEB-DL', 'fooNF WEB-DL', 'NF7 WEB-DL']:
            with self.subTest(title=title):
                self.assertIsNone(self.source(title))

    def test_case_and_underscore(self):
        self.assertEqual(self.source('Show_nf_WEB-DL'), 'nf')

    def test_custom_inline_flags(self):
        self.p._parsed_sources = [self.p._SubscribeAutofill__normalize_source_pattern('(?i)CR|Crunchyroll')]
        self.assertEqual(self.source('Crunchyroll'), 'Crunchyroll')

    def test_invalid_pattern_ignored(self):
        self.p._parsed_sources = ['(', self.p._SubscribeAutofill__normalize_source_pattern('NF')]
        self.assertEqual(self.source(TITLE), 'NF')

    def test_longest_platform(self):
        self.p._parsed_sources = [self.p._SubscribeAutofill__normalize_source_pattern(x) for x in ['HBO', 'HBO[ .]*Max', 'Netflix|NF']]
        self.assertEqual(self.source('HBO Max WEB-DL'), 'HBO Max')

    def test_group_compound(self):
        get = self.p._SubscribeAutofill__extract_group_from_title
        for text, expected in [('Show.1080p-AnimS@ADWeb[中字]', 'AnimS@ADWeb'),
                               ('Show.1080p-M-Team', 'M-Team'), ('Show.1080p-VCB-Studio.mkv', 'VCB-Studio'),
                               ('Show.1080p-6Audios', ''), ('Show.1080p-ADWeb', 'ADWeb')]:
            self.assertEqual(get(text), expected)

    def test_audio_literal_spacing(self):
        expr = self.p._SubscribeAutofill__escape_regex('DDP5.1 Atmos')
        for text in ['DDP5.1 Atmos', 'DDP 5.1.Atmos', 'DDP_5_1__Atmos']:
            self.assertIsNotNone(re.fullmatch(expr, text, re.I))
        self.assertIsNone(re.fullmatch(expr, 'DDP7.1 Atmos', re.I))

    def test_parse_resolution_aliases(self):
        get = self.p._SubscribeAutofill__parse_pix
        self.assertEqual(get('1080i'), '1080[pi]|x1080')
        self.assertEqual(get('2160p'), '4K|2160p|x2160')

    def test_legacy_generated_include_has_boundaries(self):
        self.p._respect_rules = False
        self.p.download_notice(self.f.event)
        data = self.p._subscribeoper.update.call_args.args[1]
        expr = data['include']
        self.assertTrue(re.search(expr, TITLE, re.I))
        self.assertFalse(re.search(expr, TITLE.replace(' NF ', ' Nfoo '), re.I))
        self.assertFalse(re.search(expr, TITLE.replace('-MWeb', '-AilMWeb'), re.I))
        self.assertNotIn('(?=.*Cr)', expr)

    def test_legacy_spacing_alternative(self):
        self.p._respect_rules = False
        self.p.download_notice(self.f.event)
        expr = self.p._subscribeoper.update.call_args.args[1]['include']
        self.assertTrue(re.search(expr, TITLE.replace('DDP5.1 Atmos', 'DDP 5.1.Atmos'), re.I))


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.f = make_fixture()
        self.p = self.f.p

    def run_event(self):
        self.p.download_notice(self.f.event)
        return self.p._subscribeoper.update.call_args.args[1] if self.p._subscribeoper.update.called else None

    def test_default_protection(self):
        self.assertTrue(self.p._respect_rules)
        fields = self.run_event()
        self.assertEqual(set(fields), {'include', 'sites'})
        self.assertNotIn('DDP', fields['include'])
        self.assertNotIn('NF', fields['include'])
        self.assertTrue(re.search(fields['include'], TITLE.replace('DDP5.1 Atmos', 'TrueHD7.1 Atmos'), re.I))

    def test_native_recheck_rejects_1080(self):
        self.f.torrent.title = TITLE.replace('2160p', '1080p')
        self.f.meta.resource_pix = '1080p'
        self.assertIsNone(self.run_event())
        self.assertNotIn('history_handle', self.f.store)

    def test_no_mutation_of_original_torrent(self):
        def check(**kw):
            kw['torrent_list'][0].pri_order = 1
            return kw['torrent_list']
        self.p.chain.filter_torrents.side_effect = check
        self.run_event()
        self.assertEqual(self.f.torrent.pri_order, 55)

    def test_manual_fields_not_overwritten(self):
        self.p._override_mode = True
        self.f.sub.resolution = '4K'
        self.f.sub.include = 'manual'
        self.f.sub.effect = 'HDR'
        fields = self.run_event()
        self.assertEqual(fields, {'sites': [1]})
        self.assertEqual(self.f.sub.include, 'manual')

    def test_global_ordinary_fallback(self):
        self.run_event()
        self.assertEqual(self.p.chain.filter_torrents.call_args.kwargs['rule_groups'], ['前置过滤', '欧美剧'])

    def test_individual_rules_take_precedence(self):
        self.f.sub.filter_groups = ['欧美剧']
        self.run_event()
        self.assertEqual(self.p.chain.filter_torrents.call_args.kwargs['rule_groups'], ['欧美剧'])

    def test_best_version_uses_own_global(self):
        self.f.sub.best_version = 1
        self.run_event()
        self.assertEqual(self.p.chain.filter_torrents.call_args.kwargs['rule_groups'], ['洗版'])

    def test_string_zero_is_not_best_version(self):
        self.f.sub.best_version = '0'
        self.run_event()
        self.assertEqual(self.p.chain.filter_torrents.call_args.kwargs['rule_groups'], ['前置过滤', '欧美剧'])

    def test_stale_group_ref_is_not_global_fallback(self):
        self.f.sub.filter_groups = ['removed-group']
        self.assertIsNone(self.run_event())
        self.p.chain.filter_torrents.assert_not_called()

    def test_one_stale_ref_blocks_fill(self):
        self.f.sub.filter_groups = ['前置过滤', 'removed-group']
        self.assertIsNone(self.run_event())

    def test_missing_category_blocks_fill(self):
        self.f.media.category = None
        self.assertIsNone(self.run_event())

    def test_only_prefilter_applicable_blocks_fill(self):
        self.f.media.category = '未分类'
        self.assertIsNone(self.run_event())

    def test_custom_media_category_used_on_copy(self):
        self.f.media.category = None
        self.f.sub.media_category = '欧美剧'
        self.assertIsNotNone(self.run_event())
        self.assertIsNone(self.f.media.category)

    def test_guard_exception_blocks_fill(self):
        self.p.chain.filter_torrents.side_effect = RuntimeError('test')
        self.assertIsNone(self.run_event())

    def test_no_rule_configuration_keeps_selected_legacy_locks(self):
        self.f.system['SubscribeFilterRuleGroups'] = []
        fields = self.run_event()
        self.assertIn('resolution', fields)
        self.assertIn('quality', fields)
        self.p.chain.filter_torrents.assert_not_called()

    def test_explicit_disable_keeps_legacy_mode(self):
        self.p._respect_rules = False
        self.f.torrent.title = TITLE.replace('2160p', '1080p')
        self.f.meta.resource_pix = '1080p'
        self.assertEqual(self.run_event()['resolution'], '1080[pi]|x1080')

    def test_legacy_override_can_clear_effect(self):
        self.p._respect_rules = False
        self.p._override_mode = True
        self.f.sub.effect = 'DV'
        self.f.sub.include = 'manual'
        self.assertIsNone(self.run_event()['effect'])

    def test_protection_does_not_apply_user_audio_selection(self):
        self.p._update_details = ['分辨率', '音频特效', '视觉特效', '资源质量', '视频源']
        self.assertIsNone(self.run_event())

    def test_corrupt_group_value_fails_closed(self):
        self.f.sub.filter_groups = 'not JSON'
        self.assertIsNone(self.run_event())

    def test_global_groups_as_json_supported(self):
        self.f.system['SubscribeFilterRuleGroups'] = '["前置过滤", "欧美剧"]'
        self.assertIsNotNone(self.run_event())

    def test_missing_media_blocks_fill(self):
        self.f.event.event_data['context'].media_info = None
        self.assertIsNone(self.run_event())

    def test_existing_quality_not_replaced_even_legacy(self):
        self.p._respect_rules = False
        self.f.sub.quality = 'Remux'
        fields = self.run_event()
        self.assertNotIn('quality', fields)

    def test_no_download_or_rule_config_write_api(self):
        self.run_event()
        self.p.update_config.assert_not_called()
        self.assertNotIn('filter_groups', self.p._subscribeoper.update.call_args.args[1])


    def test_empty_native_result_blocks_fill(self):
        self.p.chain.filter_torrents.side_effect = None
        self.p.chain.filter_torrents.return_value = None
        self.assertIsNone(self.run_event())

    def test_empty_applicable_rule_blocks_fill(self):
        self.f.groups[1].rule_string = ''
        self.assertIsNone(self.run_event())

    def test_global_list_not_modified(self):
        snapshot = copy.deepcopy(self.f.system)
        self.run_event()
        self.assertEqual(self.f.system, snapshot)

    def test_single_rules_precede_best_version(self):
        self.f.sub.best_version = 1
        self.f.sub.filter_groups = ['欧美剧']
        self.run_event()
        self.assertEqual(self.p.chain.filter_torrents.call_args.kwargs['rule_groups'], ['欧美剧'])


class EventTests(unittest.TestCase):
    def setUp(self):
        self.f = make_fixture()
        self.p = self.f.p

    def test_disabled(self):
        self.p._enabled = False
        self.p.download_notice(self.f.event)
        self.p._subscribeoper.update.assert_not_called()

    def test_empty_event(self):
        self.p.download_notice(None)
        self.p.download_notice(types.SimpleNamespace(event_data={}))
        self.p._subscribeoper.update.assert_not_called()

    def test_missing_meta_is_safe(self):
        self.f.event.event_data['context'].meta_info = None
        self.p.download_notice(self.f.event)
        self.assertTrue(self.p._subscribeoper.update.called)

    def test_no_movie_updates(self):
        self.f.history.type = '电影'
        self.p.download_notice(self.f.event)
        self.p._subscribeoper.update.assert_not_called()

    def test_missing_tmdb_id(self):
        self.f.history.tmdbid = None
        self.p.download_notice(self.f.event)
        self.p._subscribeoper.update.assert_not_called()

    def test_multiple_seasons_not_applied_to_all(self):
        self.f.history.seasons = 'S01-S03'
        self.p.download_notice(self.f.event)
        self.p._subscribeoper.update.assert_not_called()

    def test_duplicate_event_updates_once(self):
        self.p.download_notice(self.f.event)
        self.p.download_notice(self.f.event)
        self.assertEqual(self.p._subscribeoper.update.call_count, 1)

    def test_next_season_is_independent(self):
        self.p.download_notice(self.f.event)
        self.f.history.seasons = 'S02'
        self.f.sub.season = 2
        self.f.sub.id = 8
        self.p.download_notice(self.f.event)
        self.assertEqual(self.p._subscribeoper.update.call_count, 2)

    def test_legacy_showwide_key_does_not_block_another_season(self):
        self.f.store['history_handle'] = ['电视剧:101']
        self.p.download_notice(self.f.event)
        self.assertTrue(self.p._subscribeoper.update.called)

    def test_site_not_in_rss_is_not_added(self):
        self.f.system['RssSites'] = [2]
        self.p.download_notice(self.f.event)
        self.assertNotIn('sites', self.p._subscribeoper.update.call_args.args[1])

    def test_sites_string_ids_normalized(self):
        self.f.system['RssSites'] = ['1']
        self.p.download_notice(self.f.event)
        self.assertEqual(self.p._subscribeoper.update.call_args.args[1]['sites'], [1])

    def test_user_site_selection_kept(self):
        self.f.sub.sites = [2]
        self.p.download_notice(self.f.event)
        self.assertNotIn('sites', self.p._subscribeoper.update.call_args.args[1])

    def test_form_has_protect_switch(self):
        form, defaults = self.p.get_form()
        self.assertTrue(defaults['respect_rules'])
        self.assertIn('respect_rules', json.dumps(form))

    def test_reinitializing_none_disables(self):
        self.p.init_plugin(None)
        self.assertFalse(self.p.get_state())

    def test_history_is_recorded(self):
        self.p.download_notice(self.f.event)
        item = self.f.store['history'][-1]
        self.assertEqual(item['name'], '示例电视剧')
        self.assertIn('sites', json.loads(item['content']))
        self.assertIn('before', item)
        self.assertIsInstance(self.p.get_page(), list)

    def test_effects_functions_still_available(self):
        get_audio = self.p._SubscribeAutofill__extract_audio_effects_from_title
        get_visual = self.p._SubscribeAutofill__extract_visual_effects_from_title
        self.assertIn('DDP5.1 Atmos', get_audio(TITLE))
        self.assertIn('DV', get_visual(TITLE + ' DV HDR'))


class PackagingTests(unittest.TestCase):
    def test_manifest_version(self):
        manifest = json.loads((ROOT / 'package.v2.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['SubscribeAutofill']['version'], MOD.SubscribeAutofill.plugin_version)
        self.assertIn('v3.18', manifest['SubscribeAutofill']['history'])

    def test_source_remains_download_after_hook(self):
        source = PLUGIN.read_text(encoding='utf-8')
        self.assertIn('@eventmanager.register(EventType.DownloadAdded)', source)
        self.assertNotIn('self.chain.download', source)
        self.assertNotIn("update['filter_groups']", source)

if __name__ == '__main__':
    unittest.main()
