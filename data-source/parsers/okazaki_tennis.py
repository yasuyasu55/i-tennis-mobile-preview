"""岡崎市テニス協会の大会一覧を読む専用パーサー（標準ライブラリのみ）。

公式「大会情報 R8」ページのカード表示から開催日・種目・参加資格・会場・
一覧記載の申込締切を読む。申込フォームへアクセスせず、PDF本文は未解析。
"""
from __future__ import annotations

import datetime
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

SOURCE_ID = "okazaki_tennis_association"
SOURCE_NAME = "岡崎市テニス協会"
PAGE_URL = "https://www.okazaki-tennis.com/taikai-r8"
ALLOWED_HOST = "www.okazaki-tennis.com"


def fiscal_year_from_url(page_url: str) -> Optional[int]:
    m = re.search(r"/taikai-r(\d+)(?:[/?#]|$)", page_url or "", re.I)
    return 2018 + int(m.group(1)) if m else None


class _Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return " ".join(x.text() if isinstance(x, _Node) else x for x in self.children)

    def descendants(self, tag=None):
        for x in self.children:
            if isinstance(x, _Node):
                if tag is None or x.tag == tag:
                    yield x
                yield from x.descendants(tag)


class _Tree(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self.ignore_depth += 1
            return
        if self.ignore_depth:
            return
        node = _Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if not self.ignore_depth:
            self.stack[-1].children.append(_Node(tag.lower(), attrs, self.stack[-1]))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style") and self.ignore_depth:
            self.ignore_depth -= 1
            return
        if self.ignore_depth:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip() and not self.ignore_depth:
            self.stack[-1].children.append(data)


def _clean(s: str) -> str:
    s = (s or "").replace("\u3000", " ").replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip()


def _label_pattern(label: str) -> str:
    # Some Wix card labels insert spaces inside the Japanese label (e.g. 資 格).
    return r"\s*".join(re.escape(c) for c in label)


def _between(text: str, label: str, next_label: str) -> Optional[str]:
    m = re.search(_label_pattern(label) + r"\s*(.*?)\s*(?=" + _label_pattern(next_label) + r")", _clean(text))
    return _clean(m.group(1)) if m else None


def parse_dates(text: str, fiscal_year: int) -> List[Dict[str, Any]]:
    """5/23・31・6/13 のような日付列。R8年度（4月始まり）を年に変換する。"""
    s = unicodedata.normalize("NFKC", text or "")
    rx = re.compile(r"(?:(\d{1,2})\s*/\s*)?(\d{1,2})\s*(?:日)?")
    out, month = [], None
    for m in rx.finditer(s):
        if m.start() and (s[m.start()-1].isalnum() or s[m.start()-1] == "/"):
            continue
        if not m.group(1):
            # Only accept a day-only token after a previously found month/date separator.
            left = s[:m.start()].rstrip()
            if month is None or not left or left[-1] not in "・,、/～〜~－-":
                continue
        else:
            month = int(m.group(1))
        if month is None:
            continue
        day = int(m.group(2))
        year = fiscal_year if month >= 4 else fiscal_year + 1
        raw = m.group(0).strip()
        warnings, normalized = [], None
        try:
            normalized = datetime.date(year, month, day).isoformat()
        except ValueError:
            warnings.append("存在しない日付のため原文のみ保持")
        out.append({"raw": raw, "normalized": normalized, "year": year,
                    "weekday_check": "not_available", "warnings": warnings})
    return out


def _find_card(heading, root):
    node = heading.parent
    while node is not None and node is not root:
        body = _clean(node.text())
        if len(body) < 12000 and re.search(r"開催\s*日", body) and re.search(r"資\s*格", body) and re.search(r"場\s*所", body):
            return node
        node = node.parent
    return None


def parse_page(raw: bytes, page_url: str = PAGE_URL, fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    fiscal_year = fiscal_year if fiscal_year is not None else fiscal_year_from_url(page_url)
    if fiscal_year is None:
        return {"fatal_error": "大会ページURLから令和年度を特定できません（年度の推測は行いません）", "events": []}
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        return {"fatal_error": "UTF-8として読み取れません: " + str(e), "events": []}
    tree = _Tree()
    try:
        tree.feed(text)
    except Exception as e:
        return {"fatal_error": "HTMLを解析できません: " + str(e), "events": []}
    events, seen = [], set()
    for h in tree.root.descendants("h4"):
        title = _clean(h.text())
        card = _find_card(h, tree.root)
        if not title or card is None or title in seen:
            continue
        body = _clean(card.text())
        date_text = _between(body, "〈開催日〉", "〈予備日〉") or _between(body, "〈開催日〉", "〈種目〉")
        event_text = _between(body, "〈種目〉", "〈資格〉")
        eligibility = _between(body, "〈資格〉", "〈場所〉")
        venue = _between(body, "〈場所〉", "〈要項〉")
        dm = re.search(r"申込締切日\s*[:：]?\s*([0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日(?:\s*[（(][^）)]*[）)])?)", body)
        deadline = _clean(dm.group(1)) if dm else None
        pdfs = []
        for a in card.descendants("a"):
            label = _clean(a.text())
            absolute = urljoin(page_url, a.attrs.get("href", ""))
            parts = urlsplit(absolute)
            if (parts.scheme == "https" and parts.hostname == ALLOWED_HOST and parts.path.lower().endswith(".pdf")
                    and "申込書" not in label and not re.search(r"ドロー|結果", label)):
                pdfs.append((label, absolute))
        guideline = next((u for label, u in pdfs if "詳細" in label), None)
        if guideline is None and len(pdfs) == 1:
            guideline = pdfs[0][1]
        periods = parse_dates(date_text or "", fiscal_year)
        if not (date_text and event_text and eligibility and venue and periods):
            continue
        events.append({"title": title, "date_text": date_text, "periods": periods, "events_text": event_text,
                       "eligibility": eligibility, "venue": venue, "deadline_text": deadline,
                       "guideline_url": guideline})
        seen.add(title)
    return {"fatal_error": None if events else "大会カードを抽出できません（ページ構造が変わった可能性）",
            "events": events, "fiscal_year": fiscal_year, "page_url": page_url}

