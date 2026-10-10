"""Read official listings on the normal GitHub runner, with the production fetch policy."""
import json
from pathlib import Path
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from fetcher import PoliteFetcher

f = PoliteFetcher('I-Tennis-Mobile-DataBot/1.0 (+https://github.com/yasuyasu55/i-tennis-mobile-preview)', timeout=20, max_requests=12)
urls = [('anjo', 'https://anjo-tennis.net/' + quote('大会情報') + '/'),
        ('kariya_menu', 'https://www.katch.ne.jp/~fmhmksy/newpage2.htm'),
        ('kariya_top', 'https://www.katch.ne.jp/~fmhmksy/renmeitop.htm')]
Path('pipeline/out-probe').mkdir(exist_ok=True)
for name, url in urls:
    try:
        r = f.get(url, 3000000)
        Path('pipeline/out-probe/' + name + '.html').write_bytes(r.body)
        soup = BeautifulSoup(r.body, 'html.parser', from_encoding='shift_jis' if 'kariya' in name else 'utf-8')
        print('PAGE', name, url, len(r.body), flush=True)
        print('TEXT', soup.get_text(' ', strip=True), flush=True)
        for table in soup.select('table'):
            print('TABLE', json.dumps([{'cells':[c.get_text(' ',strip=True) for c in row.find_all(['td','th'],recursive=False)],'links':[(a.get_text(' ',strip=True),urljoin(url,a['href'])) for a in row.select('a[href]')]} for row in table.select('tr')],ensure_ascii=False),flush=True)
        print('LINKS', json.dumps([(a.get_text(' ',strip=True),urljoin(url,a['href'])) for a in soup.select('a[href]')],ensure_ascii=False),flush=True)
    except Exception as ex:
        print('FAILED', name, str(ex), flush=True)
print('FETCH_LOG', json.dumps(f.log,ensure_ascii=False),flush=True)
