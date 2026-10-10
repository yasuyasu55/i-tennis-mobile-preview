"""市民スポーツ祭の列配置を保持した要項から、補足情報だけを読む。"""
import datetime
import re
import unicodedata


def parse(text, event):
    text = unicodedata.normalize('NFKC', text or '')
    compact = lambda s: re.sub(r'\s+', '', s)
    title = compact(event.get('title') or '')
    heading = compact(text.splitlines()[0] if text.splitlines() else '')
    pattern = r'第(\d+)回浜松市(?:民)?スポーツ[祭際]'
    a, b = re.search(pattern, title), re.search(pattern, heading)
    if not a or not b or a[1] != b[1] or 'テニス競技' not in heading:
        return None
    lines = text.splitlines()
    # 同じ用紙に複数大会が載るので、単一の締切にはしない。
    groups = ('団体', '一般ダブルス', '混合ダブルス', '小中学生ダブルス', '高校の部')
    marks = []
    for i, line in enumerate(lines):
        first = re.split(r'\s{2,}', line.strip())[0]
        for group in groups:
            if compact(first) == group:
                marks.append((i, group))
    deadlines = {}
    for i, line in enumerate(lines):
        m = re.search(r'(\d{1,2})月(\d{1,2})日\s*\(([月火水木金土日])\)\s{2,}(\d{1,2})月(\d{1,2})日\s*\(([月火水木金土日])\)', line)
        if not m or not marks:
            continue
        near = sorted((abs(i - row), group) for row, group in marks)
        if near[0][0] > 5 or (len(near) > 1 and near[0][0] == near[1][0]):
            continue
        year = event.get('folder_year')
        if not isinstance(year, int):
            return None
        try:
            day = datetime.date(year, int(m[1]), int(m[2]))
            deadline = datetime.date(year, int(m[4]), int(m[5]))
        except ValueError:
            continue
        if '月火水木金土日'[day.weekday()] != m[3] or '月火水木金土日'[deadline.weekday()] != m[6] or deadline >= day:
            continue
        group = near[0][1]
        if group in deadlines:
            return None  # 曖昧な表は採用しない。
        deadlines[group] = {'date': day.isoformat(), 'deadline': deadline.isoformat()}
    wanted = ['団体'] if '団体' in title else [g for g in ('一般ダブルス', '混合ダブルス') if g in title]
    if not wanted or any(g not in deadlines for g in wanted):
        return None
    # 少なくとも一つは一覧の開催日と一致。原文の年の誤記は修正しない。
    expected = {p['normalized'] for p in event['periods'] if p.get('normalized')}
    if not any(deadlines[g]['date'] in expected for g in wanted):
        return None
    block, key = {}, None
    labels = ('主催', '共催', '主管', '会場', '試合方法', '使用球', '参加資格', '参加料', '申込締切', '団体戦について', '駐車場制限', '申込先', '注意事項')
    for line in lines:
        found = None
        for label in labels:
            rx = r'^\s*' + r'\s*'.join(re.escape(c) for c in label) + r'\s{2,}(.*)$'
            m = re.match(rx, line)
            if m:
                found = (label, m[1].strip())
                break
        if found:
            key, tail = found
            block[key] = [tail] if tail else []
        elif key and line.strip():
            block[key].append(line.strip())
    fees = [re.sub(r'\s+', ' ', line) for line in block.get('参加料', [])
            if (('団体' in compact(line)) if wanted == ['団体'] else ('一般・混合ダブルス' in compact(line)))
            and re.search(r'\d[\d,]*円', compact(line))]
    addresses = block.get('申込先', [])
    if not fees or not addresses or '18:00' not in compact(' '.join(block.get('申込締切', []))):
        return None
    return {'fee_text': ' ／ '.join(fees),
            'entry_text': '申込先: ' + ' ／ '.join(re.sub(r'\s+', ' ', s) for s in addresses),
            'deadline_notes': ['要項PDFの%s申込締切: %s 18:00必着（複数種目の締切は別々に表示）' % (g, deadlines[g]['deadline']) for g in wanted]}
