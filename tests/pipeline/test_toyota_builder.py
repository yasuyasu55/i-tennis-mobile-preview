import copy
import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("tools", "pipeline", "data-source"):
    sys.path.insert(0, str(ROOT / p))
from parsers import toyota_tennis as ty
import build_tournaments_json as bt
import run_update as ru
import validate
from fetcher import PoliteFetcher


def fixture(year=2026):
    header = "<tr><td></td>" + "".join("<td>%s</td>" % h for h in ty.HEADERS) + "</tr>"
    rows = []
    for i in range(1, 9):
        title = "架空大会%d(シングルス)" % i
        date = "11/8,15<br>11/29" if i == 7 else ("%d年<br>1/10" % (year+1) if i == 8 else "9/27<br>10/11")
        period = "10/2-10/11" if i == 7 else ("11/27-12/6" if i == 8 else "8/21-8/30")
        guideline = '<a href="/test.pdf">PDF</a>' if i == 7 else 'PDF'
        entry = '<a href="https://business.form-mailer.jp/fms/test">申込</a>' if i == 7 else '<a href="/entries.pdf">エントリーリスト</a>'
        rows.append("<tr>" + "".join("<td>%s</td>" % v for v in [str(i), title, date, guideline, period, entry, "PDF", '<a href="/results.pdf">PDF</a>']) + "</tr>")
    rows.insert(6, '<tr><td colspan="8">Grow Up(ダブルス練習会)</td></tr>')
    return ("<p>%d年度 大会</p><table>" % year + header + "".join(rows) + "</table>").encode()


class ToyotaTests(unittest.TestCase):
    def setUp(self):
        self.provider = bt.PROVIDER
        bt.PROVIDER = {"toyota_tournament.html": fixture()}
        bt.configure(snapshot_dates={"toyota": "2026-10-10"})

    def tearDown(self):
        bt.PROVIDER = self.provider

    def test_only_numbered_tournaments_and_real_links(self):
        rows = bt.build_source("toyota")["records"]
        self.assertEqual(len(rows), 8)
        self.assertIsNone(rows[0]["guideline_url"])
        self.assertIsNone(rows[0]["entry_url"])
        self.assertEqual(rows[6]["guideline_url"], "https://www.toyota-ta.jp/test.pdf")
        self.assertIn("form-mailer", rows[6]["entry_url"])
        self.assertEqual(validate.validate_records(rows), [])
        self.assertTrue(all(r["venue"] is None and r["eligibility_status"] == "参加資格要確認" for r in rows))

    def test_dates_and_year_boundary(self):
        rows = bt.build_source("toyota")["records"]
        self.assertEqual([p["normalized"] for p in rows[6]["periods"]], ["2026-11-08", "2026-11-15", "2026-11-29"])
        self.assertEqual(rows[7]["periods"][0]["normalized"], "2027-01-10")
        self.assertEqual(rows[7]["deadline_date"], "2026-12-06")
        self.assertEqual(rows[7]["fiscal_year"], 2026)

    def test_fiscal_year_is_not_fixed(self):
        bt.PROVIDER = {"toyota_tournament.html": fixture(2027)}
        rows = bt.build_source("toyota")["records"]
        self.assertEqual(rows[-1]["periods"][0]["normalized"], "2028-01-10")

    def test_structure_and_invalid_dates_fail_closed(self):
        for raw in (fixture().replace('開催日'.encode(), '日付変更'.encode()),
                    fixture().replace(b'9/27', b'9/99'), fixture().replace(b'2026', b'2025', 1)):
            bt.PROVIDER = {"toyota_tournament.html": raw}
            with self.assertRaises(bt.BuildError):
                bt.build_source("toyota")

    def test_schedule_change_preserves_stable_id(self):
        before = bt.build_source("toyota")["records"]
        bt.PROVIDER = {"toyota_tournament.html": fixture().replace(b'9/27', b'9/28')}
        after = bt.build_source("toyota")["records"]
        self.assertEqual([r["id"] for r in before], [r["id"] for r in after])

    def test_fetch_failure_keeps_previous_toyota(self):
        source = bt.build_source("toyota")
        prev = bt.assemble([source])
        registry = json.loads((ROOT / 'pipeline/sources.json').read_text())
        def failed(url, headers, timeout, limit):
            return 503, {}, b''
        f = PoliteFetcher(registry['user_agent'], delay=0, http=failed)
        result = ru.run(registry, copy.deepcopy(prev), {}, f,
                        datetime.datetime(2026,10,10,tzinfo=ru.JST), only=['toyota'])
        self.assertEqual(result['data']['tournaments'], prev['tournaments'])
        self.assertEqual(result['summary']['results']['toyota']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
