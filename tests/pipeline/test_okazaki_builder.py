import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_tournaments_json as bt


SAMPLE = '''<!doctype html><html><body>
<div><h4>岡崎サンプルシングルス大会</h4><div>〈開催日〉 11/29</div><div>〈予備日〉 12/6</div>
<div>〈種目〉 男女シングルス</div><div>〈資格〉 オープン参加</div><div>〈場所〉 サンプルテニス会場</div><div>〈要項〉</div></div>
<div><h4>岡崎サンプルチーム対抗戦</h4><div>〈開催日〉 12/5</div><div>〈予備日〉 12/13</div>
<div>〈種目〉 男子の部・女子の部</div><div>〈資格〉 加盟員のみ</div><div>〈場所〉 サンプルテニス会場</div><div>〈要項〉</div></div>
<div><h4>岡崎サンプル春季大会</h4><div>〈開催日〉 1/17</div><div>〈予備日〉 1/24</div>
<div>〈種目〉 男女シングルス</div><div>〈資格〉 オープン参加</div><div>〈場所〉 サンプルテニス会場</div><div>〈要項〉</div></div>
<div><h4>岡崎サンプル夏季大会</h4><div>〈開催日〉 2/13</div><div>〈予備日〉 2/21</div>
<div>〈種目〉 男女ダブルス</div><div>〈資格〉 オープン参加</div><div>〈場所〉 サンプルテニス会場</div><div>〈要項〉</div></div>
<div><h4>岡崎サンプル秋季大会</h4><div>〈開催日〉 3/6</div><div>〈予備日〉 3/14</div>
<div>〈種目〉 男女ダブルス</div><div>〈資格〉 オープン参加</div><div>〈場所〉 サンプルテニス会場</div><div>〈要項〉</div></div>
</body></html>'''.encode("utf-8")


class OkazakiBuilderTests(unittest.TestCase):
    def tearDown(self):
        bt.PROVIDER = None

    def test_explicit_member_only_is_classified_without_changing_source_text(self):
        bt.PROVIDER = {bt.OKAZAKI_PAGE: SAMPLE}
        bt.configure(snapshot_dates={"okazaki": "2026-10-09"})
        out = bt.build_source("okazaki")
        team = next(r for r in out["records"] if "チーム対抗戦" in r["title"])
        self.assertEqual(team["eligibility_status"], "協会登録必要")
        self.assertEqual(team["eligibility_text"], "加盟員のみ")
        self.assertEqual(team["classification_basis"][-1]["matched"], "加盟員のみ")
        self.assertEqual(out["meta"]["record_count"], 5)

    def test_resident_worker_eligibility_does_not_gain_school_condition(self):
        raw = SAMPLE.replace("オープン参加".encode("utf-8"), "加盟員 及び 市内在住・在勤者（戦績制限あり）".encode("utf-8"), 1)
        bt.PROVIDER = {bt.OKAZAKI_PAGE: raw}
        bt.configure(snapshot_dates={"okazaki": "2026-10-09"})
        out = bt.build_source("okazaki")
        singles = next(r for r in out["records"] if "シングルス" in r["title"])
        self.assertNotIn("在学", singles["eligibility_note"])
        self.assertIn("加盟員 及び 市内在住・在勤者（戦績制限あり）", singles["eligibility_note"])
        audience_basis = next(b for b in singles["classification_basis"]
                              if b.get("field") == "audience"
                              and b.get("source") == "一覧表の参加資格欄")
        self.assertEqual(audience_basis["matched"], "加盟員 及び 市内在住・在勤者（戦績制限あり）")


if __name__ == "__main__":
    unittest.main()
