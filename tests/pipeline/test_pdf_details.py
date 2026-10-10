import copy
import os
import sys
import unittest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path[:0] = [os.path.join(ROOT, 'data-source'), os.path.join(ROOT, 'pipeline')]
from parsers import okazaki_pdf as op, hamamatsu_pdf_table as ht
import plans
from fetcher import FetchError, FetchResult

TEAM = '''2026 架空チーム大会
日程/種目
日 程
12月5日(土)
雨天予備日 12月13日(日)
参加資格 サンプル加盟員
参 加 費 1チーム 8,500円
申込締切日
インターネット申込 令和8年11月1日(日)
郵送申込 令和8年10月30日(金)(当日消印可)
申込方法 サンプル窓口
'''
EVENT = {'title':'架空チーム大会', 'periods':[{'normalized':'2026-12-05'}]}
TABLE = '''第80回 浜松市民スポーツ祭 (テニス競技)
      種目                       開催日          申込締切
        一般男子 年齢制限なし
                               9月20日 (日)    8月28日 (金)
     一般ダブルス
        一般女子 年齢制限なし
        一般 年齢制限なし
                               10月11日 (日)   9月18日 (金)
     混合ダブルス
        40歳以上
主 催          サンプル市
参 加 料       団体 男子・女子 10,000円
              一般・混合ダブルス 3,500円
              小中学生ダブルス 2,500円
申込締切       上記 各18:00必着
申 込 先       サンプル申込窓口
注意事項       所定用紙を使う
'''
SPORTS = {'title':'第80回浜松市スポーツ際 一般ダブルス・混合ダブルス',
          'folder_year':2026, 'periods':[{'normalized':'2026-09-20'}, {'normalized':'2026-10-11'}]}


class PdfDetailTests(unittest.TestCase):
    def test_distinct_online_and_postal_deadlines(self):
        result = op.parse(TEAM, EVENT)
        self.assertTrue(result['ok'])
        self.assertEqual([(d['mode'],d['date']) for d in result['deadlines']],
                         [('インターネット','2026-11-01'),('郵送','2026-10-30')])
        self.assertEqual(result['fee_lines'],['1チーム 8,500円'])

    def test_wrong_title_is_rejected(self):
        self.assertFalse(op.parse(TEAM.replace('架空チーム大会','別大会'), EVENT)['ok'])

    def test_wrong_weekday_is_rejected(self):
        self.assertFalse(op.parse(TEAM.replace('12月5日(土)','12月5日(日)'), EVENT)['ok'])

    def test_reserve_date_is_not_main_event_date(self):
        e=copy.deepcopy(EVENT); e['periods'][0]['normalized']='2026-12-13'
        self.assertFalse(op.parse(TEAM,e)['ok'])

    def test_explicit_deadline_year_required(self):
        self.assertFalse(op.parse(TEAM.replace('令和8年',''),EVENT)['ok'])

    def test_duplicate_method_is_ambiguous(self):
        self.assertFalse(op.parse(TEAM.replace('郵送申込','インターネット申込'),EVENT)['ok'])

    def test_sports_festival_preserves_each_deadline(self):
        result=ht.parse(TABLE, SPORTS)
        self.assertEqual(result['fee_text'],'一般・混合ダブルス 3,500円')
        self.assertIn('2026-08-28',result['deadline_notes'][0])
        self.assertIn('2026-09-18',result['deadline_notes'][1])
        self.assertNotIn('deadline_date',result)

    def test_wrong_festival_number_is_rejected(self):
        self.assertIsNone(ht.parse(TABLE.replace('第80回','第81回'),SPORTS))

    def test_ambiguous_or_broken_table_is_not_adopted(self):
        self.assertIsNone(ht.parse(TABLE.replace('10月11日 (日)','10月11日 (土)'),SPORTS))
        self.assertIsNone(ht.parse(TABLE.replace('3,500円','金額未定'),SPORTS))

    def test_source_event_dates_are_not_corrected(self):
        event=copy.deepcopy(SPORTS)
        event['periods'][0]['normalized']='2025-09-20'
        before=copy.deepcopy(event)
        self.assertIsNotNone(ht.parse(TABLE,event))
        self.assertEqual(event,before)

    def test_okazaki_pdf_failure_keeps_listing_inputs(self):
        page=b'<html>listing</html>'
        event={'title':'example','periods':[{'normalized':'2026-12-05'}],
               'guideline_url':'https://www.okazaki-tennis.com/a.pdf'}
        class Fake:
            def get(self,url,limit):
                if url.endswith('.pdf'): raise FetchError('http','HTTP 503',url,503)
                return FetchResult(url,200,page,'text/html')
        old=plans.ok.parse_page
        plans.ok.parse_page=lambda p:{'events':[event]}
        try:
            result=plans.plan_okazaki({'pages':[{'url':'https://www.okazaki-tennis.com/list','name':'okazaki_tournament.html'}],
                                      'pdf':{'max':4,'allowed_host_suffixes':['www.okazaki-tennis.com']}},
                                     {'max_html_bytes':3000000,'max_pdf_bytes':4000000},Fake(),'2026-10-10',lambda p:'')
            self.assertEqual(result.inputs['okazaki_tournament.html'],page)
            self.assertEqual(result.stats['pdf_fetch_failed'],1)
        finally:
            plans.ok.parse_page=old


if __name__ == '__main__':
    unittest.main()
