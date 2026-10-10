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


def anjo_fixture(year=2026):
    header = '<tr>' + ''.join('<th>'+h+'</th>' for h in bt.aj.HEADERS) + '</tr>'
    rows = [
        ['★架空大会(一般)','男女一般S','12/5','無し','<a href="/rules.pdf">要項</a>','<a href="/result.pdf">結果</a>','オープン※戦績制限あり'],
        ['架空大会(ベテラン)','男女ベテランD(45歳以上)','1/30','なし','要項','ドロー','オープン'],
        ['架空市選手権','男女S','11/15','11/29','要項','ドロー','安城市在住、在勤、在学'],
        ['西三河マスターズ(ダイセンアリーナ西尾)','男女D','2/21','なし','要項','ドロー','大会ベスト8以上']]
    return ('<a href="/entry/">大会・イベント申込</a><h3>%d年度大会情報</h3><table>' % year + header + ''.join('<tr>'+''.join('<td>'+v+'</td>' for v in row)+'</tr>' for row in rows)+'</table><h3>2025年度大会情報</h3>').encode()


def kariya_fixture(year=2026):
    header = '<tr>' + ''.join('<th>'+h+'</th>' for h in bt.ky.HEADERS) + '</tr>'
    rows = [[f'{year}年6月28日/予7月5日','架空チーム団体戦','一般女子ダブルス+一般男子ダブルス+一般ミックスダブルス'],
            [f'{year+1}年3月21日/予28日','架空ミックス大会','一般ミックスダブルス']]
    return ('<p>令和%d年度の大会予定</p><table>' % (year-2018) + header + ''.join('<tr>'+''.join('<td>'+v+'</td>' for v in row)+'</tr>' for row in rows)+'</table>').encode('shift_jis')


class AreaExpansionTests(unittest.TestCase):
    def setUp(self):
        self.provider = bt.PROVIDER
        bt.PROVIDER={'anjo_tournament.html':anjo_fixture(),'kariya_schedule.html':kariya_fixture()}
        bt.configure(snapshot_dates={'anjo':'2026-10-10','kariya':'2026-10-10'})
    def tearDown(self):
        bt.PROVIDER=self.provider
    def test_anjo_real_rule_links_only_and_other_city_excluded(self):
        rows=bt.build_source('anjo')['records']
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['guideline_url'],'http://anjo-tennis.net/rules.pdf')
        self.assertIsNone(rows[1]['guideline_url'])
        self.assertEqual(rows[0]['entry_url'],'http://anjo-tennis.net/entry/')
        self.assertTrue(all('result.pdf' not in str(r) for r in rows))
        self.assertEqual(validate.validate_records(rows),[])
    def test_anjo_fiscal_year_and_reserves(self):
        rows=bt.build_source('anjo')['records']
        self.assertEqual(rows[1]['periods'][0]['normalized'],'2027-01-30')
        self.assertEqual(rows[2]['reserve_periods'][0]['normalized'],'2026-11-29')
        self.assertEqual(len(rows[2]['periods']),1)
        bt.PROVIDER['anjo_tournament.html']=anjo_fixture(2027)
        self.assertEqual(bt.build_source('anjo')['records'][1]['periods'][0]['normalized'],'2028-01-30')
    def test_anjo_shorthand_and_participation_conditions(self):
        rows=bt.build_source('anjo')['records']
        self.assertEqual(rows[0]['primary_event_types'],['シングルス'])
        self.assertEqual(rows[1]['primary_event_types'],['ダブルス'])
        self.assertEqual(rows[1]['audience_types'],['ベテラン'])
        self.assertEqual(rows[2]['eligibility_status'],'地域条件あり')
        self.assertIn('戦績制限',rows[0]['eligibility_text'])
        self.assertIsNone(rows[0]['fee_text'] if 'fee_text' in rows[0] else None)
    def test_anjo_invalid_structure_and_date_stop_source(self):
        for raw in [anjo_fixture().replace('期日'.encode(),'日付変更'.encode()),anjo_fixture().replace(b'12/5',b'12/99'),anjo_fixture().replace(b'11/29',b'10/29')]:
            bt.PROVIDER['anjo_tournament.html']=raw
            with self.assertRaises(bt.BuildError): bt.build_source('anjo')
    def test_anjo_does_not_adopt_external_or_script_rule_links(self):
        for link in ['javascript:alert(1)','https://other.example/rules.pdf']:
            bt.PROVIDER['anjo_tournament.html']=anjo_fixture().replace(b'/rules.pdf',link.encode())
            self.assertIsNone(bt.build_source('anjo')['records'][0]['guideline_url'])
    def test_kariya_shift_jis_dates_and_team_components(self):
        rows=bt.build_source('kariya')['records']
        self.assertEqual(rows[0]['periods'][0]['normalized'],'2026-06-28')
        self.assertEqual(rows[0]['reserve_periods'][0]['normalized'],'2026-07-05')
        self.assertEqual(rows[0]['primary_event_types'],['団体戦'])
        self.assertEqual(set(rows[0]['component_match_types']),{'ミックス','ダブルス'})
        self.assertEqual(rows[1]['periods'][0]['normalized'],'2027-03-21')
        self.assertEqual(rows[1]['reserve_periods'][0]['normalized'],'2027-03-28')
        self.assertEqual(validate.validate_records(rows),[])
    def test_kariya_invalid_or_wrong_year_stops_source(self):
        for raw in [kariya_fixture().replace('日程'.encode('shift_jis'),'変更'.encode('shift_jis')),kariya_fixture().replace(b'28',b'99'),kariya_fixture().replace(b'2026',b'2025')]:
            bt.PROVIDER['kariya_schedule.html']=raw
            with self.assertRaises(bt.BuildError): bt.build_source('kariya')
    def test_schedule_change_preserves_ids(self):
        for key, name, before, after in [('anjo','anjo_tournament.html',b'12/5',b'12/6'),('kariya','kariya_schedule.html',b'6\x8c\x8e28',b'6\x8c\x8e27')]:
            old=bt.build_source(key)['records']
            bt.PROVIDER[name]=bt.PROVIDER[name].replace(before,after)
            new=bt.build_source(key)['records']
            self.assertEqual([r['id'] for r in old],[r['id'] for r in new])
    def test_fetch_failure_keeps_both_sources_exactly(self):
        previous=bt.assemble([bt.build_source(k) for k in ('anjo','kariya')])
        registry=json.loads((ROOT/'pipeline/sources.json').read_text())
        fetcher=PoliteFetcher(registry['user_agent'],delay=0,http=lambda *args:(503,{},b''))
        result=ru.run(registry,copy.deepcopy(previous),{},fetcher,datetime.datetime(2026,10,10,tzinfo=ru.JST),only=['anjo','kariya'])
        self.assertEqual(result['data']['tournaments'],previous['tournaments'])
        self.assertEqual({r['status'] for r in result['summary']['results'].values()},{'failed'})

if __name__=='__main__': unittest.main()
