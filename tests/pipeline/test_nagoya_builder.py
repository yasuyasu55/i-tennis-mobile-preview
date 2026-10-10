"""Synthetic DOM fixtures only; no copies of official HTML."""
import copy
import datetime
import json
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
for p in ('tools','pipeline','data-source'):
    sys.path.insert(0,str(ROOT/p))
import build_tournaments_json as bt
import run_update as ru
import validate
from fetcher import PoliteFetcher


def fixture():
    fields = [('開催日時','令和8年6/7（日） 8時集合'),('参加資格','6名以上・20歳以上（学生不可）'),
              ('ル l ル進行等','男子Ｄ・女子Ｄ・ミックスで対戦する'),
              ('参加申し込み','令和8年4月1日（水）20時より先着順にて受付'),
              ('参加費','1チーム9,000円'),('場所','架空テニスコート')]
    rows = ''.join('<tr><td>'+k+'</td><td>'+v+'</td></tr>' for k,v in fields)
    return ('<p>社会人のアマチュアの男女</p><p>年会費3,000円 駐車料500円</p>'
            '<li>名古屋テニス協会主催の大会はアマチュアの方のみ参加できます。</li>'
            '<section class="pera1-section" id="plans"><h2>試合開催予定<br>２０２６年度開催予定日<br>'
            '2027年 1/24（日）ミックス<br>2027年 3/14（日）ミックス</h2></section>'
            '<section class="pera1-section" id="detail"><h2>6/7（日）団体戦 ご案内 終了</h2><table>'+rows+
            '<tr class="pera1-ghost pera1-add" style="opacity: 0"><td>参加資格</td><td>50歳以上</td></tr></table></section>'
            '<a href="https://form1ssl.fc2.com/form/?id=fiction">6/7（日）団体戦 申し込みフォーム</a>'
            '<h2>3/29（2026年）ミックス ギャラリー</h2><h2>1/18（2026年）練習会 ギャラリー</h2>'
            '<h2>11/30団体戦 ギャラリー</h2><table><tr><td>役員</td><td>架空A</td></tr><tr><td>役員</td><td>架空B</td></tr></table>').encode()


class NagoyaTests(unittest.TestCase):
    def setUp(self):
        self.provider=bt.PROVIDER
        bt.PROVIDER={'nagoya_tournament.html':fixture()}
        bt.configure(snapshot_dates={'nagoya':'2026-10-10'})
    def tearDown(self):
        bt.PROVIDER=self.provider
    def rows(self):
        return bt.build_source('nagoya')['records']
    def change(self,a,b):
        bt.PROVIDER['nagoya_tournament.html']=fixture().replace(a.encode(),b.encode())
    def test_explicit_future_year_fiscal_year_and_independent_region(self):
        rows=self.rows()
        self.assertEqual([r['periods'][0]['normalized'] for r in rows],['2027-01-24','2027-03-14','2026-06-07'])
        self.assertTrue(all(r['fiscal_year']==2026 and r['event_area']=='名古屋' for r in rows))
        self.assertEqual(validate.validate_records(rows),[])
    def test_planned_events_do_not_inherit_other_event_details(self):
        for r in self.rows()[:2]:
            for field in ('venue','fee_text','entry_url','guideline_url','deadline_date','entry_text'):
                self.assertIsNone(r[field])
            self.assertNotIn('20歳',r['eligibility_text'])
            self.assertNotIn('50歳',str(r))
            self.assertEqual(r['primary_event_types'],['ミックス'])
    def test_detail_hidden_templates_excluded_and_application_start_not_deadline(self):
        r=self.rows()[2]
        self.assertNotIn('50歳',str(r))
        self.assertEqual(r['fee_text'],'1チーム9,000円')
        self.assertEqual(r['venue'],'架空テニスコート')
        self.assertEqual(r['primary_event_types'],['団体戦'])
        self.assertEqual(set(r['component_match_types']),{'ダブルス','ミックス'})
        self.assertIn('20時より',r['entry_text'])
        self.assertIsNone(r['deadline_date'])
        self.assertIsNone(r['deadline_text'])
        self.assertTrue(r['entry_url'].endswith('fiction'))
    def test_date_change_same_month_preserves_ids_and_warns_weekday(self):
        before=self.rows()
        self.change('1/24','1/25')
        after=self.rows()
        self.assertEqual([r['id'] for r in before],[r['id'] for r in after])
        self.assertEqual(after[0]['periods'][0]['weekday_check'],'mismatch')
        self.assertTrue(after[0]['warnings'])
    def test_date_or_structure_change_fails_closed(self):
        for a,b in [('2027年 1/24','1/24'),('2027年 1/24','2026年 1/24'),('1/24','1/99'),
                    ('年度開催予定日','年度予定変更'),('参加費','費用変更'),('3/14（日）ミックス','3/14（日）練習会')]:
            self.change(a,b)
            with self.assertRaises(bt.BuildError): self.rows()
    def test_duplicate_same_month_fails_closed(self):
        self.change('3/14（日）ミックス','1/31（日）ミックス')
        with self.assertRaises(bt.BuildError):self.rows()
    def test_duplicate_plan_and_detail_merge(self):
        self.change('2027年 3/14（日）ミックス','2026年 6/7（日）団体戦')
        rows=self.rows()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['venue'],'架空テニスコート')
    def test_unsafe_or_wrong_event_form_not_attached(self):
        for a,b in [('https://form1ssl.fc2.com/form/?id=fiction','javascript:alert(1)'),
                    ('https://form1ssl.fc2.com/form/?id=fiction','https://other.example/form'),
                    ('6/7（日）団体戦 申し込みフォーム','6/14（日）団体戦 申し込みフォーム')]:
            self.change(a,b)
            self.assertIsNone(self.rows()[2]['entry_url'])
    def test_fetch_failure_keeps_previous_exactly(self):
        previous=bt.assemble([bt.build_source('nagoya')])
        registry=json.loads((ROOT/'pipeline/sources.json').read_text())
        fetcher=PoliteFetcher(registry['user_agent'],delay=0,http=lambda *args:(503,{},b''))
        result=ru.run(registry,copy.deepcopy(previous),{},fetcher,datetime.datetime(2026,10,10,tzinfo=ru.JST),only=['nagoya'])
        self.assertEqual(result['data']['tournaments'],previous['tournaments'])
        self.assertEqual(result['summary']['results']['nagoya']['status'],'failed')

if __name__=='__main__': unittest.main()
