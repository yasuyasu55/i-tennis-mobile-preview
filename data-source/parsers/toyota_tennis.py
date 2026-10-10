"""Toyota's numbered annual tournament table; lessons/results are not entries."""
import datetime
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

PAGE_URL = "https://www.toyota-ta.jp/tournament/"
SOURCE_ID = "toyota_tennis_association"
SOURCE_NAME = "豊田市テニス協会"
HEADERS = ["大会名", "開催日", "開催要項", "申込期間", "申込", "実施要項", "試合結果"]


def text(node):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", node.get_text(" ", strip=True))).strip()


def dates(raw, fiscal_year):
    """January-March belongs to the following year; comma days inherit month."""
    value = unicodedata.normalize("NFKC", raw)
    explicit = re.search(r"(\d{4})\s*年", value)
    value = re.sub(r"\d{4}\s*年", "", value).strip()
    periods, month = [], None
    for token in re.split(r"[\s,、]+", value):
        if not token:
            continue
        m = re.fullmatch(r"(?:(\d{1,2})/)?(\d{1,2})", token)
        if not m:
            raise ValueError("開催日を解釈できません: " + raw)
        if m.group(1):
            month = int(m.group(1))
        if month is None:
            raise ValueError("開催月がありません")
        year = int(explicit.group(1)) if explicit else fiscal_year + (month < 4)
        day = datetime.date(year, month, int(m.group(2)))
        if year - (month < 4) != fiscal_year:
            raise ValueError("開催年と年度が一致しません")
        periods.append({"raw": token, "normalized": day.isoformat(), "year": year,
                        "weekday_check": "not_available", "warnings": []})
    if not periods:
        raise ValueError("開催日がありません")
    return periods


def link(cell, kind):
    for a in cell.select("a[href]"):
        label = text(a)
        url = urljoin(PAGE_URL, a["href"])
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            continue
        if kind == "guideline" and parts.hostname in ("www.toyota-ta.jp", "toyota-ta.jp") and parts.path.lower().endswith(".pdf"):
            return url
        if kind == "entry" and label == "申込":
            return url
    return None


def parse_page(raw):
    soup = BeautifulSoup(raw, "html.parser")
    for el in soup.select("script,style"):
        el.decompose()
    tables = [t for t in soup.select("table") if t.find("tr") and
              [text(c) for c in t.find("tr").find_all(["td", "th"], recursive=False)][1:] == HEADERS]
    if len(tables) != 1:
        raise ValueError("大会一覧の列構造を確認できません")
    table = tables[0]
    fy = None
    for el in table.previous_elements:
        if getattr(el, "name", None) in ("p", "h2", "h3", "h4"):
            m = re.fullmatch(r"(\d{4})年度\s*大会", text(el))
            if m:
                fy = int(m.group(1))
                break
    if fy is None:
        raise ValueError("大会表の年度見出しがありません")
    events, seen = [], set()
    for row in table.select("tr")[1:]:
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) != 8:
            if len(cells) == 1 and "練習会" in text(cells[0]):
                continue
            raise ValueError("大会行の列数が変化しました")
        number = text(cells[0])
        if not number.isdigit() or number in seen:
            raise ValueError("大会行番号が不正です")
        seen.add(number)
        title, date_text, entry_period = text(cells[1]), text(cells[2]), text(cells[4])
        if not title or "練習会" in title:
            raise ValueError("大会名が不正です")
        periods = dates(date_text, fy)
        deadline = None
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})\s*[-~〜]\s*(\d{1,2})/(\d{1,2})", entry_period)
        if m:
            first = min(p["normalized"] for p in periods)
            event = datetime.date.fromisoformat(first)
            end = datetime.date(event.year, int(m.group(3)), int(m.group(4)))
            if end >= event:
                end = end.replace(year=end.year - 1)
            start = datetime.date(end.year, int(m.group(1)), int(m.group(2)))
            if start > end:
                start = start.replace(year=start.year - 1)
            if not 0 <= (event-end).days <= 180 or (end-start).days > 90:
                raise ValueError("申込期間と開催日の関係を確認できません")
            deadline = end.isoformat()
        elif entry_period:
            raise ValueError("申込期間の形式が変化しました")
        events.append({"title": title, "date_text": date_text, "periods": periods,
                       "entry_period": entry_period or None, "deadline_date": deadline,
                       "guideline_url": link(cells[3], "guideline"), "entry_url": link(cells[5], "entry")})
    if not events:
        raise ValueError("大会一覧が空です")
    return {"fiscal_year": fy, "events": events}
