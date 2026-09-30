"""W02 fictional pure-policy checks; these do not certify host or delivery integration."""
import importlib.util
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
PATH = ROOT / "plugins.v3/subscribetter/policy.py"


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PATH.exists():
            spec = importlib.util.spec_from_file_location("subscribetter_policy", PATH)
            cls.m = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.m
            spec.loader.exec_module(cls.m)

    def setUp(self):
        self.assertTrue(PATH.exists(), "W02 independent policy is not implemented")
        self.p = self.m.Policy({"stable-id": "欧美剧"}, 7)
        self.c = {"state": "complete", "policy_revision": 7,
                  "effective": {"category_id": "stable-id", "category_path": ["改名目录"],
                                "rule_id": "rule-a", "source": "policy"}}

    def facts(self, title="2160p WEB-DL -HHWEB 中文字幕", **raw):
        return self.p.normalize({"title": "Fictional " + title, **raw}, current=raw.pop("current", False))

    def admit(self, facts, **kw):
        return self.p.admit(facts, self.c, identity_ok=True, scope_ok=True, **kw)

    def compare(self, a, b, **kw):
        versions = b if isinstance(b, list) else [self.m.Version("old", b)]
        return self.p.compare(a, versions, self.c, identity_ok=True, scope_ok=True, **kw)

    def test_display_keeps_all_changes_separate_from_deciding_dimension(self):
        old = self.facts('1080p WEB-DL -HHWEB 中文字幕', current=True,
                         technical={'resolution':1080,'picture':0,'audio':0})
        new = self.facts('2160p Dolby Vision TrueHD WEB-DL -HHWEB 中文字幕')
        decision = self.compare(new, old)
        self.assertEqual('QUALITY_UPGRADE', decision.reason)
        self.assertEqual(['resolution'], [v['dimension'] for v in decision.comparisons])
        change = self.p.describe_change(new, [self.m.Version('old',old)], decision)
        rows = {v['dimension']:v for v in change['versions'][0]['changes']}
        self.assertEqual([1,1,1], [rows[d]['order'] for d in ('resolution','picture','audio')])
        self.assertTrue(rows['resolution']['decisive']); self.assertFalse(rows['audio']['decisive'])
        better_audio = replace(old, audio=3, raw={**old.raw, 'technical':{'resolution':1080,'picture':0,'audio':3}})
        ddp = self.facts('2160p Dolby Vision DDP WEB-DL -HHWEB 中文字幕')
        versions = [self.m.Version('old',old), self.m.Version('lossless',better_audio)]
        mixed = self.p.describe_change(ddp, versions, self.compare(ddp,versions))
        audio = {v['version_id']:next(d['order'] for d in v['changes'] if d['dimension']=='audio') for v in mixed['versions']}
        self.assertEqual({'old':1,'lossless':-1},audio)
        old_dv = replace(old, picture=2, raw={**old.raw, 'technical':{'resolution':1080,'picture':2,'audio':0}})
        hdr = self.facts('2160p HDR WEB-DL -HHWEB 中文字幕')
        picture = self.p.describe_change(hdr,[self.m.Version('dv',old_dv)],self.compare(hdr,old_dv))['versions'][0]
        self.assertEqual(-1,next(v['order'] for v in picture['changes'] if v['dimension']=='picture'))
        pgs = self.facts('1080p WEB-DL -HHWEB 中文字幕',current=True,chinese_pgs=True,
                         technical={'resolution':1080,'picture':0,'audio':0})
        explicit = self.facts('2160p Dolby Vision DDP WEB-DL -HHWEB 简繁特效PGS字幕')
        simultaneous = self.p.describe_change(explicit,[self.m.Version('pgs',pgs)],self.compare(explicit,pgs))
        self.assertEqual('quality',simultaneous['kind'])
        self.assertTrue(next(v['evidence'] for v in simultaneous['versions'][0]['changes'] if v['dimension']=='special'))
        unknown = self.facts('2160p WEB-DL -HHWEB 中文字幕')
        row = self.p.describe_change(unknown,[self.m.Version('old',old)],self.compare(unknown,old))['versions'][0]
        self.assertIsNone(next(v['order'] for v in row['changes'] if v['dimension']=='audio'))
        many = [self.m.Version(str(i),old) for i in range(25)]
        bounded = self.p.describe_change(new,many,self.compare(new,many))
        self.assertEqual(20,len(bounded['versions']));self.assertEqual(25,bounded['version_count']);self.assertTrue(bounded['truncated'])

    def test_configured_template_controls_admission_ranking_and_current_comparison(self):
        template={'resolutions':[1080,2160],'group':'official','source':'movie','dimensions':['source','resolution']}
        p=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':template})
        candidate=p.normalize({'title':'Fictional 2160p WEB-DL -HHWEB 中文字幕'})
        current=p.normalize({'title':'Fictional 1080p REMUX -HHWEB 中文字幕'},current=True)
        result=p.compare(candidate,[self.m.Version('old',current)],self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('CURRENT_BETTER',result.reason)
        self.assertEqual('source',result.comparisons[0]['dimension'])
        self.assertNotEqual(p.semantic_hash,self.p.semantic_hash)
        template['dimensions'].reverse()
        self.assertEqual(('source','resolution'),tuple(p.categories['欧美剧'][3]))
        restricted=self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'resolutions':[1080]}})
        result=restricted.admit(restricted.normalize({'title':'Fictional 2160p WEB-DL -HHWEB 中文字幕'}),self.c,identity_ok=True,scope_ok=True)
        self.assertEqual('RESOLUTION_NOT_ALLOWED',result.reason)
        with self.assertRaises(ValueError):
            self.m.Policy({'stable-id':'欧美剧'},7,templates={'欧美剧':{**template,'dimensions':['source','source']}})

    def test_category_matrix(self):
        matrix = {
            "华语电影": ("2160p WEB-DL 中文字幕", "1080p WEB-DL 中文字幕"),
            "外语电影": ("2160p REMUX 中文字幕", "1080p REMUX 中文字幕"),
            "动画电影": ("1080i BluRay x265 中文字幕", "720p WEB-DL 中文字幕"),
            "国产剧": ("2160p WEB H.265 -HHWEB 中文字幕", "2160p REMUX -HHWEB 中文字幕"),
            "欧美剧": ("1080i REMUX -HHWEB 中文字幕", "2160p WEB-DL -Unknown 中文字幕"),
            "日韩剧": ("1080p BluRay x264 -HHWEB 中文字幕", "720p -HHWEB 中文字幕"),
            "港台剧": ("2160p WEBRip -HHWEB 中文字幕", "2160p -Unknown 中文字幕"),
            "纪录片": ("1080p WEBRip 中文字幕", "2160p BDMV 中文字幕"),
            "国漫": ("2160p -HHWEB 中文字幕", "1080p -HHWEB 中文字幕"),
            "日番": ("1080p -VCB-Studio 中文字幕", "2160p -VCB-Studio 中文字幕"),
            "欧美漫": ("1080i -HHWEB 中文字幕", "2160p -HHWEB 中文字幕"),
            "综艺": ("1080p -HHWEB 中文字幕", "2160p -MTeam 中文字幕"),
            "现场": ("1080p 中文字幕", "720p 中文字幕"),
        }
        self.assertEqual(set(matrix), set(self.m.CATEGORIES))
        for name, (yes, no) in matrix.items():
            with self.subTest(category=name):
                self.p = self.m.Policy({"stable-id": name}, 7)
                self.assertEqual("ALLOW", self.admit(self.facts(yes)).status)
                self.assertEqual("REJECT", self.admit(self.facts(no)).status)

    def test_language_negations_and_native_OR_guard(self):
        negatives = ["无字幕", "中文字幕已删除", "no Chinese subtitles", "Chinese subtitles removed",
                     "without Mandarin", "Mandarin audio missing", "无国语", "国语音轨已移除",
                     "粤语", "Cantonese", "多国语言", "English subtitles only"]
        for text in negatives:
            with self.subTest(text=text):
                self.assertEqual("REJECT", self.admit(self.facts("2160p WEB-DL -HHWEB " + text,
                                                                        original_language="zh")).status)
        for text in ["国语", "国英双语", "台配", "中文硬字幕", "CHS", "Chinese subtitles",
                     "无国语 中文字幕", "无字幕 Mandarin", "无简体字幕 繁体字幕"]:
            with self.subTest(text=text):
                self.assertEqual("ALLOW", self.admit(self.facts("2160p WEB-DL -HHWEB " + text)).status)
        self.assertEqual("ALLOW", self.admit(self.facts("2160p WEB-DL -HHWEB", original_language="cn")).status)
        self.assertEqual("REJECT", self.admit(self.facts("2160p WEB-DL -HHWEB", original_language="bo")).status)

    def test_web_technical_boundary_and_base_exclusions(self):
        for token in ["WEB-DL", "WEBRip", "WEB H.265", "WEB_x264", "WEB / HEVC"]:
            self.assertEqual("ALLOW", self.admit(self.facts(f"2160p {token} -HHWEB 中文字幕")).status)
        for title in ["The Web 2160p -CHDWEB 中文字幕", "2160p -CHDWEB 中文字幕"]:
            self.assertEqual("REJECT", self.admit(self.facts(title)).status)
        for token in ["ISO", "BDMV", "BD50", "AV1", "OPUS 5.1", "HSBS", "MiniBD", "HDCAM", "-SubsPlease"]:
            self.assertEqual("REJECT", self.admit(self.facts(f"2160p WEB-DL -HHWEB 中文字幕 {token}")).status)

    def test_audio_and_resolution_picture_normalization(self):
        for token, rank in [("TrueHD Atmos", 3), ("TrueHD", 3), ("FLAC", 3), ("DTS-HD MA", 3),
                            ("DDP Atmos", 2), ("DTS:X", 2), ("DDP", 1), ("EAC3", 1), ("DD+", 1),
                            ("PCM", 3), ("LPCM", 3), ("DTS-HD HRA", 0), ("AAC", 0)]:
            self.assertEqual(rank, self.facts(f"2160p WEB-DL {token}").audio)
        a = self.facts("1080i WEB-DL DV HQ -HHWEB 中文字幕")
        b = self.facts("1080p WEB-DL -HHWEB 中文字幕")
        self.assertEqual("QUALITY_UPGRADE", self.compare(a, b).reason)
        self.assertEqual((0, False), (self.facts("2160p EDR").picture, self.facts("2160p EDR").hq))
        self.assertNotEqual(2160, self.facts("4K修复 1080p").resolution)

    def test_lexicographic_precedence(self):
        pairs = [("2160p WEB-DL", "1080p REMUX TrueHD"),
                 ("2160p DV WEB-DL", "2160p HDR REMUX TrueHD"),
                 ("2160p WEB-DL 特效中文字幕", "2160p REMUX TrueHD"),
                 ("2160p REMUX", "2160p WEB-DL HQ TrueHD"),
                 ("2160p WEB-DL HQ", "2160p WEB-DL TrueHD")]
        for better, worse in pairs:
            self.assertEqual("QUALITY_UPGRADE", self.compare(self.facts(better + " -HHWEB 中文字幕"),
                                                              self.facts(worse + " -HHWEB 中文字幕")).reason)

    def test_T018_each_picture_claim_and_restoration_boundary(self):
        plain = self.facts("1080p WEB-DL -HHWEB 中文字幕")
        for resolution in ("1080p", "1080i"):
            for claim in ("DV", "HDR", "HQ", "DV HDR HQ"):
                with self.subTest(resolution=resolution, claim=claim):
                    tagged = self.facts(f"{resolution} WEB-DL {claim} -HHWEB 中文字幕")
                    self.assertEqual("QUALITY_UPGRADE", self.compare(tagged, plain).reason)
                    self.assertEqual("CURRENT_BETTER", self.compare(plain, tagged).reason)
        for resolution in ("1080p", "2160p"):
            edr = self.facts(f"{resolution} WEB-DL EDR -HHWEB 中文字幕")
            self.assertEqual((0, False), (edr.picture, edr.hq))
        for claim in ("4K修复", "4K修复 1080p", "1080p WEB-DL", "2160p WEB-DL"):
            restored = self.facts(claim, description="4K修复")
            expected = 2160 if claim.startswith("2160p") else 1080 if "1080p" in claim else None
            self.assertEqual(expected, restored.resolution)

    def test_T019_source_identity_and_disc_blacklist(self):
        plain = self.facts("2160p WEB-DL -HHWEB 中文字幕")
        for token in ("WEB", "WEB-DL", "WEBRip"):
            with self.subTest(token=token):
                web = self.facts(f"2160p {token} H.264 -HHWEB 中文字幕")
                self.assertEqual("web", web.source)
                self.assertEqual("ALLOW", self.admit(web).status)
                self.assertEqual("EQUIVALENT", self.compare(web, plain).reason)
        for title in ("The Web 2160p -CHDWEB 中文字幕", "2160p -CHDWEB 中文字幕"):
            facts = self.facts(title)
            self.assertIsNone(facts.source)
            self.assertEqual("REJECT", self.admit(facts).status)
        # A real technical WEB token stays valid even when the release group contains WEB.
        self.assertEqual("web", self.facts("2160p WEB-DL -CHDWEB 中文字幕").source)
        for token in ("原盘", "藍光原盤", "ISO", "BDMV", "BD50"):
            with self.subTest(disc=token):
                disc = self.facts(f"2160p {token} -HHWEB 中文字幕")
                self.assertFalse(disc.base)
                self.assertEqual("REJECT", self.admit(disc).status)
        # Preserve the imported rule's explicit encoding exception; source notes alone
        # must not relabel a WEB/REMUX release as a full disc.
        for source in ("WEB-DL", "REMUX"):
            self.assertTrue(self.facts(f"2160p {source} -HHWEB 中文字幕 原盘").base)
        for token in ("ISO", "BDMV"):
            self.assertFalse(self.facts(f"2160p WEB-DL -HHWEB 中文字幕 {token}").base)

    def test_japanese_tiers_and_exceptions_keep_base_filters(self):
        self.p = self.m.Policy({"stable-id": "日番"}, 7)
        tiers = ["1080i -VCB-Studio", "2160p B-Global", "1080p AMZN -HHWEB", "1080p B-Global", "1080p -HHWEB"]
        for higher, lower in zip(tiers, tiers[1:]):
            self.assertEqual("QUALITY_UPGRADE", self.compare(self.facts(higher + " 中文字幕"),
                                                              self.facts(lower + " 中文字幕")).reason)
        self.assertEqual("REJECT", self.admit(self.facts("1080p -VCB-Studio 无字幕")).status)
        self.assertEqual("REJECT", self.admit(self.facts("2160p B-Global AV1 中文字幕")).status)
        self.assertEqual("DEFER", self.compare(self.facts("1080p -VCB-Studio 中文字幕"),
                                                self.facts("1080p 中文字幕", current=True)).status)

    def test_old_group_unknown_does_not_remove_current_or_block_earlier_dimension(self):
        old = self.facts("1080p WEB-DL 中文字幕", current=True)
        self.assertEqual("QUALITY_UPGRADE", self.compare(self.facts(), old).reason)
        old4k = self.facts("2160p WEB-DL 中文字幕", current=True)
        self.assertEqual("EQUIVALENT", self.compare(self.facts(), old4k).reason)
        self.assertEqual("DEFER", self.compare(self.facts(), replace(old4k, picture=None)).status)

    def test_active_reliable_versions_only_and_never_downgrade(self):
        candidate = self.facts()
        high = self.facts("2160p DV REMUX -HHWEB 中文字幕")
        low = self.facts("1080p WEB-DL -HHWEB 中文字幕")
        self.assertEqual("REJECT", self.compare(candidate, [self.m.Version("h", high), self.m.Version("l", low)]).status)
        self.assertEqual("ALLOW", self.compare(candidate, [self.m.Version("h", high, active=False), self.m.Version("l", low)]).status)
        self.assertEqual("DEFER", self.compare(candidate, [self.m.Version("h", high, reliable=False)]).status)

    def test_classification_and_explicit_gates_fail_closed(self):
        self.c["policy_revision"] = 6
        self.assertEqual("DEFER", self.admit(self.facts()).status)
        self.c["policy_revision"] = 7
        self.c["effective"]["category_id"] = "unbound"
        self.assertEqual("ERROR", self.admit(self.facts()).status)
        self.c["effective"]["category_id"] = "stable-id"
        for kw in [{"excluded": True}, {"locked": {"resolution": 1080}}]:
            self.assertEqual("REJECT", self.admit(self.facts(), **kw).status)
        self.assertEqual("DEFER", self.p.admit(self.facts(), self.c).status)
        self.assertEqual("REJECT", self.p.admit(self.facts(), self.c, identity_ok=False, scope_ok=True).status)

    def test_special_evidence_and_once_only_final_association(self):
        candidate = self.facts("2160p WEB-DL -HHWEB 简繁特效PGS字幕")
        old = self.facts(current=True, chinese_pgs=True)
        self.assertTrue(old.special_zh_subtitles)
        self.assertEqual("inferred_pgs", old.evidence)
        self.assertFalse(self.facts(current=True, subtitle_formats=["ASS"], fonts=15).special_zh_subtitles)
        d = self.compare(candidate, old)
        self.assertEqual("EVIDENCE_UPGRADE", d.reason)
        self.assertEqual(1, len(d.evidence_keys))
        self.assertEqual((), self.m.confirm_evidence(d, final_association_verified=False))
        keys = self.m.confirm_evidence(d, final_association_verified=True)
        self.assertEqual("EQUIVALENT", self.compare(candidate, old, consumed=set(keys)).reason)
        same = self.compare(candidate, old, same_assets_verified={"old"})
        self.assertEqual("ENRICH_EVIDENCE", same.action)
        self.assertEqual("TRANSFER", d.action)
        reordered = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides={})
        self.assertEqual(self.p.semantic_hash, reordered.semantic_hash)

    def test_missing_required_evidence_and_provider_error(self):
        self.assertEqual("DEFER", self.admit(self.facts(missing_fields=["description"])).status)
        self.assertEqual("ERROR", self.admit(self.facts(provider_errors=["HTTP_401"])).status)
        self.assertEqual("ERROR", self.admit(self.facts("x" * 20000)).status)

    def test_missing_dimension_is_not_an_ordinary_unmentioned_tag(self):
        for field in ["resolution", "picture", "audio", "source", "group"]:
            with self.subTest(field=field):
                self.assertEqual("DEFER", self.admit(self.facts(missing_fields=[field])).status)
        old = self.facts("1080p 中文字幕", current=True, missing_fields=["audio", "source"])
        self.assertEqual("QUALITY_UPGRADE", self.compare(self.facts(), old).reason)
        self.assertEqual("DEFER", self.admit(self.facts("WEB-DL -HHWEB 中文字幕")).status)

    def test_sidecar_equivalence_tolerates_only_unknown_current_rank_dimensions(self):
        self.p = self.m.Policy({"stable-id": "外语电影"}, 7)
        candidate = self.facts("2160p WEB-DL 中文字幕", technical={"resolution": 2160, "picture": 0, "audio": 0})
        current = self.facts("2160p WEB-DL", current=True,
                             missing_fields=["description", "labels"],
                             technical={"resolution": 2160, "picture": 0, "audio": 0})
        versions = [self.m.Version("current", current)]
        ordinary = self.p.compare(candidate, versions, self.c, identity_ok=True, scope_ok=True)
        self.assertEqual("CURRENT_EVIDENCE_MISSING:special", ordinary.reason)
        sidecar = self.p.compare_sidecar(candidate, versions, self.c, identity_ok=True, scope_ok=True)
        self.assertEqual(("REJECT", "EQUIVALENT", ordinary.rank),
                         (sidecar.status, sidecar.reason, sidecar.rank))
        known_mismatch = replace(current, picture=1)
        blocked = self.p.compare_sidecar(candidate, [self.m.Version("current", known_mismatch)], self.c,
                                         identity_ok=True, scope_ok=True)
        self.assertNotEqual("EQUIVALENT", blocked.reason)

    def test_group_platform_locks_and_malformed_gates(self):
        f = self.facts("2160p AMZN WEB-DL -M-Team 中文字幕")
        self.assertEqual("ALLOW", self.admit(f, locked={"group": "MTeam", "platform": "Amazon"}).status)
        self.assertEqual("REJECT", self.admit(f, locked={"platform": "Netflix"}).status)
        self.assertEqual("ERROR", self.admit(f, locked={"resolution": True}).status)
        self.assertEqual("ERROR", self.admit(f, excluded="true").status)

    def test_an_equal_current_highest_prevents_transfer_for_lower_duplicate(self):
        f = self.facts()
        lower = self.facts("1080p WEB-DL -HHWEB 中文字幕")
        self.assertEqual("EQUIVALENT", self.compare(f, [self.m.Version("equal", f), self.m.Version("lower", lower)]).reason)

    def test_semantic_reorder_preserves_evidence_key_and_import_is_detached(self):
        exprs = [{"regex": ["text", "fictional"]}, {"literal": True}]
        overrides = {"Test": {"all": exprs}}
        one = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides=overrides)
        two = self.m.Policy({"stable-id": "欧美剧"}, 8, overrides={"Test": {"all": list(reversed(exprs))}})
        self.assertEqual(one.semantic_hash, two.semantic_hash)
        overrides["Test"]["all"][0]["regex"][1] = "changed"
        self.assertEqual(one.semantic_hash, two.semantic_hash)

    def test_bounded_custom_regex_and_malformed_import(self):
        p = self.m.Policy({"stable-id": "欧美剧"}, 7,
                          overrides={"GeneralFilter": {"regex": ["text", "(a+)+$"]}})
        f = p.normalize({"title": "a" * 15000 + "!"})
        self.assertEqual("ERROR", p.admit(f, self.c, identity_ok=True, scope_ok=True).status)
        self.assertEqual(("PREDICATE_TIMEOUT",), f.errors)
        for bad in [{"regex": [[], "x"]}, {"registered": []}, {"in": ["size", "x"]}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.m.import_predicates({"Bad": bad})
        for unsupported in ["match", "seeders", "shell"]:
            with self.assertRaises(ValueError):
                self.m.import_legacy_overrides([{"id": "CNSUB", unsupported: ["title"]}])

    def test_owned_snapshot_exact_migration_and_full_group_list(self):
        import json
        source = ROOT / "docs/subscribetter/design-v1.2-20260917/输入参考"
        migrated = json.loads((PATH.parent / "policy-data.json").read_text(encoding="utf-8"))
        records = json.loads((source / "01_自定义规则.json").read_text(encoding="utf-8"))
        records += json.loads((source / "WEB最小补丁_收紧版_20260914.json").read_text(encoding="utf-8"))
        expected = {r["id"]: {k: r[k] for k in ("include", "exclude")} for r in records}
        self.assertEqual(expected, {k: v for k, v in migrated.items() if k != "CNSUB"})
        groups = ["MWeb", "MTeam", "M-Team", "TPTV", "ADE", "ADWeb", "Audies", "HHWEB", "CHDWEB",
                  "CHDBits", "CHDTV", "CHDHKTV", "SGNB", "OurTV", "OurBits", "UBWEB", "UBits", "UBTV",
                  "Dream", "DBTV", "QHstudIo"]
        for group in groups:
            self.assertEqual("ALLOW", self.admit(self.facts(f"2160p WEB-DL -{group} 中文字幕")).status)
        for group in ["HHWEB2", "Unknown", "B-Global", "VCB-Studio"]:
            self.assertEqual("REJECT", self.admit(self.facts(f"2160p WEB-DL -{group} 中文字幕")).status)

    def test_category_specific_dimensions_and_special_negations(self):
        for name in ["日韩剧", "港台剧", "纪录片"]:
            self.p = self.m.Policy({"stable-id": name}, 7)
            self.assertEqual("CURRENT_BETTER", self.compare(self.facts("2160p WEB-DL -HHWEB 特效中文字幕"),
                                                             self.facts("2160p REMUX -HHWEB 中文字幕")).reason)
        self.p = self.m.Policy({"stable-id": "现场"}, 7)
        self.assertEqual("QUALITY_UPGRADE", self.compare(self.facts("2160p TrueHD 中文字幕"),
                                                          self.facts("2160p HQ DDP 中文字幕")).reason)
        self.p = self.m.Policy({"stable-id": "综艺"}, 7)
        self.assertEqual("EQUIVALENT", self.compare(self.facts("2160p DV HQ TrueHD -HHWEB 中文字幕"), self.facts()).reason)
        for text in ["无特效字幕 中文字幕", "ASS 中文字幕", "PGS 中文字幕", "特效字幕 English subtitles"]:
            self.assertFalse(self.facts(text).special_zh_subtitles)

    def test_all_542_migrated_category_levels_are_reachable_in_exact_order(self):
        import json
        groups = json.loads((ROOT / "docs/subscribetter/design-v1.2-20260917/输入参考/02_优先级规则组.json")
                            .read_text(encoding="utf-8"))
        witnesses = {"Resolution4K": "2160p", "Resolution1080": "1080i", "DolbyVision": "DV",
                     "HDRVideo": "HLG", "SpecialSubtitles": "特效中文字幕", "ChineseSubtitles": "中文字幕",
                     "RemuxSource": "REMUX", "MovieSource": "WEB-DL", "WEBDL": "WEB H.265",
                     "HighBitrate": "HQ", "LosslessAudio": "FLAC", "ImmersiveAudio": "Atmos", "DolbyPlus": "DDP",
                     "OfficialGroup": "-HHWEB", "HHWEBGroup": "-HHWEB", "VCBGroup": "-VCB-Studio",
                     "BGlobal": "B-Global", "AnimePlatform": "AMZN", "GeneralFilter": ""}
        count = 0
        for group in groups[1:]:
            self.p = self.m.Policy({"stable-id": group["category"]}, 7)
            previous = None
            # Only the test reads archived ordinal strings. Production never parses them.
            for index, conjunction in enumerate(group["rule_string"].split(">")):
                title = " ".join(witnesses[key.strip()] for key in conjunction.split("&")) + " 中文字幕"
                with self.subTest(category=group["category"], level=index):
                    decision = self.admit(self.facts(title))
                    self.assertEqual("ALLOW", decision.status)
                    if previous is not None:
                        self.assertGreater(previous, decision.rank)
                    previous = decision.rank
                count += 1
        self.assertEqual(542, count)

    def test_public_classification_adapter_refreshes_without_losing_manual_override(self):
        from copy import deepcopy
        import types
        from unittest.mock import patch
        from test_ownership import AdapterContractTests
        host = AdapterContractTests()
        host.setUp()

        class Snapshot:
            def __init__(self, value):
                self.value = value

            def model_copy(self, update=None, deep=False):
                return Snapshot({**deepcopy(self.value), **(update or {})})

            def model_dump(self, mode="json"):
                return deepcopy(self.value)

        old = {**self.c, "effective": {**self.c["effective"], "source": "manual"}}
        media = types.SimpleNamespace(classification=Snapshot(old))
        sdk = types.ModuleType("app.sdk.classification")
        sdk.classify_media = deepcopy  # Real SDK returns an untouched copy if unassembled.
        with patch.dict(sys.modules, {"app.sdk.classification": sdk}):
            result = host.adapter.classify(media)
            self.assertEqual("not_evaluated", result["state"])
            self.assertEqual("complete", media.classification.value["state"])
            self.assertEqual("manual", result["effective"]["source"])

            def assembled(copy):
                self.assertEqual("manual", copy.classification.value["effective"]["source"])
                copy.classification = Snapshot(old)
                return copy

            sdk.classify_media = assembled
            self.assertEqual(old, host.adapter.classify(media))

    def test_structured_predicates_and_validated_import(self):
        expr = {"all": [{"any": [{"eq": ["original_language", "zh"]}, {"registered": "ChineseSubtitles"}]},
                        {"not": {"in": ["media_type", ["music"]]}}, {"ge": ["size", 10]}]}
        imported = self.m.import_predicates({"CustomGate": expr})
        p = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides=imported, admission={"registered": "CustomGate"})
        f = p.normalize({"title": "2160p WEB-DL -HHWEB 中文字幕", "size": 10, "media_type": "movie"})
        self.assertEqual("ALLOW", p.admit(f, self.c, identity_ok=True, scope_ok=True).status)
        for bad in [{"eval": "1"}, {"registered": "unknown"}, {"all": []}]:
            with self.assertRaises(ValueError):
                self.m.import_predicates({"CustomGate": bad})
        with self.assertRaises(ValueError):
            self.m.import_predicates({"A": {"registered": "B"}, "B": {"registered": "A"}})

    def test_admission_explanation_preserves_short_circuit_and_missing_evidence(self):
        cases = [
            ({"all": [{"literal": False}, {"gt": ["size", 0]}]}, "FAIL", "NOT_RUN"),
            ({"any": [{"literal": True}, {"gt": ["size", 0]}]}, "PASS", "NOT_RUN"),
            ({"all": [{"literal": True}, {"gt": ["size", 0]}]}, "MISSING", "MISSING"),
            ({"any": [{"literal": False}, {"gt": ["size", 0]}]}, "MISSING", "MISSING"),
            ({"not": {"gt": ["size", 0]}}, "MISSING", "MISSING"),
        ]
        for expression, expected, leaf in cases:
            with self.subTest(expression=expression):
                policy = self.m.Policy({"stable-id": "欧美剧"}, 7, admission=expression)
                result = policy.explain_admission({"title": "Fictional WEB-DL"})
                trace = {tuple(item["path"]): item for item in result["trace"]}
                self.assertEqual(expected, result["status"])
                self.assertEqual(expected, trace[()]["status"])
                self.assertEqual(leaf, trace[(1,) if "not" not in expression else (0,)]["status"])

    def test_legacy_override_import_and_revision_require_reprofile(self):
        overrides = self.m.import_legacy_overrides([
            {"id": "OfficialGroup", "name": "Local user choice", "include": "-LocalOnly", "exclude": ""},
            {"id": "CNSUB", "include": ["LOCAL_LANGUAGE"], "exclude": [],
             "tmdb": {"original_language": "zh,cn", "origin_country": "CN,TW"}}])
        p = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides=overrides)
        f = p.normalize({"title": "2160p WEB-DL -LocalOnly LOCAL_LANGUAGE"})
        self.assertEqual("ALLOW", p.admit(f, self.c, identity_ok=True, scope_ok=True).status)
        for country, expected in [("TW", "ALLOW"), ("CA", "REJECT")]:
            f = p.normalize({"title": "2160p WEB-DL -LocalOnly", "original_language": "zh", "origin_country": country})
            self.assertEqual(expected, p.admit(f, self.c, identity_ok=True, scope_ok=True).status)
        f = p.normalize({"title": "2160p WEB-DL -LocalOnly 中文字幕"})
        self.assertEqual("FACTS_REQUIRE_RENORMALIZATION", self.admit(f).reason)

    def test_classification_fail_closed_and_source_does_not_invent_country_scope(self):
        for country in ["CA", "BR", "ZA", "IN"]:
            self.assertEqual("ALLOW", self.admit(self.facts(origin_country=country)).status)
        for state, status in [("not_evaluated", "DEFER"), ("partial", "DEFER"), ("invalid_policy", "ERROR")]:
            self.c["state"] = state
            self.assertEqual(status, self.admit(self.facts()).status)

    def test_review_F1_incomplete_text_cannot_prove_negative_filters(self):
        for category, subtitle in [("国产剧", "中文字幕"), ("欧美剧", "特效中文字幕")]:
            self.p = self.m.Policy({"stable-id": category}, 7)
            title = f"2160p WEB-DL -HHWEB {subtitle}"
            for field in ["description", "labels", "subtitle_description"]:
                with self.subTest(category=category, missing=field):
                    partial = self.facts(title, missing_fields=[field])
                    self.assertIsNone(partial.base)
                    self.assertEqual("DEFER", self.admit(partial).status)
                    complete = self.facts(title, **{field: ["ISO"] if field == "labels" else "ISO"})
                    self.assertEqual("REJECT", self.admit(complete).status)
        # A custom condition must see the same missing merged-text dependency even
        # if all built-in fields are explicitly replaced by field-independent rules.
        overrides = {name: {"literal": True} for name in ["GeneralFilter", "Resolution4K", "OfficialGroup",
                     "WEBDL", "RemuxSource", "MovieSource", "HighBitrate", "DolbyVision", "LosslessAudio",
                     "MandarinAudio", "ChineseSubtitles", "SpecialSubtitles", "HHWEBGroup", "VCBGroup",
                     "BGlobal", "AnimePlatform"]}
        self.p = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides=overrides,
                              admission={"not": {"regex": ["text", "ISO"]}})
        self.assertEqual("DEFER", self.admit(self.facts(missing_fields=["description"])).status)

    def test_review_F2_movie_source_override_is_independent_of_web_label(self):
        override = {"MovieSource": {"regex": ["text", "-LocalOnly"]}}
        for category in ["华语电影", "外语电影", "动画电影", "欧美剧", "日韩剧", "港台剧", "纪录片"]:
            self.p = self.m.Policy({"stable-id": category}, 7, overrides=override)
            with self.subTest(category=category):
                f = self.facts()
                self.assertEqual("web", f.source)
                self.assertEqual("REJECT", self.admit(f).status)
                self.assertEqual("ALLOW", self.admit(self.facts("2160p WEB-DL -HHWEB -LocalOnly 中文字幕")).status)
                self.assertEqual("ALLOW", self.admit(self.facts("2160p REMUX -HHWEB 中文字幕")).status)
        self.p = self.m.Policy({"stable-id": "国产剧"}, 7, overrides=override)
        self.assertEqual("ALLOW", self.admit(self.facts()).status)
        self.p = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides={"MovieSource": {"ge": ["size", 100]}})
        self.assertEqual("DEFER", self.admit(self.facts()).status)

    def test_review_F3_unknown_admission_predicate_preserves_current_profile(self):
        overrides = [self.m.import_legacy_overrides([{"id": "OfficialGroup", "include": "-HHWEB", "exclude": "",
                                                     "tmdb": {"origin_country": "CN"}}]),
                     {"OfficialGroup": {"ge": ["size", 100]}},
                     {"GeneralFilter": {"ge": ["size", 100]}},
                     {"MovieSource": {"ge": ["size", 100]}}]
        for override in overrides:
            self.p = self.m.Policy({"stable-id": "欧美剧"}, 7, overrides=override)
            with self.subTest(override=override):
                candidate = self.facts(size=200)
                old = self.facts("1080p WEB-DL 中文字幕", current=True)
                self.assertEqual((1080, 0, 0), (old.resolution, old.picture, old.audio))
                self.assertEqual("QUALITY_UPGRADE", self.compare(candidate, old).reason)
        self.p = self.m.Policy({"stable-id": "日番"}, 7, overrides=overrides[0])
        old = self.facts("1080p 中文字幕", current=True)
        self.assertEqual(1080, old.resolution)
        self.assertEqual("DEFER", self.compare(self.facts("1080p -VCB-Studio 中文字幕"), old).status)

    def test_review_cached_facts_require_new_normalization_contract(self):
        # The previous release fingerprinted only predicate data, so a persisted
        # partial-text ALLOW could otherwise survive this evaluator bug fix.
        stale = replace(self.facts(), predicate_hash=self.m._hash(self.p.rules))
        self.assertEqual("FACTS_REQUIRE_RENORMALIZATION", self.admit(stale).reason)

    def test_review_fix2_unknown_source_defers_but_known_rejection_remains(self):
        for category in ["欧美剧", "国产剧"]:
            self.p = self.m.Policy({"stable-id": category}, 7,
                                  overrides={"RemuxSource": {"ge": ["size", 100]}})
            with self.subTest(category=category, evidence="missing"):
                facts = self.facts()
                self.assertIsNone(facts.source)
                self.assertIn("predicate:RemuxSource", facts.missing)
                self.assertTrue(facts.source_admission["web" if category == "国产剧" else "movie"])
                self.assertEqual("DEFER", self.admit(facts).status)
            with self.subTest(category=category, evidence="confirmed_non_remux"):
                self.assertEqual("ALLOW", self.admit(self.facts(size=0)).status)
            with self.subTest(category=category, evidence="no_allowed_source"):
                self.assertEqual("REJECT", self.admit(self.facts("2160p -HHWEB 中文字幕", size=0)).status)
        self.assertEqual("REJECT", self.admit(self.facts(size=200)).status)  # Domestic WEB rejects confirmed REMUX.


if __name__ == "__main__":
    unittest.main()
