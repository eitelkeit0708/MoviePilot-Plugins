"""A verified site identity must not assign a different physical video to its target."""
from pathlib import Path
from types import SimpleNamespace as NS
from enum import Enum
import tempfile
import unittest

from torrentool.api import Bencode
from test_planner import load


class PhysicalIdentityTests(unittest.TestCase):
    def test_provider_alias_and_explicit_type_year_conflicts(self):
        candidates, repository = load("candidates"), load("repository")
        target = repository.Target("电影", "tmdb", "42")
        recognized = {"meta": NS(en_name="Right Film", year="2026"),
                      "media": NS(title="右边的电影", original_title="Alternate Film", year="2026")}
        for name, kind, year, expected in (
            ("Alternate Film", "电影", "2026", True),
            ("Wrong Film", "电影", "2026", False),
            ("Right Film", "电视剧", "2026", False),
            ("Right Film", "电影", "2025", False),
            (None, "电影", "2026", False),
        ):
            with self.subTest(name=name, kind=kind, year=year):
                physical = NS(meta=NS(cn_name=None, en_name=name, type=kind, year=year))
                self.assertEqual(expected, candidates._physical_identity_confirmed(
                    physical, recognized, target, (name or "Unknown") + ".mkv"))

    def test_site_name_cannot_vouch_for_provider_identity_or_wrong_tv_year(self):
        candidates, repository = load("candidates"), load("repository")
        movie = repository.Target("电影", "tmdb", "42")
        recognized = {"meta": NS(en_name="Wrong Film"),
                      "media": NS(title="Right Film", original_title=None, year="2026")}
        physical = NS(meta=NS(en_name="Wrong Film", cn_name=None, type="电影", year="2026"))
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, movie, "Wrong.Film.2026.mkv"))
        physical.meta.cn_name = "Right Film"
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, movie, "[Right Film] Wrong.Film.2026.mkv"))
        physical.meta.cn_name = None
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, movie, "[Right Film] Wrong.Film.2026.mkv"))
        tv = repository.Target("电视剧", "tmdb", "43", 1)
        recognized = {"meta": NS(en_name="The Flash"),
                      "media": NS(title="The Flash", year="2014")}
        physical = NS(meta=NS(en_name="The Flash", cn_name=None, type="电视剧", year="1990"))
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, tv, "The.Flash.1990.S01E01.mkv"))

    def test_physical_provider_id_must_not_conflict(self):
        candidates, repository = load("candidates"), load("repository")
        target = repository.Target("电影", "tmdb", "42")
        recognized = {"meta": NS(en_name="Right Film"),
                      "media": NS(title="Right Film", year="2026")}
        physical = NS(meta=NS(en_name="Right Film", type="电影", year="2026",
                              media_source="themoviedb", media_id="99"))
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, target, "Right.Film.2026.mkv"))
        physical.meta.media_id = "42"
        self.assertTrue(candidates._physical_identity_confirmed(
            physical, recognized, target, "Right.Film.2026.mkv"))
        physical.meta.media_source = Enum("MediaSource", {"TMDB": "themoviedb"}).TMDB
        self.assertTrue(candidates._physical_identity_confirmed(
            physical, recognized, target, "Right.Film.2026.mkv"))

    def test_generic_episode_name_needs_provider_named_parent(self):
        candidates, repository = load("candidates"), load("repository")
        target = repository.Target("电视剧", "tmdb", "42", 1)
        recognized = {"meta": NS(en_name="Expected Show"),
                      "media": NS(title="Expected Show", year="2026")}
        physical = NS(meta=NS(en_name="Episode 1", cn_name=None, type="电视剧", year=None))
        self.assertTrue(candidates._physical_identity_confirmed(
            physical, recognized, target, "Expected Show/Season 1/Episode 1.mkv"))
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, target, "Other Show/Season 1/Episode 1.mkv"))
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, target, "Expected Show/Other Show/Season 1/E01.mkv"))
        physical.meta.en_name = "Other Show"
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, target, "Expected Show/Season 1/Other.Show.S01E01.mkv"))
        physical.meta.cn_name = "第1集"
        self.assertFalse(candidates._physical_identity_confirmed(
            physical, recognized, target, "Expected Show/Season 1/Other.Show.第1集.mkv"))

    def test_wrong_movie_or_show_file_never_gets_acquire_plan(self):
        for kind, good, wrong, season, episode, category in (
            ("电影", "Right Film", "Wrong Film", None, None, "movie"),
            ("电视剧", "Expected Show", "Other Show", 1, 1, "tv"),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                repository, candidates, policy_module, planner = (load(name) for name in
                    ("repository", "candidates", "policy", "planner"))
                repo = repository.Repository(Path(directory) / "state.db")
                target = repository.Target(kind, "tmdb", "42", season)
                unit = planner.TargetUnit(target, episode).key
                path = (f"{wrong.replace(' ', '.')}.S01E01.1080p.WEB-DL-HHWEB.mkv" if season else
                        f"{wrong.replace(' ', '.')}.2026.2160p.REMUX.mkv")
                content = Bencode.encode({"info": {"name": path, "length": 100,
                    "piece length": 16384, "pieces": b"x" * 20}})

                class Adapter:
                    def recognize(self, meta, declared):
                        return NS(type=kind, title=good, original_title=good, year="2026")
                    def identity(self, media):
                        return ("tmdb", "42")
                    def classify(self, media):
                        return {"state": "complete", "policy_revision": 7,
                                "effective": {"category_id": category}}
                    def acquire(self, raw):
                        return content

                class Meta:
                    corrector = NS(revision="physical-name-test")
                    def parse(self, key, title, *args, **kwargs):
                        physical = kwargs.get("is_path", False)
                        name = wrong if physical else good
                        meta = NS(cn_name=None, en_name=name, year="2026",
                                  type=kind, begin_season=season, end_season=None,
                                  begin_episode=episode, end_episode=None)
                        return NS(status="OK", meta=meta,
                                  record=lambda: {"status": "OK", "corrected": {"en_name": name}})

                service = candidates.CandidateService(repo, Adapter())
                row = service.observe(dict(site=1, torrent_id="wrong-physical", title=
                    f"{good} S01E01 1080p WEB-DL-HHWEB 中文字幕" if season else
                    f"{good} 2026 2160p REMUX 中文字幕", description="", labels=[]))
                policy = policy_module.Policy({category: "欧美剧" if season else "外语电影"}, 7)
                pipeline = candidates.CandidatePipeline(service, Meta(), policy,
                    lambda keys: {unit: {"state": "MISSING", "revision": 0, "versions": []}},
                    lambda name: object())
                result = pipeline.evaluate(row["candidate_key"], target, [unit],
                    downloader="test", save_path="/test", assistance=False)
                self.assertEqual([], result["plans"])
                self.assertEqual("PHYSICAL_IDENTITY_UNCONFIRMED", result["reason"])


if __name__ == "__main__":
    unittest.main()
