"""Anjo's fiscal-year tournament table; PDFs and historical results are separate."""
import datetime
import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit
from bs4 import BeautifulSoup

PAGE_URL = 'http://anjo-tennis.net/' + quote('大会情報') + '/'
SOURCE_ID = 'anjo_tennis_association'
SOURCE_NAME = '安城市テニス協会'
HEADERS = ['大会名','種目','期日','予備日','要項','ドロー・結果','参加資格']


def text(node):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', node.get_text(' ',strip=True))).strip()


def parse_date(raw, fiscal_year, optional=False):
    v = re.sub(r'\s+', '', unicodedata.normalize('NFKC',raw))
    if optional and re.fullmatch(r'(?:無し|なし|無)(?:\([月火水木金土日]\))?',v):
        return []
    m = re.fullmatch(r'(\d{1,2})/(\d{1,2})(?:\(([月火水木金土日])\))?',v)
    if not m:
        raise ValueError('日付の形式が変化しました: ' + raw)
    year = fiscal_year + (int(m[1]) < 4)
    day = datetime.date(year,int(m[1]),int(m[2]))
    check = 'not_available' if not m[3] else ('ok' if '月火水木金土日'[day.weekday()] == m[3] else 'mismatch')
    return [{'raw':raw,'normalized':day.isoformat(),'year':year,'weekday_check':check,
             'warnings':['公式の曜日表記と日付が一致しません。要項で確認してください'] if check == 'mismatch' else []}]


def safe_url(url):
    u = urljoin(PAGE_URL,url)
    p = urlsplit(u)
    if p.scheme not in ('http','https') or p.hostname != 'anjo-tennis.net':
        return None
    return quote(u,safe=':/%?=&#+')


def parse_page(raw):
    soup = BeautifulSoup(raw,'html.parser')
    tables = [t for t in soup.select('table') if t.find('tr') and
              [text(c) for c in t.find('tr').find_all(['td','th'],recursive=False)] == HEADERS]
    if len(tables) != 1:
        raise ValueError('大会表の列構造を確認できません')
    table = tables[0]
    heading = table.find_previous(['h2','h3','h4'])
    m = re.fullmatch(r'(\d{4})年度大会情報',text(heading)) if heading else None
    if not m:
        raise ValueError('大会表の年度見出しがありません')
    fy = int(m[1])
    entry_urls = {safe_url(a['href']) for a in soup.select('a[href]')
                  if text(a) == '大会・イベント申込' and urlsplit(urljoin(PAGE_URL,a['href'])).path == '/entry/'} - {None}
    entry_url = next(iter(entry_urls)) if len(entry_urls) == 1 else None
    events, excluded = [], []
    for row in table.select('tr')[1:]:
        cells = row.find_all(['td','th'],recursive=False)
        if len(cells) != 7:
            raise ValueError('大会行の列数が変化しました')
        title, kinds, date_text, reserve_text, _, _, eligibility = [text(c) for c in cells]
        title = re.sub(r'^[★☆]+','',title).strip()
        if not title or not kinds or not eligibility:
            raise ValueError('大会名・種目・参加資格が空です')
        periods = parse_date(date_text,fy)
        reserves = parse_date(reserve_text,fy,optional=True)
        if reserves and not 0 < (datetime.date.fromisoformat(reserves[0]['normalized'])-datetime.date.fromisoformat(periods[0]['normalized'])).days <= 60:
            raise ValueError('開催日と予備日の関係を確認できません')
        if title.startswith('西三河マスターズ'):
            excluded.append(title)
            continue
        links = [safe_url(a['href']) for a in cells[4].select('a[href]') if text(a)=='要項']
        links = [u for u in links if u and urlsplit(u).path.lower().endswith('.pdf')]
        if len(links)>1:
            raise ValueError('要項リンクが複数あります')
        events.append({'title':title,'kinds':kinds,'date_text':date_text,'periods':periods,
                       'reserve_text':reserve_text if reserves else None,'reserve_periods':reserves,
                       'eligibility':eligibility,'guideline_url':links[0] if links else None,
                       'entry_url':entry_url})
    if not events:
        raise ValueError('大会一覧が空です')
    return {'fiscal_year':fy,'events':events,'excluded':excluded}
