"""安城・豊田・刈谷の要項。大会・年度を照合し、日時の矛盾は補正しない。"""
import datetime
import re
import unicodedata


def compact(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s))


LABELS = ('参加資格','参加料','参加費','申込締切','申込期限','申込期間','会場','種目','日程','期日','日時','部門/期日')


def sections(text):
    result = {}; key = None
    for line in unicodedata.normalize('NFKC', text).splitlines():
        line = line.strip()
        # Only actual section headings; prose mentioning a fee is not a heading.
        heading = re.match(r'^(?:■\s*|\d+\s*[.、]?\s+)(.*)$', line)
        if heading:
            key = None
            for label in LABELS:
                m = re.match(r'^' + r'\s*'.join(map(re.escape,label)) + r'(?:\s+|[:：]|$)(.*)$', heading[1])
                if m:
                    key = label
                    result.setdefault(key,[]).append([])
                    if m[1].strip():result[key][-1].append(m[1].strip().lstrip(':：').strip())
                    break
        elif key and line and '開催要項' not in compact(line) and not re.search(r'\d{4}年度',line):
            result[key][-1].append(line)
    return result


def title_matches(text, event, key):
    head = compact('\n'.join(text.splitlines()[:7]))
    title = compact(event['title']).replace('ウィークデイ','ウィークデー')
    head = head.replace('ウィークデイ','ウィークデー')
    if key == 'anjo':
        title = title.replace('大会(一般・チャレンジャー)','大会(一般)').replace('選手権大会','選手権')
        title = title.replace('C・D級','【C・D級】').replace('シングルス大会','シングルス')
        title = title.replace('秋季女子ウィークデーダブルス','秋季ウィークデー女子ダブルス')
    if title not in head:return False
    first = datetime.date.fromisoformat(min(p['normalized'] for p in event['periods']))
    fy = first.year - (first.month < 4)
    if key == 'kariya':return ('令和%d年'%(fy-2018)) in head
    return ('%d年度'%fy) in head


def parse(text,event,key):
    text=unicodedata.normalize('NFKC',text)
    out={'matched':False,'fields':{},'warnings':[]}
    if not title_matches(text,event,key):
        out['warnings'].append('要項の大会名・年度を一覧と照合できません');return out
    b=sections(text)
    expected=[datetime.date.fromisoformat(p['normalized']) for p in event['periods']]
    schedule=' '.join(' '.join(v) for k in ('日程','期日','日時','部門/期日') for v in b.get(k,[]))
    dates=list(re.finditer(r'(?:((?:20\d{2})|(?:令和\d+))年)?\s*(\d{1,2})月\s*(\d{1,2})日',compact(schedule)))
    if not all(any(int(m[2])==d.month and int(m[3])==d.day for m in dates) for d in expected):
        # Toyota's table may write 11月8日・15日(日); verify inherited month too.
        s=compact(schedule)
        if key!='toyota' or not all((f'{d.month}月{d.day}日' in s or re.search(fr'{d.month}月\d+日・{d.day}日',s)) for d in expected):
            out['warnings'].append('要項の開催月日を一覧と照合できません');return out
    for m in dates:
        if not m[1]:continue
        yr=int(m[1][2:])+2018 if m[1].startswith('令和') else int(m[1])
        if any((d.month,d.day)==(int(m[2]),int(m[3])) and d.year!=yr for d in expected):
            out['warnings'].append('要項本文の開催年が公式一覧と不一致。開催日は一覧を採用し、要項の訂正を要確認')
    out['matched']=True
    def values(*keys):
        return list(dict.fromkeys(re.sub(r'\s+',' ',s).strip() for k in keys for v in b.get(k,[]) for s in v if s.strip()))
    venue=values('会場')
    venue=[s for s in venue if 'コート' in s and '全会場' not in s[:4]]
    if venue:out['fields']['venue']=' / '.join(venue)
    fees=values('参加料','参加費')
    fees=[s for s in fees if re.search(r'\d[\d,]*円|無料|半額',compact(s))]
    if fees:out['fields']['fee_text']=' / '.join(fees)
    elig=values('参加資格')
    kinds=values('種目')
    # Class/age restrictions are often in the kind block instead of eligibility.
    restricted=[s for s in kinds if re.search(r'出場できません|出場出来ません|出場をお断り|対象|歳|オープン|ベスト|試合経験|初心',s)]
    if elig:out['fields']['eligibility_text']=' / '.join(elig+restricted)
    if key=='toyota':
        age_lines=[s for v in b.get('部門/期日',[]) for s in v if '歳以上' in s]
        if age_lines and elig:out['fields']['eligibility_text']+=' / '+' / '.join(age_lines)
        out['fields']['eligibility_note']='会場の詳細・部門ごとの開催日は実施要項を確認してください'
    deadlines=values('申込締切','申込期限','申込期間')
    if deadlines:
        # First line per section; later cancellation/transfer prose is unrelated.
        ds=list(dict.fromkeys(v[0] for k in ('申込締切','申込期限','申込期間') for v in b.get(k,[]) if v))
        out['fields']['deadline_text']=' / '.join(ds)
        candidates=[]
        for line in ds:
            cs=compact(line)
            matches=list(re.finditer(r'(\d{1,2})月(\d{1,2})日\(([月火水木金土日])\)',cs))
            # A range ends at its final date, a deadline with opening date uses first.
            m=matches[-1] if ('~' in cs or '〜' in cs or 'から' in cs) and matches else (matches[0] if matches else None)
            if not m:continue
            try:day=datetime.date(expected[0].year,int(m[1]),int(m[2]))
            except ValueError:
                out['warnings'].append('要項の申込締切の日付が不正です。公式へ要確認');continue
            if day>=min(expected):day=day.replace(year=day.year-1)
            if not 0<(min(expected)-day).days<=180 or '月火水木金土日'[day.weekday()]!=m[3]:
                out['warnings'].append('要項の申込締切は日付・曜日の整合を確認できません。締切は公式へ要確認');continue
            candidates.append(day.isoformat())
        if len(set(candidates))==1 and len(candidates)==len(ds):out['fields']['deadline_date']=candidates[0]
        else:out['fields']['deadline_date']=None
    return out
