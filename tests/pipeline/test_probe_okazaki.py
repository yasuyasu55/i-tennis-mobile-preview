import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
sys.path.insert(0, os.path.join(ROOT, "data-source"))
from datetime import date

from probe_okazaki import extract_events, _deadline_is_upcoming
from parsers import okazaki_tennis


FIXTURE = '''<!doctype html><html><body>
<div id="comp-mlpcrmqx1__item-01"><div><h4>岡崎シングルステニス大会</h4>
<div>〈開催日〉 11/29</div><div>〈予備日〉 12/6</div><div>〈種 目〉 男女シングルス</div>
<div>〈資 格〉 加盟員及び市内在住・在勤者（戦績制限あり）</div><div>〈場 所〉 岡崎中央総合公園</div>
<div>〈要 項〉 <a href="/_files/ugd/singles.pdf">詳細はこちら</a></div><div>申込締切日：10月26日</div>
<a href="https://ws.formzu.net/fgen/example">インターネット申込</a>
</div></div>
<div id="comp-mlpcrmqx1__item-02"><div><h4>岡崎チーム対抗戦</h4>
<div>〈開催日〉 12/5</div><div>〈予備日〉 12/13</div><div>〈種 目〉 男子の部・女子の部</div>
<div>〈資 格〉 加盟員のみ</div><div>〈場 所〉 岡崎中央総合公園</div>
<div>〈要 項〉 <a href="/_files/ugd/team.pdf">詳細はこちら</a></div><div>申込締切日：11月1日（日）</div>
<a href="https://ws.formzu.net/fgen/unsafe">インターネット申込</a>
</div></div></body></html>'''.encode("utf-8")


class ProbeParserTests(unittest.TestCase):
    def test_extracts_visible_fields_and_only_official_pdf_links(self):
        events = extract_events(FIXTURE)
        self.assertEqual(len(events), 2)
        singles, team = events
        self.assertEqual(singles["title"], "岡崎シングルステニス大会")
        self.assertEqual(singles["date_text"], "11/29")
        self.assertEqual(singles["eligibility"], "加盟員及び市内在住・在勤者（戦績制限あり）")
        self.assertTrue(singles["guideline_url"].endswith("/singles.pdf"))
        self.assertNotIn("formzu", singles["guideline_url"])
        self.assertEqual(team["deadline_text"], "11月1日（日）")

    def test_candidate_parser_matches_probe_on_same_fixture(self):
        probe = extract_events(FIXTURE)
        parsed = okazaki_tennis.parse_page(FIXTURE, page_url="https://www.okazaki-tennis.com/taikai-r8")
        self.assertIsNone(parsed["fatal_error"])
        self.assertEqual(len(probe), len(parsed["events"]))
        by_title = {e["title"]: e for e in parsed["events"]}
        for event in probe:
            candidate = by_title[event["title"]]
            for field in ("date_text", "events_text", "eligibility", "venue", "deadline_text", "guideline_url"):
                normalize = lambda x: " ".join((x or "").replace("\u200b", "").split())
                self.assertEqual(normalize(event.get(field)), normalize(candidate.get(field)), (event["title"], field))

    def test_upcoming_deadline_selection_around_october_2026(self):
        today = date(2026, 10, 9)
        self.assertTrue(_deadline_is_upcoming({"date_text": "11/29", "deadline_text": "10月26日"}, today))
        self.assertTrue(_deadline_is_upcoming({"date_text": "12/5", "deadline_text": "11月1日"}, today))
        self.assertFalse(_deadline_is_upcoming({"date_text": "10/31", "deadline_text": "9月28日"}, today))
        self.assertFalse(_deadline_is_upcoming({"date_text": "8/29", "deadline_text": "7月21日"}, today))


if __name__ == "__main__":
    unittest.main()
