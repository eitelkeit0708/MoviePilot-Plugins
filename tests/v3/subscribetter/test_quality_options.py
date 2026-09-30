"""Independent quality facts, configurable subtypes and policy-local admission."""
import unittest
import test_policy


class QualityOptionsTests(unittest.TestCase):
    setUpClass = classmethod(test_policy.PolicyTests.setUpClass.__func__)
    setUp = test_policy.PolicyTests.setUp
    facts = test_policy.PolicyTests.facts
    compare = test_policy.PolicyTests.compare

    def test_custom_equal_priority_uses_next_dimension_and_keeps_primary_groups(self):
        template=dict(resolutions=[2160],group='any',source='any',dimensions=['picture','audio'],
                      preferences={'picture':[['dv_p5','dv_p8'],'dv_p7_fel']})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        self.assertEqual(p.value_rank('dv_p5','picture','欧美剧'),p.value_rank('dv_p8','picture','欧美剧'))
        self.assertGreater(p.value_rank('dv_p5','picture','欧美剧'),p.value_rank('dv_p7_fel','picture','欧美剧'))
        self.assertGreater(p.value_rank('dv_p7_fel','picture','欧美剧'),p.value_rank('hdr10plus','picture','欧美剧'))
        new=p.normalize({'title':'2160p WEB-DL DV P8 TrueHD 中文字幕'})
        old=p.normalize({'title':'2160p WEB-DL DV P5 AAC 中文字幕'},current=True)
        self.assertEqual('QUALITY_UPGRADE',p.compare(new,[self.m.Version('old',old)],self.c,identity_ok=True,scope_ok=True).reason)
        self.assertEqual(p.value_rank('hdr10plus','picture','欧美剧'),p.value_rank('hdr_vivid','picture','欧美剧'))
        for order in [[],[[]],[['dv_p5','dv_p5']],[['dv_p5'],'dv_p5'],[['dv_p5','hdr10']],[[['dv_p5']]],['absent']]:
            with self.subTest(order=order),self.assertRaises(ValueError):
                self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'preferences':{'picture':order}}})

    def test_audio_primary_groups_keep_stored_families_and_default_order(self):
        options=self.m.QUALITY_VALUES['audio']
        groups=self.p.describe('欧美剧')['comparison'][-1]['groups']
        self.assertEqual(['无损空间音频','无损','空间音频','其他音轨'],[g['title'] for g in groups])
        cases=[('TrueHD Atmos','truehd_atmos',3,'lossless_spatial'),
               ('DTS-HD MA DTS:X','dtshdma_x',3,'lossless_spatial'),
               ('TrueHD','truehd',3,'lossless'),('DDP Atmos','ddp_atmos',2,'spatial'),
               ('DD+ Atmos','ddp_atmos',2,'spatial'),('Atmos E-AC-3','ddp_atmos',2,'spatial'),
               ('DTS:X','dtsx',2,'spatial'),('DDP','ddp',1,'other'),('AAC','aac',0,'other')]
        for token,option,family,group in cases:
            with self.subTest(token=token):
                facts=self.facts('2160p WEB-DL -HHWEB 中文字幕 '+token)
                self.assertEqual((family,option),(facts.audio,facts.audio_format))
                self.assertEqual(group,options[option]['group'])
        for new,old in [('TrueHD Atmos','TrueHD'),('TrueHD','DDP Atmos'),('DDP Atmos','DDP')]:
            self.assertEqual('QUALITY_UPGRADE',self.compare(self.facts('2160p WEB-DL -HHWEB 中文字幕 '+new),self.facts('2160p WEB-DL -HHWEB 中文字幕 '+old,current=True)).reason)

    def test_audio_primary_and_subtype_preferences_apply_to_new_groups(self):
        template=dict(resolutions=[2160],group='any',source='any',dimensions=['audio'],
                      family_preferences={'audio':['other','spatial','lossless','lossless_spatial']},
                      preferences={'audio':['aac','truehd_atmos','ddp','ddp_atmos']})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        self.assertGreater(p.value_rank('aac','audio','欧美剧'),p.value_rank('ddp','audio','欧美剧'))
        self.assertGreater(p.value_rank('ddp','audio','欧美剧'),p.value_rank('ddp_atmos','audio','欧美剧'))
        self.assertGreater(p.value_rank('truehd','audio','欧美剧'),p.value_rank('truehd_atmos','audio','欧美剧'))

    def test_legacy_audio_orders_migrate_by_split_merge_and_facts_keep_their_meanings(self):
        order=['1','3','0','2']
        template=dict(resolutions=[2160],group='any',source='any',dimensions=['audio'],family_preferences={'audio':order})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        self.assertEqual(order,p.templates['欧美剧']['family_preferences']['audio'])
        self.assertGreater(p.value_rank('ddp','audio','欧美剧'),p.value_rank('truehd_atmos','audio','欧美剧'))
        self.assertGreater(p.value_rank('aac','audio','欧美剧'),p.value_rank('ddp_atmos','audio','欧美剧'))
        self.assertEqual(['other','lossless_spatial','lossless','spatial'],[v['id'] for v in p.describe('欧美剧')['comparison'][0]['groups']])
        self.assertGreater(p.value_rank('aac','audio','欧美剧'),p.value_rank('truehd_atmos','audio','欧美剧'))
        for family,option in [(0,'other'),(1,'ddp'),(2,'immersive'),(3,'lossless')]:
            facts=self.facts('2160p WEB-DL TrueHD Atmos',technical={'audio':family})
            self.assertEqual((family,option),(facts.audio,facts.audio_format))
        with self.assertRaises(ValueError):
            self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'family_preferences':{'audio':['1','lossless','spatial','other']}}})

    def test_unknown_spatial_or_lossless_format_cannot_be_assumed_inferior(self):
        for old,new in [('Atmos','TrueHD'),('Atmos','TrueHD Atmos'),('DTS:X','TrueHD'),('DTS:X','TrueHD Atmos')]:
            with self.subTest(old=old,new=new):
                self.assertEqual('DEFER',self.compare(self.facts('2160p WEB-DL -HHWEB 中文字幕 '+new),self.facts('2160p WEB-DL -HHWEB 中文字幕 '+old,current=True)).status)
        old=self.facts('2160p WEB-DL -HHWEB 中文字幕',technical={'audio':3},current=True)
        self.assertEqual('DEFER',self.compare(self.facts('2160p WEB-DL -HHWEB 中文字幕 TrueHD Atmos'),old).status)
        self.assertEqual(2,self.facts('2160p WEB-DL DTS:X').audio)
        self.assertEqual('DTS:X（无损状态未确认）',self.m.QUALITY_VALUES['audio']['dtsx']['title'])

    def test_primary_quality_tier_precedes_subtype_preferences(self):
        template=dict(resolutions=[2160],group='any',source='any',dimensions=['picture','audio'],
                      preferences={'picture':['hdr10','dv_p8','dv_p5'],'audio':['aac','ddp_atmos','truehd']})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        def compare(new,old):
            raw=lambda token:{'title':'2160p WEB-DL 中文字幕 '+token}
            return p.compare(p.normalize(raw(new)),[self.m.Version('old',p.normalize(raw(old),current=True))],self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('QUALITY_UPGRADE',compare('DV P5 AAC','HDR10 TrueHD').reason)
        self.assertEqual('QUALITY_UPGRADE',compare('DV P8 AAC','DV P5 TrueHD').reason)
        self.assertEqual('CURRENT_BETTER',compare('HDR10 AAC','DV P8 AAC').reason)
        self.assertEqual('QUALITY_UPGRADE',compare('HDR10 TrueHD','HDR10 DDP Atmos').reason)
        self.assertEqual('CURRENT_BETTER',compare('HDR10 AAC','HDR10 DDP Atmos').reason)

    def test_primary_order_is_configurable_and_unknown_profile_stays_unknown(self):
        template=dict(resolutions=[2160],group='any',source='any',dimensions=['picture'],
                      family_preferences={'picture':['1','2','0']},preferences={'picture':['dv_p8','dv_p5']})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        self.assertGreater(p.value_rank('hdr10','picture','欧美剧'),p.value_rank('dv_p8','picture','欧美剧'))
        self.assertGreater(p.value_rank('dv_p8','picture','欧美剧'),p.value_rank('dv_p5','picture','欧美剧'))
        self.assertEqual(p.value_rank('hdr10plus','picture','欧美剧'),p.value_rank('hdr_vivid','picture','欧美剧'))
        self.assertGreater(p.value_rank('hdr10plus','picture','欧美剧'),p.value_rank('hdr10','picture','欧美剧'))
        unknown=p.normalize({'title':'2160p WEB-DL DV 中文字幕'},current=True)
        known=p.normalize({'title':'2160p WEB-DL DV P8 中文字幕'})
        self.assertEqual('DEFER',p.compare(known,[self.m.Version('old',unknown)],self.c,identity_ok=True,scope_ok=True).status)
        self.assertEqual('HDR',p.describe('欧美剧')['comparison'][0]['groups'][0]['title'])
        with self.assertRaises(ValueError):
            self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'family_preferences':{'picture':['1','1','0']}}})

    def test_precise_formats_drive_comparison_and_measured_facts_win(self):
        for token, expected in [('HDR10+', 'hdr10plus'), ('HDR Vivid', 'hdr_vivid'),
                                ('DV P7 FEL', 'dv_p7_fel'), ('DV P7 MEL', 'dv_p7_mel'),
                                ('DV P5', 'dv_p5'), ('DV P8.1', 'dv_p8')]:
            with self.subTest(token=token):
                new = self.facts('2160p WEB-DL -HHWEB 中文字幕 ' + token)
                self.assertEqual(expected, new.picture_format)
                self.assertEqual('QUALITY_UPGRADE', self.compare(new, self.facts('2160p HDR10 WEB-DL -HHWEB 中文字幕')).reason)
        measured = self.facts('2160p DV P7 FEL TrueHD Atmos WEB-DL', technical={'picture': 1, 'audio': 1})
        self.assertEqual(('hdr', 'ddp'), (measured.picture_format, measured.audio_format))
        precise = self.facts('2160p DV P7 FEL WEB-DL', technical={'picture': 2, 'picture_format': 'dv_p5'})
        self.assertEqual('dv_p5', precise.picture_format)

    def test_subtype_admission_order_and_local_conditions_are_independent(self):
        template = dict(resolutions=[2160,1080],group='any',source='any',dimensions=['picture','audio'],
                        allowed={'picture':['hdr10plus','hdr10'], 'audio':['aac','ddp']},
                        preferences={'picture':['hdr10','hdr10plus']},admission={'ge':['seeders',5]})
        p = self.m.Policy({'stable-id':'欧美剧','other':'国产剧'},7,templates={'欧美剧':template})
        raw = {'title':'2160p HDR10 WEB-DL -HHWEB 中文字幕 AAC','seeders':6}
        a = p.normalize(raw)
        b = p.normalize({**raw,'title':raw['title'].replace('HDR10','HDR10+')},current=True)
        result = p.compare(a,[self.m.Version('old',b)],self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('QUALITY_UPGRADE',result.reason)
        self.assertEqual('POLICY_ADMISSION',p.admit(p.normalize({**raw,'seeders':1}),self.c,identity_ok=True,scope_ok=True).reason)
        other = {**self.c,'effective':{**self.c['effective'],'category_id':'other'}}
        self.assertEqual('ALLOW',p.admit(p.normalize({**raw,'seeders':1}),other,identity_ok=True,scope_ok=True).status)
        self.assertEqual('QUALITY_NOT_ALLOWED:picture',p.admit(p.normalize({**raw,'title':raw['title'].replace('HDR10','DV P5')}),self.c,identity_ok=True,scope_ok=True).reason)
        with self.assertRaises(ValueError):
            self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'allowed':{'audio':[]}}})

    def test_missing_profile_cannot_be_assumed_inferior_and_catalog_is_complete(self):
        new=self.facts('2160p DV P7 WEB-DL -HHWEB 中文字幕')
        old=self.facts('2160p DV WEB-DL -HHWEB 中文字幕',current=True)
        self.assertEqual('DEFER',self.compare(new,old).status)
        catalog=self.m.policy_catalog()
        self.assertEqual(set(self.m.FIELDS),set(catalog['predicate_fields']))
        self.assertIn('OfficialGroup',catalog['rule_definitions'])
        self.assertEqual(set(['resolution','picture','special','source','hq','audio','anime']),set(catalog['quality_options']))

    def test_all_filter_dimensions_apply_even_when_not_used_for_ordering(self):
        template=dict(resolutions=[720,1080,2160],group='any',source='any',dimensions=['resolution'],
                      allowed={'audio':['aac'],'source':['webdl'],'special':['false'],'hq':['false'],'anime':['0']})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        raw={'title':'720p WEB-DL AAC 中文字幕'}
        gate=lambda raw:p.admit(p.normalize(raw),self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('ALLOW',gate(raw).status)
        for token,dimension in [('DTS','audio'),('WEBRip','source'),('特效字幕','special'),('HQ','hq')]:
            title=raw['title'].replace('AAC','DTS') if token=='DTS' else raw['title'].replace('WEB-DL','WEBRip') if token=='WEBRip' else raw['title']+' '+token
            self.assertEqual('QUALITY_NOT_ALLOWED:'+dimension,gate({'title':title}).reason)

    def test_dual_hdr_and_invalid_technical_subtype_do_not_corrupt_facts(self):
        mixed=self.facts('2160p DV HDR10+ WEB-DL -HHWEB 中文字幕')
        self.assertEqual((2,'dv'),(mixed.picture,mixed.picture_format))
        wrong=self.facts(technical={'picture':1,'picture_format':'dv_p7'})
        self.assertEqual(('INVALID_FACTS',),wrong.errors)
        self.assertEqual('aac',self.m.quality_facts(self.facts('2160p AAC WEB-DL'))['audio_format'])
        self.assertEqual(0,self.m.quality_facts(self.facts('2160p AAC WEB-DL'))['audio'])

    def test_local_and_global_conditions_both_apply_and_rules_are_overridable(self):
        template=dict(resolutions=[2160],group='official',source='movie',dimensions=['picture'],admission={'registered':'MyRule'})
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template},admission={'ge':['seeders',2]},
                        overrides={'OfficialGroup':{'regex':['title','MY-GROUP']},'MyRule':{'regex':['title','ALLOWED']}})
        def gate(title,seeders=3):return p.admit(p.normalize({'title':'2160p WEB-DL 中文字幕 '+title,'seeders':seeders}),self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('ALLOW',gate('MY-GROUP ALLOWED').status)
        self.assertEqual('GROUP_NOT_ALLOWED',gate('-HHWEB ALLOWED').reason)
        self.assertEqual('POLICY_ADMISSION',gate('MY-GROUP').reason)
        self.assertEqual('CUSTOM_ADMISSION',gate('MY-GROUP ALLOWED',1).reason)
