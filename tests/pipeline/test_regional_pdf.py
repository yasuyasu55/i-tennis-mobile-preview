import sys
from pathlib import Path
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[2]/'data-source'),str(Path(__file__).resolve().parents[2]/'pipeline')]
from parsers import regional_pdf as p
from run_update import preserve_unread_optional_fields

TEXT='''2026年度 架空シングルス 開催要項
■ 日程 開催日2026年11月21日(土)
■ 会場 架空テニスコート
■ 参加資格 オープン
■ 参加料 協会員2,000円 非協会員3,000円
■ 申込締切 10月31日(土)まで
■ 注意事項 申込締切後はキャンセル料が発生
'''
EV={'title':'架空シングルス','periods':[{'normalized':'2026-11-21'}]}
class RegionalDetails(unittest.TestCase):
 def test_fields_and_deadline(self):
  r=p.parse(TEXT,EV,'anjo');self.assertTrue(r['matched']);self.assertEqual(r['fields']['deadline_date'],'2026-10-31');self.assertEqual(r['fields']['fee_text'],'協会員2,000円 非協会員3,000円')
 def test_wrong_title_and_fiscal_year_rejected(self):
  for t in (TEXT.replace('架空シングルス','別大会'),TEXT.replace('2026年度','2025年度')):self.assertFalse(p.parse(t,EV,'anjo')['matched'])
 def test_wrong_month_day_rejected(self):
  self.assertFalse(p.parse(TEXT.replace('11月21日','11月22日'),EV,'anjo')['matched'])
 def test_conflicting_year_is_never_corrected(self):
  r=p.parse(TEXT.replace('2026年11','2025年11'),EV,'anjo');self.assertTrue(r['matched']);self.assertTrue(r['warnings']);self.assertNotIn('periods',r['fields'])
 def test_conflicting_weekday_not_normalized(self):
  r=p.parse(TEXT.replace('10月31日(土)','10月31日(日)'),EV,'anjo');self.assertIsNone(r['fields']['deadline_date']);self.assertIn('10月31日',r['fields']['deadline_text'])
 def test_multiple_fees_and_qualifications_retained(self):
  t=TEXT+'■ 参加資格 初級者、過去優勝者は参加出来ません\n■ 参加料 無料\n■ 表彰 賞品\n'
  r=p.parse(t,EV,'anjo');self.assertIn('無料',r['fields']['fee_text']);self.assertIn('優勝者',r['fields']['eligibility_text'])
 def test_class_specific_conditions_all_retained(self):
  text=TEXT+"■ 種目 女子A級 過去優勝者も出場可能\n女子B級 過去A・B級優勝者以外は出場可能\n女子C級 過去B級ベスト4以上は出場できません\n■ 表彰 賞品\n"
  r=p.parse(text,EV,'anjo')
  for category in ('女子A級','女子B級','女子C級'):self.assertIn(category,r['fields']['eligibility_text'])
 def test_repeated_optional_failures_keep_pdf_conditions(self):
  old={'source_id':'anjo_tennis_association','venue':'既知コート','fee_text':'2000円','eligibility_text':'年齢条件','eligibility_status':'年齢条件あり','eligibility_note':'要項確認','audience_types':['ベテラン'],'classification_basis':[{'field':'eligibility','source':'要項PDFの資格欄'}],'acquisition':{'pdf_state':'parsed_details'}}
  candidate={'source_id':old['source_id'],'eligibility_text':'オープン','audience_types':[],'classification_basis':[],'acquisition':{'pdf_state':'fetch_failed'}}
  for _ in range(2):
   old=preserve_unread_optional_fields(old,candidate)
   self.assertEqual(old['venue'],'既知コート');self.assertEqual(old['eligibility_text'],'年齢条件');self.assertEqual(old['audience_types'],['ベテラン'])
