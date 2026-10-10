"""岡崎要項の明記された参加費・申込締切だけを読む。分類は変更しない。"""
import datetime
import re
import unicodedata

LABELS = ('主催', '後援', '会場', '日程/種目', '種目', 'チーム構成', '人数',
          '日程', '雨天予備日', '試合方法', '使用球', '参加資格', '参加費',
          '表彰', '申込締切日', '申込方法', '取消し/変更', 'ドロー会議',
          'ドロー発表', 'ドロー郵送', '注意事項', '問い合せ先')


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text or ''))


def blocks(text):
    out, key = {}, None
    for line in unicodedata.normalize('NFKC', text).splitlines():
        line = line.strip()
        if not line:
            continue
        found = None
        for label in LABELS:
            pattern = r'^' + r'\s*'.join(re.escape(c) for c in label) + r'(?:\s+|[:：]|$)(.*)$'
            match = re.match(pattern, line)
            if match:
                found = (label, match.group(1).strip())
                break
        if found:
            key, tail = found
            out.setdefault(key, [])
            if tail:
                out[key].append(tail)
        elif key:
            out[key].append(line)
    return out


def parse(text, event):
    """開催日・曜日・タイトルを照合。誤紐付け・曖昧な締切は採用しない。"""
    res = {'ok': False, 'problems': [], 'fee_lines': [], 'deadlines': []}
    normalized = unicodedata.normalize('NFKC', text)
    title = next((s.strip() for s in normalized.splitlines() if s.strip()), '')
    if compact(event['title']) not in compact(title):
        res['problems'].append('要項の大会名が一覧と一致しません')
        return res
    b = blocks(normalized)
    expected = {p['normalized'] for p in event['periods'] if p.get('normalized')}
    dates = []
    for line in b.get('日程', []):
        for m in re.finditer(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*\(([月火水木金土日])\)', line):
            candidates = []
            for iso in expected:
                d = datetime.date.fromisoformat(iso)
                if (d.month, d.day) == (int(m[1]), int(m[2])) and '月火水木金土日'[d.weekday()] == m[3]:
                    candidates.append(iso)
            if len(candidates) == 1:
                dates.extend(candidates)
    if not expected or set(dates) != expected:
        res['problems'].append('要項の開催日・曜日が一覧と一致しません')
        return res
    for line in b.get('申込締切日', []):
        match = re.search(r'令和\s*(\d+)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*\(([月火水木金土日])\)', line)
        if not match:
            continue
        try:
            d = datetime.date(2018 + int(match[1]), int(match[2]), int(match[3]))
        except ValueError:
            continue
        if d.isoformat() >= min(expected) or '月火水木金土日'[d.weekday()] != match[4]:
            continue
        mode = 'インターネット' if 'インターネット' in line else ('郵送' if '郵送' in line else '共通')
        res['deadlines'].append({'mode': mode, 'date': d.isoformat(), 'text': re.sub(r'\s+', ' ', line)})
    # 複数の無印締切、同一方法の異なる締切は、誤った単一締切にしない。
    modes = [x['mode'] for x in res['deadlines']]
    if len(modes) != len(set(modes)):
        res['deadlines'] = []
        res['problems'].append('同じ申込方法に複数の締切があります')
    res['fee_lines'] = [re.sub(r'\s+', ' ', s) for s in b.get('参加費', [])]
    if not res['fee_lines'] or not any(re.search(r'\d[\d,\s]*円', s) for s in res['fee_lines']):
        res['fee_lines'] = []
    if not res['deadlines']:
        res['problems'].append('年・曜日付きの申込締切を確認できません')
    if not res['fee_lines']:
        res['problems'].append('参加費を確認できません')
    res['ok'] = not res['problems']
    return res
