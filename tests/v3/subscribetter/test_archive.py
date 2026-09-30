"""W06 real SQLite/path/policy checks with offline SDK boundary doubles."""
import copy
from enum import Enum
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import test_planner as tp


class Sources:
    def __init__(self):
        self.items = []
        self.cloud = {}
        self.fail_at = None
        self.calls = []

    def emby_page(self, service, library, start, limit):
        self.calls.append((service, library, start, limit))
        if start == self.fail_at:
            raise ValueError('EMBY_HTTP_401')
        return {'Items': copy.deepcopy(self.items[start:start + limit]), 'TotalRecordCount': len(self.items)}

    def cloud_stat(self, scope, path, *, refresh=False, timeout=20):
        value = self.cloud.get((scope, path))
        if value is None:
            raise ValueError('CLOUD_NOT_FOUND')
        return dict(value, cloud_scope_id=scope, path=path)

    def emby_item(self, service, library, item_id):
        return copy.deepcopy(next(i for i in self.items if i['Id'] == item_id))

    def classify_target(self, key):
        return {'state': 'complete', 'policy_revision': 1, 'effective': {'category_id': 'movie', 'category_path': [], 'rule_id': 'r', 'source': 'policy'}}


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = tp.load('repository')
        cls.p = tp.load('policy')
        cls.a = tp.load('planner')
        cls.s = tp.load('scheduler')
        if (tp.PLUGIN / 'archive.py').exists():
            cls.m = tp.load('archive')

    def setUp(self):
        self.assertTrue((tp.PLUGIN / 'archive.py').exists(), 'W06 archive missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.strms = self.root / 'strms'
        self.strms.mkdir()
        self.repo = self.r.Repository(self.root / 'state.db')
        self.policy = self.p.Policy({'movie': '外语电影'}, 1)
        self.sources = Sources()
        self.mapping = dict(id='movies', revision='1', emby_service='test', library_id='10',
                            emby_prefix='/Emby/Movies', local_strm_prefix=str(self.strms),
                            cloud_scope_id='cloud', playback_prefix='/Cloud/115/media',
                            cd2_prefix='/115/media', max_strm_bytes=1024)
        self.archive = self.m.Archive(self.repo, self.policy, self.sources, mappings=[self.mapping])
        self.key = self.a.TargetUnit(self.r.Target('电影', 'themoviedb', '42')).key
        self.item = self.media('old', '中文 {电影}.strm', 'a' * 40)

    def media(self, item_id, name, sha1, height=2160):
        cloud = '/115/media/' + name.removesuffix('.strm') + '.mkv'
        (self.strms / name).write_text('\ufeff/Cloud' + cloud + '\n', encoding='utf-8')
        self.sources.cloud['cloud', cloud] = dict(sha1=sha1, size=100, cd2_id=item_id, p115_id='')
        item = dict(Id=item_id, Type='Movie', Path='/Emby/Movies/' + name, ProviderIds={'Tmdb': '42'},
                    Name='Fiction', MediaSources=[dict(Id='source-' + item_id, Path='/Emby/Movies/' + name,
                    MediaStreams=[dict(Type='Video', Height=height, Width=3840 if height == 2160 else 1920, Codec='hevc', VideoRange='SDR'),
                                  dict(Type='Audio', Codec='aac', Language='eng')])])
        self.sources.items.append(item)
        return item

    def test_draft_sample_uses_saved_read_root_and_checks_all_four_paths(self):
        from unittest.mock import patch
        mapping={**self.mapping,'max_strm_bytes':1024}
        with patch.object(self.m,'read_strm',side_effect=AssertionError('must not read an unsaved root')):
            result=self.m.draft_mapping_check(mapping,self.item,[])
            self.assertEqual('READ_SCOPE_REQUIRED',result['state'])
            self.assertIsNone(result['locations'][0]['content_path'])
        result=self.m.draft_mapping_check(mapping,self.item,[mapping])
        self.assertEqual('DRAFT_MAPPING_VERIFIED',result['state'])
        row=result['locations'][0]
        self.assertEqual('/Cloud/115/media/中文 {电影}.mkv',row['content_path'])
        self.assertEqual('/115/media/中文 {电影}.mkv',row['cd2_path'])
        self.assertFalse(result['cloud_file_verified'])
        with patch.object(self.m,'read_strm',side_effect=AssertionError('must not read outside saved root')):
            self.assertEqual('READ_SCOPE_REQUIRED',self.m.draft_mapping_check({**mapping,'local_strm_prefix':str(self.root)},self.item,[mapping])['state'])
            self.assertEqual('READ_SCOPE_REQUIRED',self.m.draft_mapping_check(mapping,self.item,[{**mapping,'library_id':'other'}])['state'])
        with self.assertRaisesRegex(ValueError,'NO_MAPPING'):
            self.m.draft_mapping_check({**mapping,'playback_prefix':'/incorrect'},self.item,[mapping])
        with self.assertRaises(ValueError):
            self.m.draft_mapping_check(mapping,{**self.item,'Path':'/Emby/Movies/../secret.strm','MediaSources':[]},[mapping])
        with self.assertRaisesRegex(ValueError,'MEDIA_SOURCE_LIMIT'):
            self.m.draft_mapping_check(mapping,{**self.item,'MediaSources':self.item['MediaSources']*21},[mapping])

    def test_audio_title_declarations_survive_projection_without_claiming_probe(self):
        for codec, title, expected in (
                ('eac3', 'English [Dolby Digital Plus with Dolby Atmos 5.1]', 2),
                ('eac3', '', 1), ('eac3', 'Atmosphere', 1),
                ('aac', 'Dolby Atmos', 0), ('truehd', 'Dolby Atmos', 3),
                ('dts', 'DTS:X', 2)):
            with self.subTest(codec=codec, title=title):
                item = copy.deepcopy(self.item)
                streams = item['MediaSources'][0]['MediaStreams']
                streams[0]['Title'] = 'Atmos'
                streams[1].update(Codec=codec, Profile=None, Title=title)
                observed = self.archive.resolve_item('test', '10', item)[0]
                self.assertEqual(expected, self.policy.normalize(observed['raw'], current=True).audio)
                if expected == 2:
                    self.assertEqual('declaration', observed['raw']['audio_evidence']['kind'])
                    self.assertEqual('Title', observed['raw']['audio_evidence']['field'])
                    self.assertEqual(title, observed['streams'][1]['Title'])

    def test_measured_dolby_profile_and_hdr_format_survive_archive_projection(self):
        for profile,video_range,expected in [(5,'DOVI','dv_p5'),(7,'DOVI','dv_p7'),(8,'DOVI','dv_p8'),(None,'HDR10Plus','hdr10plus'),(None,'HDRVivid','hdr_vivid'),(None,'CUVA','hdr_vivid'),(None,'Dolby Vision','dv')]:
            item=copy.deepcopy(self.item)
            video=item['MediaSources'][0]['MediaStreams'][0]
            video.update(DvProfile=profile,VideoRangeType=video_range)
            projected=self.m.item_projection(item)
            raw,_=self.m.stream_facts(projected,projected['MediaSources'][0])
            facts=self.policy.normalize(raw,current=True)
            self.assertEqual(expected,facts.picture_format)
            self.assertFalse(facts.errors)

    def test_audio_profile_uses_the_same_codec_and_token_guards(self):
        for codec, profile, expected in (('eac3', 'Dolby Atmos', 2), ('eac3', 'Atmospheric', 1), ('aac', 'Atmos', 0)):
            with self.subTest(codec=codec, profile=profile):
                item = copy.deepcopy(self.item)
                item['MediaSources'][0]['MediaStreams'][1].update(Codec=codec, Profile=profile)
                observed = self.archive.resolve_item('test', '10', item)[0]
                self.assertEqual(expected, observed['raw']['technical']['audio'])

    def test_audio_projection_keeps_spatial_and_lossless_evidence_on_the_same_track(self):
        cases=[([dict(Codec='truehd',Title='Dolby Atmos')],3,'truehd_atmos'),
               ([dict(Codec='truehd'),dict(Codec='eac3',Title='Dolby Atmos')],3,'truehd'),
               ([dict(Codec='eac3',Title='Dolby Atmos')],2,'ddp_atmos'),
               ([dict(Codec='dts-hd',Profile='DTS-HD MA',Title='DTS:X')],3,'dtshdma_x'),
               ([dict(Codec='dts-hd',Title='DTS:X')],2,'dtsx'),
               ([dict(Codec='dts',Profile='DTS-HD MA')],3,'dtshdma'),
               ([dict(Codec='dts-hd',Profile='DTS-HD HRA')],0,'dtshdhra'),
               ([dict(Codec='dts-hd')],None,None),
               ([dict(Codec='aac'),dict(Codec='dts-hd')],None,None),
               ([dict(Codec='truehd'),dict(Codec='dts',Title='DTS:X')],3,'lossless')]
        for tracks,family,option in cases:
            with self.subTest(tracks=tracks):
                item=copy.deepcopy(self.item);source=item['MediaSources'][0]
                source['MediaStreams']=source['MediaStreams'][:1]+[dict(Type='Audio',**track) for track in tracks]
                raw,_=self.m.stream_facts(item,source)
                facts=self.policy.normalize(raw,current=True)
                self.assertEqual((family,option),(facts.audio,facts.audio_format))
                self.assertFalse(facts.errors)
                if option in ('truehd_atmos','ddp_atmos','dtshdma_x','dtsx'):
                    self.assertEqual('declaration',raw['audio_evidence']['kind'])

    def test_scoped_two_stage_unicode_and_changed_target_with_identical_strm(self):
        first = self.archive.resolve_item('test', '10', self.item)[0]
        self.assertEqual('/115/media/中文 {电影}.mkv', first['video']['path'])
        self.assertEqual('a' * 40, first['video']['sha1'])
        self.sources.cloud['cloud', first['video']['path']]['sha1'] = 'b' * 40
        second = self.archive.resolve_item('test', '10', self.item)[0]
        self.assertNotEqual(first['version_id'], second['version_id'])
        self.assertEqual('b' * 40, second['video']['sha1'])
        with self.assertRaisesRegex(ValueError, 'OUTSIDE_LIBRARY'):
            self.archive.resolve_item('other', '10', self.item)

    def test_embedded_attachment_paths_do_not_block_valid_media_facts(self):
        item = copy.deepcopy(self.item)
        media = item['MediaSources'][0]['MediaStreams']
        for stream in media:
            stream['Path'] = None
        media.append(dict(Type='Subtitle', Codec='subrip', Language='zho', Path='/Emby/Movies/movie.zh.srt', IsExternal=True))
        fonts = [dict(Type='Attachment', Codec='ttf', Path='STKaiti.ttf'),
                 dict(Type='Attachment', Codec='otf', Path='cronospro.otf')]
        media.extend(fonts)
        item['MediaStreams'] = copy.deepcopy(media)
        projected = self.m.item_projection(item)
        self.assertEqual(['Video', 'Audio', 'Subtitle'], [s['Type'] for s in projected['MediaStreams']])
        self.assertEqual(['Video', 'Audio', 'Subtitle'], [s['Type'] for s in projected['MediaSources'][0]['MediaStreams']])
        self.assertEqual(['Video', 'Audio', 'Subtitle'], [s['Type'] for s in self.archive.resolve_item('test', '10', item)[0]['streams']])
        for stream_type in ('Video', 'Audio', 'Subtitle'):
            changed = copy.deepcopy(item)
            stream = next(s for s in changed['MediaSources'][0]['MediaStreams'] if s['Type'] == stream_type)
            stream['Path'] = 'relative/path'
            with self.subTest(stream_type=stream_type), self.assertRaisesRegex(ValueError, 'UNSUPPORTED_TARGET'):
                self.m.item_projection(changed)

    def test_prefix_boundary_multiline_oversize_and_symlink_escape(self):
        bad = copy.deepcopy(self.item)
        bad['Path'] = bad['MediaSources'][0]['Path'] = '/Emby/MoviesElse/file.strm'
        with self.assertRaisesRegex(ValueError, 'NO_MAPPING'):
            self.archive.resolve_item('test', '10', bad)
        path = self.strms / '中文 {电影}.strm'
        for text, reason in [('one\ntwo', 'STRM_MULTILINE'), ('x' * 1025, 'STRM_TOO_LARGE'),
                             ('/Cloud/115/mediaElse/file.mkv', 'NO_MAPPING'), ('https://host/a', 'UNSUPPORTED_TARGET')]:
            path.write_text(text, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, reason):
                self.archive.resolve_item('test', '10', self.item)
        outside = self.root / 'outside.strm'
        outside.write_text('/Cloud/115/media/file.mkv')
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable; Linux gate remains')
        with self.assertRaisesRegex(ValueError, 'PATH_ESCAPE'):
            self.archive.resolve_item('test', '10', self.item)

    def test_current_old_library_independent_item_id_churn_multi_versions(self):
        self.media('second', 'copy.strm', 'b' * 40, height=1080)
        result = self.archive.reconcile('test', '10')
        self.assertEqual('COMPLETE', result['status'])
        before = self.archive.current([self.key])[self.key]
        self.assertEqual('PRESENT', before['state'])
        self.assertEqual({1080, 2160}, {v.facts.resolution for v in before['versions']})
        self.assertIsNone(before['revision'])
        self.sources.items[0]['Id'] = 'reindexed'
        self.sources.items[0]['MediaSources'][0]['Id'] = 'reindexed-source'
        self.archive.reconcile('test', '10')
        after = self.archive.current([self.key])[self.key]
        self.assertEqual({v.version_id for v in before['versions']}, {v.version_id for v in after['versions']})
        self.assertEqual([], self.repo.list_tasks())

    def test_discovery_inventory_projects_only_observed_tv_units(self):
        target=self.r.Target('电视剧','themoviedb','1396',1)
        keys=[self.a.TargetUnit(target,episode).key for episode in (1,3)]
        with self.repo.connection(write=True) as db:
            for key in keys:
                db.execute("INSERT INTO archive_targets VALUES(?,'UNKNOWN','',?,?)",
                           (key,json.dumps({'mapping_revision':self.archive.mappings.revision}),self.r.utcnow()))
        seen=[]
        def current(requested):
            seen.extend(requested)
            return {key:{'state':'PRESENT','evidence_ref':'archive:'+str(index),'diagnostics':[]}
                    for index,key in enumerate(requested)}
        with patch.object(self.archive,'current',side_effect=current):
            value=self.archive.discovery_inventory(target)
        self.assertEqual(keys,seen)
        self.assertEqual('PARTIAL',value['state'])
        self.assertEqual(['SEASON_COMPLETENESS_UNPROVEN'],value['diagnostics'])
        absent=self.archive.discovery_inventory(self.r.Target('电视剧','themoviedb','1396',2))
        self.assertEqual('UNKNOWN',absent['state'])
        self.assertEqual(['UNOBSERVED_SEASON'],absent['diagnostics'])

    def test_incomplete_scan_keeps_versions_error_never_missing_then_confirmed_absence(self):
        self.media('second', 'copy.strm', 'b' * 40)
        self.archive.reconcile('test', '10')
        self.sources.fail_at = 1
        result = self.archive.reconcile('test', '10', limits={'page_size': 1, 'pages': 2})
        self.assertEqual('ERROR', result['status'])
        current = self.archive.current([self.key])[self.key]
        self.assertEqual('ERROR', current['state'])
        self.assertEqual(2, len(current['versions']))
        self.sources.fail_at = None
        self.sources.items = []
        self.archive.reconcile('test', '10')
        self.assertEqual('UNKNOWN', self.archive.current([self.key])[self.key]['state'])
        self.archive.reconcile('test', '10')
        self.assertEqual('MISSING', self.archive.current([self.key])[self.key]['state'])

    def test_paginated_restart_resumes_and_mapping_change_invalidates(self):
        self.media('second', 'copy.strm', 'b' * 40)
        first = self.archive.reconcile('test', '10', limits={'page_size': 1, 'pages': 1, 'items': 1})
        self.assertEqual('INCOMPLETE', first['status'])
        restarted = self.m.Archive(self.repo, self.policy, self.sources, mappings=[self.mapping])
        result = restarted.reconcile('test', '10', scan_id=first['scan_id'], limits={'page_size': 1, 'pages': 2, 'items': 10})
        self.assertEqual('COMPLETE', result['status'])
        changed = self.m.Archive(self.repo, self.policy, self.sources, mappings=[dict(self.mapping, revision='2')])
        self.assertEqual('UNKNOWN', changed.current([self.key])[self.key]['state'])

    def test_scan_requires_readable_mount_and_commit_snapshot(self):
        observation = self.archive.resolve_item('test', '10', self.item)[0]
        (self.strms / '中文 {电影}.strm').write_text('/Cloud/115/media/replaced.mkv')
        with self.assertRaisesRegex(ValueError, 'STRM_CHANGED'):
            self.archive.validate_observation(observation)
        self.sources.items = []
        (self.strms / '中文 {电影}.strm').unlink()
        self.strms.rmdir()
        result = self.archive.reconcile('test', '10')
        self.assertEqual('ERROR', result['status'])

    def test_strm_changed_after_finalize_before_publish_is_not_committed(self):
        scan_module = tp.load('archive_scan')
        original = scan_module.publish
        replacement = '/115/media/replaced.mkv'

        def replace(*args, **kwargs):
            (self.strms / '中文 {电影}.strm').write_text('/Cloud' + replacement + '\n', encoding='utf-8')
            self.sources.cloud['cloud', replacement] = dict(sha1='b' * 40, size=100, cd2_id='new', p115_id='')
            return original(*args, **kwargs)

        with patch.object(scan_module, 'publish', side_effect=replace):
            first = self.archive.reconcile('test', '10')
        self.assertEqual('ERROR', first['status'])
        self.assertEqual(['STRM_CHANGED'], first['diagnostics'])
        with self.repo.connection() as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM archive_versions WHERE active=1').fetchone()[0])

        self.assertEqual('COMPLETE', self.archive.reconcile('test', '10')['status'])
        with self.repo.connection() as db:
            active = [json.loads(row[0])['video']['path'] for row in db.execute('SELECT data FROM archive_versions WHERE active=1')]
        self.assertEqual([replacement], active)

    def publication(self, planned_picture=0, *, unknown=False, video_requires=None):
        task = self.repo.submit('task', self.r.Target('电影', 'themoviedb', '42'), {}, 'test', 42, True)
        self.repo.complete_handoff(task['id'], task['generation'])
        self.task = task
        self.s.Scheduler(self.repo).open_opportunity('o', task['id'], [self.a.TargetUnit(self.r.Target.from_task(task))],
            mode='ONESHOT', config=self.s.ScheduleConfig(observation_enabled=False, cooldown_enabled=True, cooldown_seconds=600))
        saved = self.sources.items
        self.sources.items = []
        self.archive.reconcile('test', '10', target_keys=[self.key])
        self.archive.reconcile('test', '10', target_keys=[self.key])
        self.sources.items = saved
        auth = self.archive.authority
        auth.set_revisions(self.policy.semantic_hash, 'parse')
        current = self.archive.current([self.key])[self.key]
        files = [dict(index=0, path='movie.mkv', size=100, role='video', targets=[self.key], requires=[1] if video_requires is None else video_requires),
                 dict(index=1, path='movie.srt', size=5, role='subtitle', targets=[self.key], requires=[])]
        spec = dict(candidate_key='release', infohash='f'*40, downloader='test', save_path='/download',
                    policy_revision=self.policy.semantic_hash, parse_revision='parse', current={self.key:dict(state='MISSING',revision=current['revision'])},
                    targets={self.key:dict(action='ACQUIRE',reason='MISSING',evidence_keys=[],quality=[2160,planned_picture,False,1,False,0],evidence_source='none')},
                    torrent_files=files, selected_indices=[0,1], verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
        auth.prepare('plan','o',spec)
        vector = auth.claim('plan',auth.vector([self.key]))
        auth.set_transfer_phase('plan',vector,'READY_TO_PUBLISH')
        auth.begin_publish('pub','plan',vector,[0,1],validation=dict(policy_revision=self.policy.semantic_hash,parse_revision='parse',
            current_revisions={self.key:current['revision']},checks={self.key:dict(identity=True,admission=True,scope=True,not_excluded=True,current_allows=True,assets_complete=True,remote_verified=True)}))
        video_path='/115/media/中文 {电影}.mkv'
        self.sources.cloud['cloud','/115/media/movie.srt']=dict(sha1='c'*40,size=5,cd2_id='sub',p115_id='')
        self.item['MediaSources'][0]['MediaStreams'].append(dict(Type='Subtitle',Codec='srt',Language='zho',IsExternal=True,Path='/Emby/Movies/movie.srt'))
        self.manifest=dict(plan_id='plan',manifest_ref='manifest',assets=[dict(file_index=i['index'],relative_path=i['path'],role=i['role'],targets=i['targets'],requires=i['requires'],content=dict(sha1=('a' if i['index']==0 else 'c')*40,size=i['size'])) for i in files],
            publication={self.key:dict(raw=dict(title='2160p REMUX 中文字幕',description='',labels=[],group_known=True),classification=self.sources.classify_target(self.key))})
        self.consumer=dict(action_id='pub',settled=True,evidence_ref='consumer',assets=[dict(file_index=0,cloud_scope_id='cloud',path=video_path),dict(file_index=1,cloud_scope_id='cloud',path='/115/media/movie.srt')],emby=[dict(service='test',library_id='10',item_id='old')])
        auth.record_result('pub','UNKNOWN' if unknown else 'HANDED_OFF',dict(asset_manifest=self.manifest,consumer_receipt=self.consumer))
        if unknown:
            with self.repo.connection(write=True) as db:
                db.execute('INSERT INTO delivery_bundles VALUES(?,?,?,?,?,?,?)',('manifest','plan','rule','PUBLISH_OUTCOME_UNKNOWN','',0,json.dumps(dict(manifest=self.manifest,publication_action='pub'))))

    def test_review_i2_unknown_final_proof_confirms_atomically(self):
        self.publication(unknown=True)
        with patch.object(self.archive,'_store_version',side_effect=RuntimeError('rollback')):
            with self.assertRaisesRegex(RuntimeError,'rollback'):
                self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.archive.authority.action('pub')['state'])
        self.assertTrue(self.archive.confirm_ingest('pub',self.manifest,self.consumer)['accepted'])
        self.assertEqual('INGEST_CONFIRMED',self.archive.authority.action('pub')['state'])

    def test_review_i2_missing_final_asset_preserves_unknown(self):
        self.publication(unknown=True)
        self.sources.cloud.pop(('cloud','/115/media/movie.srt'))
        with self.assertRaisesRegex(ValueError,'CLOUD_NOT_FOUND'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.archive.authority.action('pub')['state'])

    def test_review_i2_unknown_requires_original_bound_manifest(self):
        self.publication(unknown=True)
        with self.repo.connection(write=True) as db:db.execute('DELETE FROM delivery_bundles')
        with self.assertRaisesRegex(ValueError,'PUBLICATION_MANIFEST_UNBOUND'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.archive.authority.action('pub')['state'])

    def test_review_i2_altered_manifest_or_missing_durable_receipt_rejected(self):
        self.publication(unknown=True)
        changed=json.loads(json.dumps(self.manifest));changed['assets'][0]['content']['sha1']='f'*40
        self.archive.authority.record_result('pub','UNKNOWN',dict(asset_manifest=changed,consumer_receipt=self.consumer))
        with self.assertRaisesRegex(ValueError,'PUBLICATION_MANIFEST_UNBOUND'):
            self.archive.confirm_ingest('pub',changed,self.consumer)
        changed_consumer=dict(self.consumer,evidence_ref='unrecorded')
        with self.assertRaisesRegex(ValueError,'DURABLE_CONSUMER_RECEIPT_REQUIRED'):
            self.archive.confirm_ingest('pub',self.manifest,changed_consumer)
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.archive.authority.action('pub')['state'])

    def test_review_i2_stale_owner_preserves_unknown(self):
        self.publication(unknown=True)
        self.repo.set_state(self.task['id'],'PAUSED','test')
        result=self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertFalse(result['accepted'])
        self.assertEqual('PUBLISH_OUTCOME_UNKNOWN',self.archive.authority.action('pub')['state'])

    def test_confirm_all_assets_atomically_then_duplicate_never_writes(self):
        self.assertTrue(hasattr(self.archive, 'confirm_ingest'), 'W06 confirmation missing')
        self.publication()
        self.sources.cloud.pop(('cloud','/115/media/movie.srt'))
        with self.assertRaisesRegex(ValueError,'CLOUD_NOT_FOUND'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertIsNone(self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])
        self.sources.cloud['cloud','/115/media/movie.srt']=dict(sha1='c'*40,size=5,cd2_id='sub',p115_id='')
        result=self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertTrue(result['accepted'])
        current=self.archive.current([self.key])[self.key]
        self.assertEqual('PRESENT',current['state'])
        self.assertEqual(1,len(current['versions']))
        self.assertIsNotNone(self.s.Scheduler(self.repo).target(self.key)['cooldown_until'])
        self.assertFalse(self.archive.confirm_ingest('pub',self.manifest,self.consumer)['accepted'])
        self.assertEqual(current['revision'],self.archive.current([self.key])[self.key]['revision'])
        with self.repo.connection() as db:
            self.assertEqual('ARCHIVED',db.execute("SELECT state FROM opportunities WHERE id='o'").fetchone()[0])
            self.assertEqual(2,db.execute('SELECT count(*) FROM archive_assets').fetchone()[0])

    def test_audio_declaration_provenance_survives_ingest_and_rescan(self):
        self.publication()
        for item in self.sources.items:
            item['MediaSources'][0]['MediaStreams'][1].update(Codec='eac3', Profile=None, Title='Dolby Atmos')
        self.assertTrue(self.archive.confirm_ingest('pub', self.manifest, self.consumer)['accepted'])
        for rescanned in (False, True):
            if rescanned:
                self.assertEqual('COMPLETE', self.archive.reconcile('test', '10')['status'])
            current = self.archive.current([self.key])[self.key]
            self.assertEqual(2, current['versions'][0].facts.audio)
            self.assertEqual('declaration', current['versions'][0].facts.raw['audio_evidence']['kind'])

    def test_confirm_rolls_back_receipts_and_clocks_on_archive_failure(self):
        self.assertTrue(hasattr(self.archive, 'confirm_ingest'), 'W06 confirmation missing')
        self.publication()
        with patch.object(self.archive,'_store_version',side_effect=RuntimeError('injected archive failure')):
            with self.assertRaisesRegex(RuntimeError,'injected'):
                self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertIsNone(self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM ingest_receipts').fetchone()[0])
            self.assertEqual('HANDED_OFF',db.execute("SELECT state FROM plan_actions WHERE id='pub'").fetchone()[0])

    def test_stale_ingest_receipt_cannot_promote_archive(self):
        self.assertTrue(hasattr(self.archive, 'confirm_ingest'), 'W06 confirmation missing')
        self.publication()
        self.repo.set_state(self.task['id'],'PAUSED','test')
        result=self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertFalse(result['accepted'])
        self.assertTrue(result['receipt_only'])
        self.assertEqual('MISSING',self.archive.current([self.key])[self.key]['state'])
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM archive_versions').fetchone()[0])

    def test_public_emby_response_falsey_error_and_explicit_parent(self):
        self.assertTrue(hasattr(self.m,'HostArchiveSources'),'W06 SDK adapter missing')
        class Response:
            status_code=401
            closed=False
            def __bool__(self):return False
            def json(self):return {'Items':[],'TotalRecordCount':0}
            def close(self):self.closed=True
        response=Response(); urls=[]
        instance=types.SimpleNamespace(get_data=lambda url: (urls.append(url),response)[1])
        helper=types.SimpleNamespace(get_service=lambda name,type_filter:types.SimpleNamespace(instance=instance))
        with patch.dict('sys.modules',{'app.sdk.services':types.SimpleNamespace(MediaServerHelper=lambda:helper)}):
            sources=self.m.HostArchiveSources(types.SimpleNamespace(),cloud_scopes={},libraries={'test':['10']})
            with self.assertRaisesRegex(ValueError,'EMBY_HTTP_401'):
                sources.emby_page('test','10',0,10)
            self.assertTrue(response.closed)
            self.assertIn('ParentId=10',urls[0])
            with self.assertRaisesRegex(ValueError,'OUTSIDE_LIBRARY'):
                sources.emby_page('test','11',0,10)
            response.status_code=200
            self.assertEqual([],sources.emby_page('test','10',0,10)['Items'])

    def test_emby_inventory_keeps_separate_versions_hidden_by_user_listing(self):
        class Response:
            status_code=200
            def __init__(self, items):self.items=items
            def json(self):return {'Items':self.items,'TotalRecordCount':len(self.items)}
            def close(self):pass
        urls=[]
        def get_data(url):
            urls.append(url)
            assert 'ParentId=10' in url
            ids=['base'] if '/Users/' in url else ['base','high']
            return Response([{'Id':value,'Type':'Movie','ProviderIds':{'Tmdb':'42'}} for value in ids])
        instance=types.SimpleNamespace(get_data=get_data)
        helper=types.SimpleNamespace(get_service=lambda name,type_filter:types.SimpleNamespace(instance=instance))
        with patch.dict('sys.modules',{'app.sdk.services':types.SimpleNamespace(MediaServerHelper=lambda:helper)}):
            sources=self.m.HostArchiveSources(types.SimpleNamespace(),cloud_scopes={},libraries={'test':['10']})
            page=sources.emby_page('test','10',0,10)
        self.assertEqual(['base','high'],[item['Id'] for item in page['Items']])
        self.assertEqual(2,page['TotalRecordCount'])
        self.assertIn('/Items?',urls[0])

    def enrichment(self, proven=True):
        task=self.repo.submit('task',self.r.Target('电影','themoviedb','42'),{},'test',42,True)
        self.repo.complete_handoff(task['id'],task['generation'])
        self.s.Scheduler(self.repo).open_opportunity('o',task['id'],[self.a.TargetUnit(self.r.Target.from_task(task))],mode='ONESHOT',config=self.s.ScheduleConfig(observation_enabled=False))
        self.archive.authority.set_revisions(self.policy.semantic_hash,'parse')
        observed=self.archive.resolve_item('test','10',self.item)[0]
        observed['raw']=dict(title='2160p REMUX 中文字幕',description='',labels=[],chinese_pgs=True,technical=observed['raw']['technical'],group_known=True)
        with self.repo.connection(write=True) as db:
            self.archive._store_version(db,observed)
            self.archive._sync(db,self.key,'PRESENT','initial')
        before=self.archive.current([self.key])[self.key]
        raw=dict(title='2160p REMUX 特效字幕 中文字幕',description='',labels=[],group_known=True)
        files=[dict(index=0,path='movie.mkv',size=100,role='video',targets=[self.key],requires=[])]
        candidate=dict(raw,infohash='f'*40,torrent_files=files,classification=self.sources.classify_target(self.key),recognition=dict(status='OK',identity=['themoviedb','42']))
        with self.repo.connection(write=True) as db:
            db.execute('INSERT INTO candidates VALUES(?,?,?,?)',('release',json.dumps(candidate),'now','now'))
        self.enrich_manifest=dict(candidate_key='release',manifest_ref='enrich-source',selected_indices=[0],
            assets=[dict(file_index=0,relative_path='movie.mkv',role='video',targets=[self.key],requires=[],content=dict(sha1='a'*40,size=100))],
            publication={self.key:dict(raw=raw,classification=self.sources.classify_target(self.key))},
            association=dict(assets=[dict(file_index=0,cloud_scope_id='cloud',path='/115/media/中文 {电影}.mkv')],emby=[dict(service='test',library_id='10',item_id='old')]))
        decision=self.policy.compare(self.policy.normalize(raw),before['versions'],self.sources.classify_target(self.key),same_assets_verified={observed['version_id']},identity_ok=True,scope_ok=True)
        self.assertEqual('ENRICH_EVIDENCE',decision.action)
        self.enrichments=[dict(target_key=self.key,current_revision=before['revision'],evidence_keys=list(decision.evidence_keys),policy_revision=self.policy.semantic_hash)]
        if proven:
            auth=self.archive.authority
            spec=dict(candidate_key='release',infohash='f'*40,downloader='test',save_path='/download',policy_revision=self.policy.semantic_hash,parse_revision='parse',current={self.key:dict(state='PRESENT',revision=before['revision'])},targets={self.key:dict(action='EVIDENCE_UPGRADE',reason='EVIDENCE_UPGRADE',evidence_keys=list(decision.evidence_keys),quality=list(decision.rank),evidence_source='explicit')},torrent_files=files,selected_indices=[0],verified=dict(identity=True,scope=True,admission=True,files=True,configuration=True))
            auth.prepare('source-plan','o',spec)
            vector=auth.claim('source-plan',auth.vector([self.key]))
            auth.begin_attempt('source-rapid','source-plan',vector,'RAPID',[0],dict(source='synthetic-test'))
            auth.record_result('source-rapid','SUCCEEDED',dict(asset_manifest=dict(assets=self.enrich_manifest['assets'])))
            auth.cancel('source-plan',vector,reason='NO_TRANSFER_NEEDED')
        return task

    def test_enrichment_consumes_once_without_publish_plan_or_cooldown(self):
        self.assertTrue(hasattr(self.archive,'enrich_evidence'),'W06 enrichment missing')
        self.enrichment()
        result=self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)
        self.assertTrue(result['accepted'])
        self.assertEqual('explicit',self.archive.current([self.key])[self.key]['versions'][0].facts.evidence)
        self.assertIsNone(self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])
        self.assertFalse(self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)['accepted'])
        with self.repo.connection() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM plans').fetchone()[0])
            self.assertEqual(0,db.execute("SELECT count(*) FROM plan_actions WHERE kind='PUBLISH'").fetchone()[0])
            self.assertEqual(1,db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])
            self.assertEqual('ARCHIVED',db.execute("SELECT state FROM opportunities WHERE id='o'").fetchone()[0])

    def test_enrichment_uses_verified_mapping_without_replacing_canonical_identity(self):
        self.enrichment()
        with self.repo.connection() as db:
            candidate=json.loads(db.execute("SELECT data FROM candidates WHERE candidate_key='release'").fetchone()[0])
        mapping=dict(state='VERIFIED',source='themoviedb',media_id='42',media_type='电影')
        before=self.archive.authority.vector([self.key])
        cases=[
            (['douban','123'],None),
            (['douban','123'],dict(mapping,state='UNKNOWN')),
            (['douban','123'],dict(mapping,state='CONFLICT')),
            (['douban','123'],dict(mapping,source='imdb')),
            (['douban','123'],dict(mapping,media_id='43')),
            (['douban','123'],dict(mapping,media_type='电视剧')),
            (['douban','123'],dict(mapping,media_type=None)),
            (None,mapping),
            (['douban',None],mapping),
            (['themoviedb','43'],mapping),
        ]
        for identity,proof in cases:
            with self.subTest(identity=identity,proof=proof):
                candidate['recognition']=dict(status='OK',identity=identity,identity_mapping=proof)
                with self.repo.connection(write=True) as db:
                    db.execute("UPDATE candidates SET data=? WHERE candidate_key='release'",(json.dumps(candidate),))
                with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):
                    self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)
                self.assertEqual(before,self.archive.authority.vector([self.key]))
                with self.repo.connection() as db:
                    self.assertEqual(0,db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])
                    self.assertEqual(0,db.execute('SELECT count(*) FROM ingest_receipts').fetchone()[0])
        candidate['recognition']=dict(status='OK',identity=['douban','123'],identity_mapping=mapping)
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE candidates SET data=? WHERE candidate_key='release'",(json.dumps(candidate),))
        self.assertTrue(self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)['accepted'])
        self.assertFalse(self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)['accepted'])
        with self.repo.connection() as db:
            self.assertEqual(candidate['recognition'],json.loads(db.execute("SELECT data FROM candidates WHERE candidate_key='release'").fetchone()[0])['recognition'])
            self.assertEqual(1,db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])

    def test_enrichment_rolls_back_on_archive_failure_and_rejects_stale_cas(self):
        self.assertTrue(hasattr(self.archive,'enrich_evidence'),'W06 enrichment missing')
        self.enrichment()
        with patch.object(self.archive,'_store_version',side_effect=RuntimeError('archive write')):
            with self.assertRaises(RuntimeError):
                self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM evidence_consumption').fetchone()[0])
        self.archive.authority.update_current(self.key,dict(state='UNKNOWN',evidence_ref='changed'),expected_revision=self.enrichments[0]['current_revision'])
        with self.assertRaisesRegex(ValueError,'STALE_CURRENT'):
            self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)

    def test_scans_do_not_resurrect_replaced_path_and_retained_publication_assets(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.sources.cloud['cloud','/115/media/中文 {电影}.mkv']['sha1']='b'*40
        self.archive.reconcile('test','10')
        current=self.archive.current([self.key])[self.key]
        self.assertEqual(1,len(current['versions']))
        self.assertNotEqual('explicit',current['versions'][0].facts.evidence)
        with self.repo.connection() as db:
            self.assertEqual(2,db.execute('SELECT count(*) FROM archive_versions').fetchone()[0])
            self.assertEqual(1,db.execute("SELECT count(*) FROM archive_locations WHERE state='REPLACED'").fetchone()[0])

    def test_subtitle_must_be_associated_in_current_emby_streams(self):
        self.publication()
        self.item['MediaSources'][0]['MediaStreams']=[s for s in self.item['MediaSources'][0]['MediaStreams'] if s['Type']!='Subtitle']
        with self.assertRaisesRegex(ValueError,'SUBTITLE_ASSOCIATION_MISSING'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)

    def test_sdk_harness_reads_without_repository_or_policy(self):
        self.assertTrue((tp.PLUGIN/'host_archive_contract.py').exists(),'W06 host harness missing')
        host=tp.load('host_archive_contract')
        fixture=dict(emby_service='subscriBetter Emby test',library_ids=['533548','533550'],cloud_scopes={},cloud_objects=[],limits=dict(page_size=10,pages=2,items=10))
        fake=types.SimpleNamespace(emby_page=lambda *a:{'Items':[],'TotalRecordCount':0},close=lambda:None)
        with patch.object(host,'HostArchiveSources',return_value=fake):
            result=host.run_host_contract(types.SimpleNamespace(),phase='sdk',fixture=fixture)
        self.assertEqual('PASS',result['status'])
        self.assertFalse(result['final_ingest_confirmed'])
        self.assertEqual(2,len(result['libraries']))

    def test_schema5_migration_preserves_receipts_and_all_old_rows(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        with self.repo.connection(write=True) as db:
            for table in ('management_operations','management_previews','candidate_decisions','archive_scan_baselines','migration_history','migration_receipts','discovery_targets','discovery_records','discovery_sources','ai_usage','ai_requests','ai_runtime','delivery_bundles','local_observations','reconcile_checkpoints','archive_assets','archive_sources','archive_scan_items','archive_scans','archive_locations','archive_contents','archive_versions','archive_targets'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=5')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before={t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables}
        migrated=self.r.Repository(self.repo.path)
        with migrated.connection() as db:
            self.assertEqual(12,db.execute('PRAGMA user_version').fetchone()[0])
            self.assertEqual(before,{t:[tuple(r) for r in db.execute('SELECT * FROM '+t)] for t in tables})
            self.assertEqual([],list(db.execute('PRAGMA foreign_key_check')))

    def test_stale_cached_facts_do_not_remain_present_forever(self):
        self.archive.reconcile('test','10')
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE archive_targets SET updated_at='2000-01-01T00:00:00+00:00'")
        self.assertEqual('UNKNOWN',self.archive.current([self.key])[self.key]['state'])

    def test_resumed_old_scan_cannot_overwrite_newer_current(self):
        self.media('second','copy.strm','b'*40)
        first=self.archive.reconcile('test','10',limits=dict(page_size=1,pages=1,items=1))
        self.sources.items[0]['MediaSources'][0]['MediaStreams'][0].update(Height=1080,Width=1920)
        self.archive.reconcile('test','10')
        result=self.archive.reconcile('test','10',scan_id=first['scan_id'])
        self.assertNotEqual('COMPLETE',result['status'])
        self.assertEqual({1080,2160},{v.facts.resolution for v in self.archive.current([self.key])[self.key]['versions']})

    def test_prior_required_assets_are_rechecked_on_subsequent_current_scan(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.sources.cloud.pop(('cloud','/115/media/movie.srt'))
        result=self.archive.reconcile('test','10')
        self.assertEqual('ERROR',result['status'])
        self.assertNotEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])

    def test_stream_quality_conflicting_with_authorized_plan_cannot_confirm(self):
        self.publication(planned_picture=2)
        with self.assertRaisesRegex(ValueError,'PLANNED_QUALITY_CONFLICT'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)

    def test_raw_cloud_sdk_account_scope_hash_and_deadlines(self):
        calls=[]
        raw=types.SimpleNamespace(fullPathName='/115/media/movie.mkv',isDirectory=False,fileHashes={2:'A'*40},size=100,id='CD2-ID')
        root=types.SimpleNamespace(fullPathName='/115',isDirectory=True,CloudAPI=types.SimpleNamespace(userName='42'))
        def find(request,**kwargs):
            calls.append((request.path,kwargs['timeout']))
            return root if request.path=='/115' else raw
        channel=types.SimpleNamespace(close=lambda:None)
        client=types.SimpleNamespace(channel=channel,stub=types.SimpleNamespace(GetToken=lambda req,timeout:types.SimpleNamespace(success=True,token='synthetic'),FindFileByPath=find))
        pb=types.SimpleNamespace(GetTokenRequest=lambda **k:types.SimpleNamespace(**k),FindFileByPathRequest=lambda **k:types.SimpleNamespace(**k))
        configs={'CloudDriveDisk':dict(enabled=True,host='test.invalid',port=19798,username='synthetic',password='synthetic'),'P115Disk':dict(cookie='UID=42_test')}
        sources=self.m.HostArchiveSources(types.SimpleNamespace(get_config=configs.get),cloud_scopes={'cloud':dict(root='/115',allowed_prefixes=['/115/media'])},libraries={})
        provider=types.SimpleNamespace(user_id=42,fs_dir_getid=lambda *a,**k:dict(state=True,id='7'),fs_files=lambda *a,**k:dict(state=True,cid='7',path=[dict(cid='7',name='media')],count=1,data=[dict(fid='8',cid='7',n='movie.mkv',s=100,sha='a'*40)]))
        with patch.dict('sys.modules',{'p115client':types.SimpleNamespace(P115Client=lambda cookie:provider),'clouddrive2_client':types.SimpleNamespace(CloudDriveClient=lambda address:client),'clouddrive2_client.proto':types.SimpleNamespace(clouddrive_pb2=pb)}):
            result=sources.cloud_stat('cloud','/115/media/movie.mkv')
            self.assertEqual('a'*40,result['sha1'])
            self.assertEqual('CD2-ID',result['cd2_id'])
            self.assertEqual('8',result['p115_id'])
            self.assertEqual([('/115',20),('/115/media/movie.mkv',20)],calls)
            with self.assertRaisesRegex(ValueError,'CLOUD_PATH_UNAUTHORIZED'):
                sources.cloud_stat('cloud','/115/mediaElse/movie.mkv')
            root.CloudAPI.userName='99'
            with self.assertRaisesRegex(ValueError,'ACCOUNT_MISMATCH'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')
            sources.close()
            with self.assertRaisesRegex(ValueError,'ACCOUNT_MISMATCH'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')

    def test_live_host_cloud_hash_requires_independent_115_object(self):
        from unittest.mock import Mock
        scope=dict(root='/115',allowed_prefixes=['/115/media'])
        sources=self.m.HostArchiveSources(types.SimpleNamespace(get_config=lambda name:{'cookie':'synthetic'}),cloud_scopes={'cloud':scope},libraries={})
        raw=types.SimpleNamespace(fullPathName='/115/media/movie.mkv',isDirectory=False,fileHashes={2:'a'*40},size=100,id='placeholder')
        sources._client=lambda *a:(types.SimpleNamespace(stub=types.SimpleNamespace(FindFileByPath=lambda *a,**k:raw)),types.SimpleNamespace(FindFileByPathRequest=lambda **k:types.SimpleNamespace(**k)),[])
        sources.accounts['cloud']='42'
        provider=Mock(user_id=42)
        provider.fs_dir_getid.return_value={'state':True,'id':'7'}
        page=dict(state=True,cid='7',path=[dict(cid='0',name=''),dict(cid='7',name='media')],count=0,data=[])
        provider.fs_files.side_effect=lambda *a,**k:page
        with patch.dict('sys.modules',{'p115client':types.SimpleNamespace(P115Client=lambda cookie:provider)}):
            with self.assertRaisesRegex(ValueError,'P115_OBJECT_AMBIGUOUS'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')
            page.update(count=1,data=[dict(fid='8',cid='7',n='movie.mkv',s=100,sha='a'*40)])
            self.assertEqual('8',sources.cloud_stat('cloud','/115/media/movie.mkv')['p115_id'])
            page['count']=2;page['data'].append(dict(fid='9',cid='7',n='movie.mkv',s=101,sha='b'*40))
            with self.assertRaisesRegex(ValueError,'P115_OBJECT_AMBIGUOUS'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')
            page['count']=1;page['data'].pop()
            page['path'][-1]['name']='other'
            with self.assertRaisesRegex(ValueError,'P115_PARENT_UNKNOWN'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')
            page['path'][-1]['name']='media';provider.user_id=99
            with self.assertRaisesRegex(ValueError,'ACCOUNT_MISMATCH'):
                sources.cloud_stat('cloud','/115/media/movie.mkv')

    def test_enrichment_cannot_backfill_candidate_hash_from_current_cloud_file(self):
        self.enrichment(proven=False)
        with self.assertRaisesRegex(ValueError,'SOURCE_ASSETS_UNVERIFIED'):
            self.archive.enrich_evidence('o','release',self.enrichments,self.enrich_manifest)

    def test_public_classification_passes_media_type_and_rejects_same_id_wrong_type(self):
        class MediaType(Enum):
            MOVIE='电影'
            TV='电视剧'
        calls=[]
        media=types.SimpleNamespace(type=MediaType.MOVIE)
        chain=types.SimpleNamespace(run_module=lambda name,**kw:(calls.append(kw),media)[1])
        import importlib
        adapter=importlib.import_module('w04_subscribetter.candidates').HostCandidateAdapter
        sources=self.m.HostArchiveSources(types.SimpleNamespace(),cloud_scopes={},libraries={})
        with patch.dict('sys.modules',{'app.chain.media':types.SimpleNamespace(MediaChain=lambda:chain),'app.sdk.media':types.SimpleNamespace(normalize_media_source=lambda value:value),'app.schemas.types':types.SimpleNamespace(MediaType=MediaType)}),patch.object(adapter,'identity',return_value=('themoviedb','42')),patch.object(adapter,'classify',return_value={'state':'complete'}):
            self.assertEqual({'state':'complete'},sources.classify_target(self.key))
            self.assertEqual(MediaType.MOVIE,calls[-1].get('mtype'))
            media.type=MediaType.TV
            with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):
                sources.classify_target(self.key)

    def second_publication(self, action='QUALITY_UPGRADE', candidate_key=None):
        if action=='QUALITY_UPGRADE':
            self.item['MediaSources'][0]['MediaStreams'][0].update(Height=1080,Width=1920)
        self.archive.reconcile('test','10')
        current=self.archive.current([self.key])[self.key]
        with self.repo.connection(write=True) as db:
            db.execute('UPDATE target_units SET cooldown_until=NULL WHERE target_key=?',(self.key,))
        self.s.Scheduler(self.repo).open_opportunity('o2',self.task['id'],[self.a.TargetUnit(self.r.Target.from_task(self.task))],mode='ONESHOT',config=self.s.ScheduleConfig(observation_enabled=False))
        auth=self.archive.authority
        spec=copy.deepcopy(auth.plan('plan')['snapshot'])
        if candidate_key:
            spec.update(candidate_key=candidate_key,infohash='2'*40)
        spec['current']={self.key:dict(state='PRESENT',revision=current['revision'])}
        spec['targets'][self.key].update(action=action,reason=action)
        selected=[1] if action=='SIDECAR_SUPPLEMENT' else [0,1]
        spec['selected_indices']=selected
        auth.prepare('plan2','o2',spec)
        vector=auth.claim('plan2',auth.vector([self.key]))
        auth.set_transfer_phase('plan2',vector,'READY_TO_PUBLISH')
        auth.begin_publish('pub2','plan2',vector,selected,validation=dict(policy_revision=self.policy.semantic_hash,parse_revision='parse',current_revisions={self.key:current['revision']},checks={self.key:dict(identity=True,admission=True,scope=True,not_excluded=True,current_allows=True,assets_complete=True,remote_verified=True)}))
        if action=='QUALITY_UPGRADE':
            self.sources.cloud['cloud','/115/media/中文 {电影}.mkv']['sha1']='b'*40
        self.item['MediaSources'][0]['MediaStreams'][0].update(Height=2160,Width=3840)
        manifest=copy.deepcopy(self.manifest)
        manifest.update(plan_id='plan2',manifest_ref='second-manifest')
        manifest['assets'][0]['content']['sha1']='b'*40
        manifest['assets']=[a for a in manifest['assets'] if a['file_index'] in selected]
        consumer=dict(self.consumer,action_id='pub2',evidence_ref='second-consumer')
        consumer['assets']=[a for a in consumer['assets'] if a['file_index'] in selected]
        auth.record_result('pub2','HANDED_OFF',dict(asset_manifest=manifest,consumer_receipt=consumer))
        return manifest,consumer

    def expire_archive(self):
        with self.repo.connection(write=True) as db:
            db.execute("UPDATE archive_targets SET updated_at='2000-01-01T00:00:00+00:00'")
            db.execute("UPDATE archive_versions SET data=json_set(data,'$.observed_at','2000-01-01T00:00:00+00:00')")

    def test_review_I1_expired_handoff_survives_restart_without_relaxing_current_ttl(self):
        self.publication()
        self.expire_archive()
        before=self.archive.current([self.key])[self.key]
        self.assertEqual('UNKNOWN',before['state'])
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10',target_keys=[self.key])['status'])
        self.assertEqual(before,self.archive.current([self.key])[self.key])
        self.archive=self.m.Archive(self.repo,self.policy,self.sources,mappings=[self.mapping])
        self.assertTrue(self.archive.confirm_ingest('pub',self.manifest,self.consumer)['accepted'])
        self.assertEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])
        self.assertIsNotNone(self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])
        with self.repo.connection() as db:
            self.assertEqual('ARCHIVED',db.execute("SELECT state FROM opportunities WHERE id='o'").fetchone()[0])

    def test_review_I1_expired_upgrade_preserves_comparison_and_refreshes_surviving_copy(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.media('copy','copy.strm','a'*40,height=1080)
        manifest,consumer=self.second_publication()
        self.expire_archive()
        self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        current=self.archive.current([self.key])[self.key]
        self.assertEqual('PRESENT',current['state'])
        self.assertEqual({1080,2160},{v.facts.resolution for v in current['versions']})

    def test_review_I1_expired_stale_generation_keeps_receipt_only_and_no_promotion(self):
        self.publication()
        self.repo.set_state(self.task['id'],'PAUSED','test')
        self.expire_archive()
        before=self.archive.current([self.key])[self.key]
        result=self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertFalse(result['accepted'])
        self.assertTrue(result['receipt_only'])
        self.assertEqual(before,self.archive.current([self.key])[self.key])
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM archive_versions').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT count(*) FROM ingest_receipts').fetchone()[0])
            self.assertEqual(1,db.execute("SELECT count(*) FROM action_receipts WHERE outcome='STALE_INGEST'").fetchone()[0])
            proof=json.loads(db.execute("SELECT evidence FROM action_receipts WHERE outcome='STALE_INGEST'").fetchone()[0])
            self.assertFalse(proof[self.key]['improvement_verified'])
        self.assertIsNone(self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])

    def test_review_I1_refresh_failure_and_pagination_do_not_clear_barrier(self):
        self.publication()
        self.expire_archive()
        self.sources.fail_at=0
        with self.assertRaisesRegex(ValueError,'BASELINE_REFRESH'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('HANDED_OFF',self.s.Scheduler(self.repo).target(self.key)['publish_phase'])
        self.assertEqual('UNKNOWN',self.archive.current([self.key])[self.key]['state'])
        self.sources.fail_at=None
        for index in range(11):
            item=self.media('extra'+str(index),'extra'+str(index)+'.strm','d'*40,height=1080)
            item['ProviderIds']['Tmdb']='99'
        original=self.sources.emby_page
        with patch.object(self.sources,'emby_page',side_effect=lambda service,library,start,limit:original(service,library,start,1)):
            with self.assertRaisesRegex(ValueError,'BASELINE_REFRESH_INCOMPLETE'):
                self.archive.confirm_ingest('pub',self.manifest,self.consumer)
            self.assertEqual('UNKNOWN',self.archive.current([self.key])[self.key]['state'])
            with self.assertRaisesRegex(ValueError,'BASELINE_REFRESH_INCOMPLETE'):
                self.archive.confirm_ingest('pub',self.manifest,self.consumer)
            for _ in range(12):
                try:
                    confirmation=self.archive.confirm_ingest('pub',self.manifest,self.consumer)
                    break
                except ValueError as error:
                    self.assertEqual('BASELINE_REFRESH_INCOMPLETE',str(error))
                    self.assertEqual('HANDED_OFF',self.s.Scheduler(self.repo).target(self.key)['publish_phase'])
            else:self.fail('bounded persisted baseline did not finish')
            self.assertTrue(confirmation['accepted'])

    def test_review_I1_one_empty_library_snapshot_cannot_retire_a_surviving_copy(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.media('copy','copy.strm','a'*40,height=1080)
        manifest,consumer=self.second_publication()
        self.expire_archive()
        original=self.sources.emby_page
        snapshots=[0]
        def page(service,library,start,limit):
            result=original(service,library,start,limit)
            if snapshots[0]==0:
                result['Items']=[i for i in result['Items'] if i['Id']!='copy']
                result['TotalRecordCount']=len(result['Items'])
            snapshots[0]+=1
            return result
        with patch.object(self.sources,'emby_page',side_effect=page):
            self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        self.assertEqual(2,len(self.archive.current([self.key])[self.key]['versions']))

    def test_review_I1_active_authority_still_requires_improvement_and_new_better_copy_blocks(self):
        self.publication()
        proof=dict(receipt_id='unverified',version_id='unverified',evidence_ref='unverified',association_verified=True,all_assets_verified=True,consumer_settled=True,improvement_verified=False)
        with self.assertRaisesRegex(ValueError,'improvement proof incomplete'):
            self.archive.authority.confirm_ingest('pub',{self.key:proof})
        self.expire_archive()
        self.media('copy','copy.strm','d'*40)
        self.sources.items[-1]['MediaSources'][0]['MediaStreams'][0]['VideoRange']='HDR10'
        with self.assertRaisesRegex(ValueError,'CURRENT_POLICY_CURRENT_BETTER'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('HANDED_OFF',self.s.Scheduler(self.repo).target(self.key)['publish_phase'])
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT count(*) FROM ingest_receipts').fetchone()[0])

    def test_review_I1_refresh_comparison_and_current_vector_must_share_revision(self):
        self.publication()
        self.expire_archive()
        authority=self.archive.authority
        original=authority.vector
        before=original([self.key])[self.key]['current_revision']
        def raced(keys):
            authority.update_current(self.key,dict(state='MISSING',archive_revision=self.archive.current([self.key])[self.key]['archive_revision'],evidence_ref='concurrent'),expected_revision=before)
            return original(keys)
        with patch.object(authority,'vector',side_effect=raced),self.assertRaisesRegex(ValueError,'STALE_CURRENT'):
            self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.assertEqual('HANDED_OFF',self.s.Scheduler(self.repo).target(self.key)['publish_phase'])

    def test_review_I5_second_publication_retires_replaced_content_keeps_independent_copy(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.media('copy','copy.strm','a'*40,height=1080)
        manifest,consumer=self.second_publication()
        self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        with self.repo.connection() as db:
            active=[json.loads(r[0]) for r in db.execute('SELECT data FROM archive_versions WHERE active=1')]
            self.assertEqual({('/115/media/中文 {电影}.mkv','b'*40),('/115/media/copy.mkv','a'*40)}, {(o['video']['path'],o['video']['sha1']) for o in active})
            self.assertEqual(3,db.execute('SELECT count(*) FROM archive_versions').fetchone()[0])
            self.assertGreaterEqual(db.execute('SELECT count(*) FROM archive_sources').fetchone()[0],4)
            self.assertEqual(0,db.execute("SELECT count(*) FROM archive_versions v JOIN archive_assets a ON a.version_id=v.id JOIN archive_locations l ON l.id=a.location_id WHERE v.active=1 AND l.state='REPLACED'").fetchone()[0])
        self.assertEqual(2,len(self.archive.current([self.key])[self.key]['versions']))

    def test_review_I5_shared_required_subtitle_replacement_invalidates_other_claims(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        other=self.media('copy','copy.strm','d'*40,height=1080)
        other['MediaSources'][0]['MediaStreams'].append(dict(Type='Subtitle',IsExternal=True,Path='/Emby/Movies/movie.srt'))
        observed=self.archive.resolve_item('test','10',other)[0]
        observed['assets']=[dict(self.manifest['assets'][0],content=self.m.content(observed['video']),location=observed['video']),dict(self.manifest['assets'][1],location=self.sources.cloud_stat('cloud','/115/media/movie.srt'))]
        with self.repo.connection(write=True) as db:
            self.archive._store_version(db,observed)
            self.archive._sync(db,self.key,'PRESENT','copy')
        manifest,consumer=self.second_publication()
        self.sources.cloud['cloud','/115/media/movie.srt']['sha1']='e'*40
        manifest['assets'][1]['content']['sha1']='e'*40
        self.archive.authority.record_result('pub2','HANDED_OFF',dict(asset_manifest=manifest,consumer_receipt=consumer))
        self.expire_archive()
        self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        self.assertEqual(1,len(self.archive.current([self.key])[self.key]['versions']))
        with self.repo.connection() as db:
            self.assertEqual(0,db.execute('SELECT active FROM archive_versions WHERE id=?',(observed['version_id'],)).fetchone()[0])

    def test_review_I5_sidecar_replacement_keeps_video_required_and_old_source_history(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        before=self.archive.current([self.key])[self.key]
        clock=self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at']
        manifest,consumer=self.second_publication(action='SIDECAR_SUPPLEMENT')
        self.sources.cloud['cloud','/115/media/movie.srt']['sha1']='e'*40
        manifest['assets'][0]['content']['sha1']='e'*40
        self.archive.authority.record_result('pub2','HANDED_OFF',dict(asset_manifest=manifest,consumer_receipt=consumer))
        self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        self.assertEqual(before['versions'][0].version_id,self.archive.current([self.key])[self.key]['versions'][0].version_id)
        self.assertEqual(clock,self.s.Scheduler(self.repo).target(self.key)['last_ingest_confirmed_at'])
        with self.repo.connection() as db:
            links=[json.loads(r[0]) for r in db.execute('SELECT a.data FROM archive_assets a JOIN archive_versions v ON v.id=a.version_id WHERE v.active=1')]
            self.assertEqual({'video','subtitle'},{a['role'] for a in links})
            historical=[json.loads(r[0]) for r in db.execute('SELECT data FROM archive_sources')]
            self.assertTrue(any(a['location']['sha1']=='c'*40 for o in historical for a in o['assets'] if a['role']=='subtitle'))
            self.assertTrue(any(a['location']['sha1']=='e'*40 for o in historical for a in o['assets'] if a['role']=='subtitle'))
        self.sources.cloud['cloud','/115/media/中文 {电影}.mkv']['sha1']='b'*40
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])
        self.assertEqual(1,len(self.archive.current([self.key])[self.key]['versions']))

    def test_review_I4_grouped_sources_keep_independent_strm_snapshots_and_quality(self):
        other=self.media('second','copy.strm','b'*40,height=1080)
        self.item['MediaSources'].extend(other['MediaSources'])
        self.sources.items=[self.item]
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])
        current=self.archive.current([self.key])[self.key]
        self.assertEqual({1080,2160},{v.facts.resolution for v in current['versions']})
        observations=self.archive.resolve_item('test','10',self.item)
        self.assertEqual({'a'*40,'b'*40},{o['video']['sha1'] for o in observations})
        self.assertEqual(2,len({o['strm']['path'] for o in observations}))
        (self.strms/'copy.strm').write_text('/Cloud/115/media/changed.mkv')
        self.archive.validate_observation(observations[0])
        with self.assertRaisesRegex(ValueError,'STRM_CHANGED'):
            self.archive.validate_observation(observations[1])
        bad=copy.deepcopy(self.item)
        bad['MediaSources'][1]['Path']='/Emby/MoviesElse/copy.strm'
        with self.assertRaisesRegex(ValueError,'NO_MAPPING'):
            self.archive.resolve_item('test','10',bad)
        bad['MediaSources']=bad['MediaSources'][:1]
        bad['MediaSources'][0]['Path']='/Cloud/115/media/conflicting.mkv'
        with self.assertRaisesRegex(ValueError,'PLAYBACK_SOURCE_CONFLICT'):
            self.archive.resolve_item('test','10',bad)

    def test_review_I3_retained_required_subtitle_still_needs_stream_association(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        self.item['MediaSources'][0]['MediaStreams']=[s for s in self.item['MediaSources'][0]['MediaStreams'] if s['Type']!='Subtitle']
        result=self.archive.reconcile('test','10')
        self.assertEqual('ERROR',result['status'])
        self.assertEqual(['SUBTITLE_ASSOCIATION_MISSING'],result['diagnostics'])
        self.assertNotEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])
        with self.repo.connection() as db:
            self.assertEqual(1,db.execute('SELECT count(*) FROM archive_sources').fetchone()[0])
            self.assertEqual(2,db.execute('SELECT count(*) FROM archive_assets').fetchone()[0])
        self.item['MediaSources'][0]['MediaStreams'].append(dict(Type='Subtitle',IsExternal=True,Path='/Emby/Movies/movie.srt'))
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])
        self.assertEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])

    def test_review_I3_retained_idx_sub_pair_requires_its_current_idx_stream(self):
        observed=self.archive.resolve_item('test','10',self.item)[0]
        assets=[]
        for index,suffix in enumerate(('idx','sub')):
            path='/115/media/movie.'+suffix
            self.sources.cloud['cloud',path]=dict(sha1=str(index+1)*40,size=5,cd2_id=suffix,p115_id='')
            assets.append(dict(file_index=index,role='subtitle',location=self.sources.cloud_stat('cloud',path),content=dict(sha1=str(index+1)*40,size=5)))
        observed['assets']=assets
        self.item['MediaSources'][0]['MediaStreams'].append(dict(Type='Subtitle',IsExternal=True,Path='/Emby/Movies/movie.idx'))
        with self.repo.connection(write=True) as db:
            self.archive._store_version(db,observed)
            self.archive._sync(db,self.key,'PRESENT','idx-sub')
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])
        self.item['MediaSources'][0]['MediaStreams'][-1]['Path']='/Emby/Movies/unrelated.idx'
        result=self.archive.reconcile('test','10')
        self.assertEqual(['SUBTITLE_ASSOCIATION_MISSING'],result['diagnostics'])
        self.assertEqual('ERROR',self.archive.current([self.key])[self.key]['state'])

    def test_review_I2_unambiguous_legacy_identity_keeps_its_own_claims_only(self):
        rules=[self.mapping,dict(self.mapping,id='other-library',library_id='20')]
        self.archive=self.m.Archive(self.repo,self.policy,self.sources,mappings=rules)
        observed=self.archive.resolve_item('test','10',self.item)[0]
        remote=observed['video']
        observed['version_id']=self.m.digest([self.key,'cloud',remote.get('account_ref'),remote['path'],self.m.content(remote)])
        observed['source_evidence']=['legacy-source']
        with self.repo.connection(write=True) as db:
            self.archive._store_version(db,observed)
            self.archive._sync(db,self.key,'PRESENT','legacy')
        self.archive.reconcile('test','10')
        self.archive.reconcile('test','20')
        with self.repo.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM archive_versions WHERE active=1')]
            self.assertEqual(2,len(rows))
            own=next(r for r in rows if r['library']=='10')
            self.assertEqual(observed['version_id'],own['id'])
            self.assertEqual(['legacy-source'],json.loads(own['data'])['source_evidence'])
            self.assertEqual([],json.loads(next(r for r in rows if r['library']=='20')['data'])['source_evidence'])

    def supplement_with_prior_dependencies(self):
        self.publication()
        self.archive.confirm_ingest('pub',self.manifest,self.consumer)
        observed=self.archive.resolve_item('test','10',self.item)[0]
        dependencies=[]
        self.item['MediaSources'][0]['MediaStreams'].append(dict(Type='Subtitle',IsExternal=True,Path='/Emby/Movies/other.srt'))
        for name,role,sha in [('required.ttf','font','9'),('license.txt','license','8'),('other.srt','subtitle','7')]:
            path='/115/media/'+name
            self.sources.cloud['cloud',path]=dict(sha1=sha*40,size=5,cd2_id=name,p115_id='')
            asset=dict(file_index=1,relative_path=name,role=role,targets=[self.key],requires=[],
                       source_ref='prior:'+name,content=dict(sha1=sha*40,size=5),location=self.sources.cloud_stat('cloud',path))
            dependencies.append(asset)
            observed['assets'].append(asset)
            observed.update(candidate_key='old:'+name,infohash=sha*40,source_assets=[asset],source_evidence=['prior:'+name])
            with self.repo.connection(write=True) as db:
                self.archive._store_version(db,observed)
                self.archive._sync(db,self.key,'PRESENT','verified-dependencies')
        manifest,consumer=self.second_publication(action='SIDECAR_SUPPLEMENT',candidate_key='new-subtitle')
        self.sources.cloud['cloud','/115/media/movie.srt']['sha1']='e'*40
        manifest['assets'][0]['content']['sha1']='e'*40
        self.archive.authority.record_result('pub2','HANDED_OFF',dict(asset_manifest=manifest,consumer_receipt=consumer))
        self.assertTrue(self.archive.confirm_ingest('pub2',manifest,consumer)['accepted'])
        return dependencies,manifest

    def test_review_N1_supplement_retains_coherent_dependencies_with_colliding_source_indices(self):
        dependencies,manifest=self.supplement_with_prior_dependencies()
        expected={a['location']['path'] for a in dependencies}|{'/115/media/movie.srt','/115/media/中文 {电影}.mkv'}
        with self.repo.connection() as db:
            observed=json.loads(db.execute('SELECT data FROM archive_versions WHERE active=1').fetchone()[0])
            links=[json.loads(r[0]) for r in db.execute('SELECT a.data FROM archive_assets a JOIN archive_versions v ON v.id=a.version_id WHERE v.active=1')]
            self.assertEqual(expected,{a['location']['path'] for a in links})
            self.assertEqual(expected,{a['location']['path'] for a in observed['assets']})
            self.assertEqual(4,len([a for a in observed['assets'] if a['file_index']==1]))
            self.assertTrue(all(a['source_ref']=='prior:'+a['relative_path'] for a in observed['assets'] if a['location']['path'] in {d['location']['path'] for d in dependencies}))
            subtitle=next(a for a in observed['assets'] if a['location']['path']=='/115/media/movie.srt')
            self.assertEqual('e'*40,subtitle['content']['sha1'])
            self.assertEqual(subtitle['content'],self.m.content(subtitle['location']))
            self.assertNotIn(subtitle['source_ref'],{a['source_ref'] for a in dependencies})
            candidate=self.archive.authority.plan('plan2')['snapshot']
            with self.assertRaisesRegex(ValueError,'SOURCE_ASSETS_UNVERIFIED'):
                self.archive._candidate_asset_proof(db,'new-subtitle',candidate,[dependencies[0]])
            self.assertTrue(self.archive._candidate_asset_proof(db,'new-subtitle',candidate,manifest['assets']))
            self.assertTrue(self.archive._candidate_asset_proof(db,'old:required.ttf',dict(infohash='9'*40),[dependencies[0]]))
        self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])
        self.assertEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])

    def test_review_N1_missing_retained_font_license_or_other_subtitle_blocks_reliable_current(self):
        dependencies,_=self.supplement_with_prior_dependencies()
        for asset in dependencies:
            with self.subTest(role=asset['role']):
                key=('cloud',asset['location']['path'])
                value=self.sources.cloud.pop(key)
                scan=self.archive.reconcile('test','10')
                self.assertEqual('ERROR',scan['status'])
                self.assertEqual(['CLOUD_NOT_FOUND'],scan['diagnostics'])
                self.assertNotEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])
                self.sources.cloud[key]=value
                self.assertEqual('COMPLETE',self.archive.reconcile('test','10')['status'])

    def test_review_N1_retained_dependency_rejects_unproved_bytes_or_account_rebinding(self):
        dependencies,_=self.supplement_with_prior_dependencies()
        path=dependencies[0]['location']['path']
        self.sources.cloud['cloud',path]['sha1']='6'*40
        scan=self.archive.reconcile('test','10')
        self.assertEqual(['ASSET_CONTENT_CONFLICT'],scan['diagnostics'])
        self.sources.cloud['cloud',path]['sha1']='9'*40
        original=self.sources.cloud_stat
        def wrong_account(scope,actual,**kwargs):
            result=original(scope,actual,**kwargs)
            return dict(result,account_ref='different-account') if actual==path else result
        with patch.object(self.sources,'cloud_stat',side_effect=wrong_account):
            scan=self.archive.reconcile('test','10')
        self.assertEqual(['ASSET_SCOPE_CONFLICT'],scan['diagnostics'])
        self.assertNotEqual('PRESENT',self.archive.current([self.key])[self.key]['state'])

    def test_review_I2_independent_library_and_service_associations_share_only_bytes(self):
        rules=[self.mapping,dict(self.mapping,id='other-library',library_id='20'),
               dict(self.mapping,id='other-service',emby_service='second')]
        self.archive=self.m.Archive(self.repo,self.policy,self.sources,mappings=rules)
        for service,library in [('test','10'),('test','20'),('second','10')]:
            self.assertEqual('COMPLETE',self.archive.reconcile(service,library)['status'])
        self.assertEqual(3,len(self.archive.current([self.key])[self.key]['versions']))
        self.sources.items=[]
        self.archive.reconcile('test','10')
        self.archive.reconcile('test','10')
        current=self.archive.current([self.key])[self.key]
        self.assertEqual('PRESENT',current['state'])
        self.assertEqual(2,len(current['versions']))
        with self.repo.connection() as db:
            self.assertEqual({('test','20'),('second','10')},{tuple(r) for r in db.execute('SELECT service,library FROM archive_versions WHERE active=1')})
            self.assertEqual(1,db.execute('SELECT count(*) FROM archive_contents').fetchone()[0])
            self.assertEqual(1,db.execute('SELECT count(*) FROM archive_locations').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
