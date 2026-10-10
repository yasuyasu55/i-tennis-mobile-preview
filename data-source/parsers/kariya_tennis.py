"""Parse Kariya's official annual schedule, keeping reserve dates separate."""
import datetime
import re
import unicodedata
from bs4 import BeautifulSoup

PAGE_URL = 'https://www.katch.ne.jp/~fmhmksy/newpage6.htm'
SOURCE_ID = 'kariya_tennis_association'
SOURCE_NAME = '刈谷市テニス連盟'
HEADERS = ['日程', '大会', '概要']


def text(node):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', node.get_text(' ', strip=True))).strip()


def schedule_dates(raw, fiscal_year):
    v = re.sub(r'\s+', '', unicodedata.normalize('NFKC', raw))
    m = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日(?:/予(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日)?', v)
    if not m:
        raise ValueError('開催日・予備日の形式が変化しました: ' + raw)
    day = datetime.date(int(m[1]), int(m[2]), int(m[3]))
    if day.year - (day.month < 4) != fiscal_year:
        raise ValueError('開催年と年度が一致しません')
    def period(d, label):
        return {'raw': label, 'normalized': d.isoformat(), 'year': d.year,
                'weekday_check': 'not_available', 'warnings': []}
    reserves = []
    if m[6]:
        year, month = int(m[4] or day.year), int(m[5] or day.month)
        if not m[4] and month < day.month:
            year += 1
        reserve = datetime.date(year, month, int(m[6]))
        if not 0 < (reserve-day).days <= 31:
            raise ValueError('開催日と予備日の関係を確認できません')
        reserves = [period(reserve, raw.split('/')[1])]
    return [period(day, raw.split('/')[0])], reserves


def parse_page(raw):
    soup = BeautifulSoup(raw, 'html.parser', from_encoding='shift_jis' if isinstance(raw, bytes) else None)
    heading = re.search(r'令和\s*(\d+|元)\s*年度の大会予定', text(soup))
    if not heading:
        raise ValueError('年度見出しがありません')
    fy = 2018 + (1 if heading[1] == '元' else int(heading[1]))
    tables = [t for t in soup.select('table') if t.find('tr') and
              [text(c) for c in t.find('tr').find_all(['td','th'], recursive=False)] == HEADERS]
    if len(tables) != 1:
        raise ValueError('大会表の列構造を確認できません')
    events = []
    for row in tables[0].select('tr')[1:]:
        cells = row.find_all(['td','th'],recursive=False)
        if len(cells) != 3:
            raise ValueError('大会行の列数が変化しました')
        date_text, title, overview = [text(c) for c in cells]
        if not title or not overview:
            raise ValueError('大会名・概要が空です')
        if any(w in title for w in ('教室','練習会','結果')):
            raise ValueError('大会表に大会以外の行があります')
        periods, reserves = schedule_dates(date_text, fy)
        events.append({'title':title,'date_text':date_text,'periods':periods,
                       'reserve_text':date_text.split('/')[1] if reserves else None,
                       'reserve_periods':reserves,'overview':overview})
    if not events:
        raise ValueError('大会一覧が空です')
    return {'fiscal_year':fy,'events':events}
