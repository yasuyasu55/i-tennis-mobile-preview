"""Nagoya association: explicit dated plans and inline guidelines, never galleries."""
import datetime
import re
import unicodedata
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup

PAGE_URL = 'https://peraichi.com/landing_pages/view/nagoyatennis/'
SOURCE_ID = 'nagoya_tennis_association'
SOURCE_NAME = '名古屋テニス協会'
DATE = r'(?:令和\s*(\d+|元)年|(\d{4})年)\s*(\d{1,2})\s*[/月]\s*(\d{1,2})(?:日)?\s*(?:\(([月火水木金土日])\))?'
KINDS = r'(ミックス|団体戦|ダブルス|シングルス)'


def text(node):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', node.get_text(' ', strip=True))).strip()


def period(match):
    year = 2018 + (1 if match[1] == '元' else int(match[1])) if match[1] else int(match[2])
    day = datetime.date(year, int(match[3]), int(match[4]))
    check = 'not_available' if not match[5] else ('ok' if '月火水木金土日'[day.weekday()] == match[5] else 'mismatch')
    return {'raw': re.match(DATE, match[0])[0], 'normalized': day.isoformat(), 'year': year, 'weekday_check': check,
            'warnings': ['公式の曜日と開催日が一致しません。公式案内で確認してください'] if check == 'mismatch' else []}


def fiscal_year(p):
    return p['year'] - (int(p['normalized'][5:7]) < 4)


def event(p, kind, section):
    sid = section.get('id')
    url = PAGE_URL + ('#' + sid if sid and re.fullmatch(r'[\w-]+', sid) else '')
    return {'title': '名古屋テニス協会 %s（%d月）' % (kind, int(p['normalized'][5:7])),
            'kind': kind, 'date_text': p['raw'], 'periods': [p], 'fiscal_year': fiscal_year(p),
            'official_url': url, 'venue': None, 'fee_text': None, 'eligibility': None,
            'rules': None, 'entry_text': None, 'entry_url': None, 'guideline_url': None}


def parse_page(raw):
    soup = BeautifulSoup(raw, 'html.parser')
    # Peraichi retains invisible editor placeholders, including obsolete eligibility.
    for node in list(soup.select('.pera1-ghost, .pera1-add, [hidden], script, style')):
        node.decompose()
    for node in list(soup.select('[style]')):
        if node.attrs and re.search(r'(?:display\s*:\s*none|opacity\s*:\s*0(?:[;\s]|$))', node.get('style', '')):
            node.decompose()
    headings = [h for h in soup.select('h2,h3,h4') if text(h).startswith('試合開催予定')]
    if len(headings) != 1:
        raise ValueError('開催予定の見出しが一意ではありません')
    heading = headings[0]
    value = text(heading)
    fy_match = re.search(r'(\d{4})年度開催予定日', value)
    if not fy_match:
        raise ValueError('開催予定の年度がありません')
    fy = int(fy_match[1])
    section = heading.find_parent(class_='pera1-section') or heading
    events = []
    for m in re.finditer(DATE + r'\s*' + KINDS, value):
        p = period(m)
        if fiscal_year(p) != fy:
            raise ValueError('開催予定の明記された年と年度が一致しません')
        events.append(event(p, m[6], section))
    # Reject a newly listed, unparsed date rather than silently dropping it.
    residue = re.sub(DATE + r'\s*' + KINDS, '', value)
    if not events or re.search(r'\d{1,2}\s*/\s*\d{1,2}|\d{4}年(?!度)', residue):
        raise ValueError('開催予定の日時または種目を読み取れません')
    common = []
    for li in soup.select('li'):
        v = text(li)
        if v.startswith('名古屋テニス協会主催の大会は') and '参加できます' in v:
            common.append(v)
    if len(set(common)) != 1:
        raise ValueError('協会主催大会の共通参加条件を確認できません')
    details = 0
    for table in soup.select('table'):
        rows = table.select('tr')
        if not any(re.sub(r'\s+', '', text(c)) == '開催日時' for row in rows for c in row.find_all(['td', 'th'], recursive=False)[:1]):
            continue
        fields = {}
        for row in rows:
            cells = row.find_all(['th', 'td'], recursive=False)
            if len(cells) != 2:
                continue
            label = re.sub(r'\s+', '', text(cells[0]))
            if label in fields:
                raise ValueError('大会案内の項目が重複しています')
            fields[label] = text(cells[1])
        if '開催日時' not in fields:
            continue
        section = table.find_parent(class_='pera1-section')
        h = section.find(['h2', 'h3', 'h4']) if section else None
        kind = re.search(KINDS + r'\s*ご案内', text(h)) if h else None
        if not kind or not all(fields.get(k) for k in ('参加資格', '参加費', '場所', '参加申し込み')):
            raise ValueError('大会案内の項目・見出しが変化しました')
        m = re.match(DATE, fields['開催日時'])
        if not m:
            raise ValueError('大会案内の開催年が明記されていません')
        p = period(m)
        # The heading's day must identify the same event as the guideline table.
        hd = re.search(r'(\d{1,2})/(\d{1,2})', text(h))
        if not hd or (int(hd[1]), int(hd[2])) != (int(m[3]), int(m[4])):
            raise ValueError('大会案内の見出しと開催日が一致しません')
        ev = event(p, kind[1], section)
        ev.update(venue=fields['場所'], fee_text=fields['参加費'], eligibility=fields['参加資格'],
                  entry_text=fields['参加申し込み'], guideline_url=ev['official_url'],
                  rules=' '.join(v for k, v in fields.items() if k.startswith('ル')) or None)
        # Separate application buttons are associated by their explicit day and kind.
        links = []
        for a in soup.select('a[href]'):
            v = text(a)
            ad = re.search(r'(\d{1,2})/(\d{1,2})', v)
            u = urljoin(PAGE_URL, a['href'])
            parsed = urlsplit(u)
            if ad and (int(ad[1]), int(ad[2])) == (int(m[3]), int(m[4])) and kind[1] in v and '申し込みフォーム' in v and parsed.scheme == 'https' and parsed.hostname == 'form1ssl.fc2.com':
                links.append(u)
        if len(set(links)) > 1:
            raise ValueError('大会の申込フォームが複数あります')
        ev['entry_url'] = links[0] if links else None
        matches = [i for i, e in enumerate(events) if e['periods'][0]['normalized'] == p['normalized'] and e['kind'] == ev['kind']]
        if matches:
            events[matches[0]] = ev
        else:
            events.append(ev)
        details += 1
    keys = [(e['fiscal_year'], e['title']) for e in events]
    if len(keys) != len(set(keys)):
        raise ValueError('同月・同種目の大会を一意に識別できません')
    return {'fiscal_year': fy, 'events': events, 'common_eligibility': common[0], 'detail_count': details,
            'adult_association': '社会人のアマチュアの男女' in text(soup)}
