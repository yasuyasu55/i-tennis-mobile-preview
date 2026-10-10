"""実取得の候補を現在の公開データと比較する。書き込みは報告だけ。"""
import json
from pathlib import Path

old = json.loads(Path('baseline-tournaments.json').read_text())
new = json.loads(Path('pipeline/out/proposed/tournaments.json').read_text())
summary = json.loads(Path('pipeline/out/run-summary.json').read_text())
targets = {'okazaki_tennis_association', 'hamamatsu_tennis_association'}
a = {r['id']: r for r in old['tournaments']}
b = {r['id']: r for r in new['tournaments']}
assert not set(a) - set(b), '既存大会が消失'
assert all(r['status']=='ok' for r in summary['results'].values()), summary['results']
assert not summary['validation_errors'], summary['validation_errors']
unchanged = [r for r in old['tournaments'] if r['source_id'] not in targets]
assert unchanged == [r for r in new['tournaments'] if r['source_id'] not in targets], '対象外の大会が変化'
classification = ('event_types','primary_event_types','component_match_types','audience_types','eligibility_status','eligibility_text','eligibility_note','classification_basis')
changed = []
for rid in set(a) & set(b):
    for field in classification:
        assert a[rid].get(field)==b[rid].get(field), (rid,field,'分類が変化')
    for field in ('official_url','guideline_url','venue','deadline_date','fee_text','entry_text'):
        if a[rid].get(field) not in (None,'',[]) and b[rid].get(field) in (None,'',[]):
            raise AssertionError((rid,field,'確認済み値が消失'))
    fields = [k for k in ('fee_text','entry_text','deadline_text','deadline_date','notes') if a[rid].get(k)!=b[rid].get(k)]
    if fields:
        changed.append({'title':b[rid]['title'],'fields':fields,'fee_text':b[rid].get('fee_text'),
                        'deadline_date':b[rid].get('deadline_date'),
                        'deadline_notes':[n for n in b[rid].get('notes',[]) if n.startswith('要項PDFの') and '申込締切' in n]})
report={'previous_count':len(a),'candidate_count':len(b),'unrelated_unchanged':len(unchanged),
        'existing_classification_unchanged':True,'changed_details':changed,
        'new_records':[r['title'] for rid,r in b.items() if rid not in a]}
Path('pipeline/out/detail-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False,indent=2))
