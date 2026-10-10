import sys,json,pathlib
sys.path[:0]=['pipeline','data-source']
from fetcher import PoliteFetcher
from parsers import anjo_tennis,toyota_tennis
from pdftext import pdf_to_layout
cfg=json.loads(pathlib.Path('pipeline/sources.json').read_text())
f=PoliteFetcher(cfg['user_agent'],delay=3,timeout=20,max_requests=48)
out=pathlib.Path('detail-evidence');out.mkdir(exist_ok=True)
for key,parser,url in [('anjo',anjo_tennis,anjo_tennis.PAGE_URL),('toyota',toyota_tennis,toyota_tennis.PAGE_URL),('kariya',None,'https://www.katch.ne.jp/~fmhmksy/renmeitop.htm')]:
 raw=f.get(url,cfg['max_html_bytes']).body
 (out/(key+'.html')).write_bytes(raw)
 events=parser.parse_page(raw)['events'] if parser else [{'title':'テニス選手権大会','guideline_url':'https://www.katch.ne.jp/~fmhmksy/20261101_entry.pdf'}]
 for n,e in enumerate(events):
  if parser and max(p['normalized'] for p in e['periods'])<'2026-10-10':continue
  if not e.get('guideline_url'):continue
  try:
   pdf=f.get(e['guideline_url'],cfg['max_pdf_bytes']).body
   name=key+'-'+str(n)
   (out/(name+'.pdf')).write_bytes(pdf)
   (out/(name+'.txt')).write_text(pdf_to_layout(pdf))
   (out/(name+'.json')).write_text(json.dumps(e,ensure_ascii=False))
   print(name,e['title'])
  except Exception as ex:print(key,e['title'],str(ex))
