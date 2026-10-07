"""
TTA-MOBILE-012 蒲郡テニス協会 専用パーサー（保存HTML専用・独立モジュール）

対象:
  - 大会一覧（https://gamagori-tennis.com/tournament/ の保存HTML）
      #contents .innerRight 内の h2「令和N年度 …」と、その直後の1つの<table>（1行=1大会、5列）
  - エントリーページ（https://gamagori-tennis.com/entry/ の保存HTML）
      #contents .innerRight 内の h2（大会名）と dl.dlCol（大会要項・大会エントリー先）

方針（TTA-MOBILE-008 の調査結果に基づく）:
  - 公式本文領域（#contents .innerRight）だけを読む。ヘッダー・フッター・HTMLコメント・拡張機能の注入要素は読まない。
  - 年は、見出しの「令和N年度」から導出する（4〜12月=その年度の西暦、1〜3月=翌年）。曜日は検算にだけ使い、
    日付・年を曜日から決めない。根拠は year_basis に記録する。
  - PDF・結果ページ・外部サイトへは通信しない（リンクのURLと表示名を保持するだけ）。
  - 表にない情報は推測せず None のままにする。

既存の app_auto_test.py・PC版・モバイル版には接続しない。
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag

from .common import absolutize_url, detect_charset, strict_decode, now_iso

SOURCE_ID = "gamagori_tennis_association"
SOURCE_NAME = "蒲郡テニス協会"
TOURNAMENT_PAGE_URL = "https://gamagori-tennis.com/tournament/"
ENTRY_PAGE_URL = "https://gamagori-tennis.com/entry/"

WEEKDAY_KANJI = "月火水木金土日"  # date.weekday(): 月=0 ... 日=6
DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s*[（(]\s*([月火水木金土日])\s*[）)]\s*$")
DEADLINE_RE = re.compile(r"(\d{1,2})月\s*(\d{1,2})日\s*[（(]\s*([月火水木金土日])\s*[）)]\s*(\d{1,2}):(\d{2})")
REIWA_RE = re.compile(r"令和\s*(\d+|元)\s*年度")


@dataclass
class DatePoint:
    raw: str
    normalized: Optional[str] = None      # "YYYY-MM-DD"
    year: Optional[int] = None
    weekday_check: str = "not_available"  # ok / mismatch / not_available
    warnings: List[str] = field(default_factory=list)


@dataclass
class GamagoriRecord:
    row_index: int
    title: str
    date: DatePoint
    reserve: DatePoint
    eligibility_text: Optional[str]
    event_lines: List[str]
    venue_text: Optional[str]
    guideline_url: Optional[str]
    guideline_label: Optional[str]
    draw_links: List[Dict[str, Any]]
    result_url: Optional[str]
    entry_place_text: Optional[str]
    deadline_text: Optional[str]
    deadline_date: Optional[str]
    deadline_time: Optional[str]
    deadline_weekday_check: str
    entry_url: Optional[str]
    year_basis: str
    parse_status: str
    warnings: List[str]


@dataclass
class GamagoriResult:
    fiscal_year: Optional[int]
    heading: Optional[str]
    page_url: str
    records: List[GamagoriRecord]
    charset: Dict[str, Any]
    overall_status: str
    fatal_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _decode(raw: bytes):
    cs = detect_charset(raw)
    if not cs.confident or not cs.charset:
        return None, cs, "文字コードを確信を持って判定できませんでした（" + cs.detail + "）"
    try:
        return strict_decode(raw, cs.charset), cs, None
    except UnicodeDecodeError as e:
        return None, cs, "厳密デコードに失敗しました: " + str(e)


def reiwa_to_fiscal_year(heading: str) -> Optional[int]:
    m = REIWA_RE.search(heading or "")
    if not m:
        return None
    n = 1 if m.group(1) == "元" else int(m.group(1))
    return 2018 + n  # 令和元年=2019


def resolve_year(month: int, fiscal_year: int) -> int:
    return fiscal_year if 4 <= month <= 12 else fiscal_year + 1


def _weekday_check(d: datetime.date, kanji: str) -> str:
    return "ok" if WEEKDAY_KANJI[d.weekday()] == kanji else "mismatch"


def parse_date_point(text: str, fiscal_year: Optional[int]) -> DatePoint:
    raw = (text or "").strip()
    dp = DatePoint(raw=raw)
    m = DATE_RE.match(raw)
    if not m:
        dp.warnings.append("日付形式を解釈できないため原文のみ保持します")
        return dp
    if fiscal_year is None:
        dp.warnings.append("年度見出しから年を導出できないため正規化していません")
        return dp
    month, day, kanji = int(m.group(1)), int(m.group(2)), m.group(3)
    year = resolve_year(month, fiscal_year)
    try:
        d = datetime.date(year, month, day)
    except ValueError:
        dp.warnings.append("存在しない日付のため正規化していません")
        return dp
    dp.normalized = d.isoformat()
    dp.year = year
    dp.weekday_check = _weekday_check(d, kanji)
    if dp.weekday_check == "mismatch":
        dp.warnings.append("原文の曜日と日付が一致しません（日付の数字は原文どおり保持）")
    return dp


def _parts_by_hr(td: Tag) -> List[BeautifulSoup]:
    html = td.decode_contents()
    chunks = re.split(r"<hr\s*/?>", html, flags=re.IGNORECASE)
    return [BeautifulSoup(c, "lxml") for c in chunks]


def _lines(soup: BeautifulSoup) -> List[str]:
    text = re.sub(r"<br\s*/?>", "\n", str(soup.body or soup), flags=re.IGNORECASE)
    plain = BeautifulSoup(text, "lxml").get_text()
    return [ln.strip() for ln in plain.split("\n") if ln.strip()]


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_tournament_page(raw: bytes, page_url: str = TOURNAMENT_PAGE_URL,
                          entry_pages: Optional[Dict[str, Dict[str, Any]]] = None) -> GamagoriResult:
    text, cs, error = _decode(raw)
    cs_dict = asdict(cs)
    if error:
        return GamagoriResult(None, None, page_url, [], cs_dict, "failed", error)
    soup = BeautifulSoup(text, "lxml")
    area = soup.select_one("#contents .innerRight")
    if area is None:
        return GamagoriResult(None, None, page_url, [], cs_dict, "failed", "#contents .innerRight が見つかりません")
    h2 = area.find("h2", recursive=False)
    heading = _clean(h2.get_text()) if h2 else None
    fiscal_year = reiwa_to_fiscal_year(heading or "")
    table = area.find("table", recursive=False)
    if table is None:
        return GamagoriResult(fiscal_year, heading, page_url, [], cs_dict, "failed", "大会一覧の表が見つかりません")

    entry_pages = entry_pages or {}
    year_basis_txt = (
        "「%s」見出しから導出（4〜12月=%d年、1〜3月=%d年）。曜日で検算" % (heading, fiscal_year, fiscal_year + 1)
        if fiscal_year else "年度見出しから年を導出できなかった"
    )
    records: List[GamagoriRecord] = []
    for idx, tr in enumerate(table.find_all("tr")[1:], 1):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 5:
            continue
        warnings: List[str] = []
        title = _clean(tds[2].get_text("", strip=True))
        if not title:
            continue

        date = parse_date_point(tds[0].get_text("", strip=True), fiscal_year)
        reserve = parse_date_point(tds[1].get_text("", strip=True), fiscal_year)
        warnings.extend(date.warnings)

        parts = _parts_by_hr(tds[3])
        eligibility_text = event_lines = venue_text = None
        event_lines_list: List[str] = []
        if len(parts) == 3:
            eligibility_text = _clean(parts[0].get_text(""))
            event_lines_list = _lines(parts[1])
            venue_text = _clean(parts[2].get_text(""))
        else:
            warnings.append("参加資格・種目・会場の欄が3区分に分かれていないため取得しません")

        guideline_url = guideline_label = result_url = None
        draw_links: List[Dict[str, Any]] = []
        entry_place_text = deadline_text = None
        for li in tds[4].find_all("li"):
            cls = li.get("class") or []
            li_text = _clean(li.get_text(" ", strip=True)) or ""
            if "deadline" in cls:
                deadline_text = _clean(re.sub(r"^エントリー締切[：:]\s*", "", li_text))
                continue
            if li_text.startswith("大会エントリー先"):
                entry_place_text = _clean(re.sub(r"^大会エントリー先[：:]\s*", "", li_text))
                continue
            for a in li.find_all("a"):
                label = _clean(a.get_text(" ", strip=True)) or ""
                url = absolutize_url(page_url, a.get("href"))
                if url is None:
                    continue
                if "開催要項" in label and guideline_url is None:
                    guideline_url, guideline_label = url, label
                elif label.startswith("ドロー"):
                    draw_links.append({"label": label, "url": url})
                elif "大会結果" in label:
                    result_url = url
        if guideline_url is None:
            warnings.append("開催要項は未公開（一覧表に「―」）")

        # 申込締切（表に明記された場合のみ。年は年度見出しから導出し、曜日で検算）
        deadline_date = deadline_time = None
        deadline_check = "not_available"
        if deadline_text:
            dm = DEADLINE_RE.search(deadline_text)
            if dm and fiscal_year:
                month, day, kanji, hh, mm = int(dm.group(1)), int(dm.group(2)), dm.group(3), dm.group(4), dm.group(5)
                try:
                    d = datetime.date(resolve_year(month, fiscal_year), month, day)
                    deadline_date, deadline_time = d.isoformat(), "%02d:%s" % (int(hh), mm)
                    deadline_check = _weekday_check(d, kanji)
                    if deadline_check == "mismatch":
                        warnings.append("申込締切の曜日が日付と一致しません（日付の数字は原文どおり保持）")
                except ValueError:
                    warnings.append("申込締切の日付を正規化できません")
            else:
                warnings.append("申込締切の形式を解釈できないため原文のみ保持します")

        # エントリーページ（保存HTML）に同じ大会名の記載がある場合だけ、エントリーページのURLを対応づける（大会名の完全一致）
        entry_url = None
        if title in entry_pages:
            entry_url = entry_pages[title]["page_url"]

        status = "success" if (date.normalized and venue_text and guideline_url) else "partial"
        records.append(GamagoriRecord(
            row_index=idx, title=title, date=date, reserve=reserve,
            eligibility_text=eligibility_text, event_lines=event_lines_list, venue_text=venue_text,
            guideline_url=guideline_url, guideline_label=guideline_label, draw_links=draw_links, result_url=result_url,
            entry_place_text=entry_place_text, deadline_text=deadline_text, deadline_date=deadline_date,
            deadline_time=deadline_time, deadline_weekday_check=deadline_check, entry_url=entry_url,
            year_basis=year_basis_txt, parse_status=status, warnings=warnings,
        ))

    overall = "success" if records and all(r.parse_status == "success" for r in records) else ("partial" if records else "failed")
    return GamagoriResult(fiscal_year, heading, page_url, records, cs_dict, overall)


def parse_entry_page(raw: bytes, page_url: str = ENTRY_PAGE_URL) -> Dict[str, Dict[str, Any]]:
    """
    エントリーページの保存HTMLから、受付中の大会（見出しh2）ごとの要項URL・エントリー先を取り出す。
    戻り値のキーは大会名（先頭の「令和N年度 」を除いたもの）。
    """
    text, _cs, error = _decode(raw)
    if error:
        return {}
    soup = BeautifulSoup(text, "lxml")
    area = soup.select_one("#contents .innerRight")
    out: Dict[str, Dict[str, Any]] = {}
    if area is None:
        return out
    for h2 in area.find_all("h2", recursive=False):
        name = _clean(h2.get_text()) or ""
        key = re.sub(r"^令和\s*(\d+|元)\s*年度\s*", "", name)
        dl = h2.find_next_sibling("dl")
        info: Dict[str, Any] = {"page_url": page_url, "heading": name, "guideline_url": None, "entry_place_text": None}
        if dl is not None:
            for dt in dl.find_all("dt", recursive=False):
                dd = dt.find_next_sibling("dd")
                if dd is None:
                    continue
                label = _clean(dt.get_text()) or ""
                if label == "大会要項":
                    a = dd.find("a")
                    if a is not None:
                        info["guideline_url"] = absolutize_url(page_url, a.get("href"))
                elif label == "大会エントリー先":
                    lines = _lines(BeautifulSoup(dd.decode_contents(), "lxml"))
                    info["entry_place_text"] = _clean(lines[0].strip("【】 ")) if lines else None
        out[key] = info
    return out
