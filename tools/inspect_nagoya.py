import sys,json,pathlib
sys.path.insert(0,'pipeline')
from fetcher import PoliteFetcher,FetchError
c=json.loads(pathlib.Path('pipeline/sources.json').read_text());f=PoliteFetcher(c['user_agent'],delay=3,timeout=25,max_requests=6)
p=pathlib.Path('nagoya-evidence');p.mkdir(exist_ok=True)
try:
 r=f.get('https://peraichi.com/landing_pages/view/nagoyatennis/',c['max_html_bytes'])
 (p/'nagoya.html').write_bytes(r.body)
 from bs4 import BeautifulSoup
 soup=BeautifulSoup(r.body,'html.parser')
 (p/'nagoya.txt').write_text(soup.get_text('\n',strip=True))
 print(r.status,len(r.body))
except FetchError as ex:print(ex.stage,ex.message)
(p/'fetch-log.json').write_text(json.dumps(f.log,ensure_ascii=False,indent=2))
