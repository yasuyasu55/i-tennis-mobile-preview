"""
TTA-MOBILE-013-A1 豊川テニス協会 専用パーサー（保存HTML＋要項PDFのテキスト専用・独立モジュール）

対象
  - 保存HTML（https://toyokawatennis.wixsite.com/index を榎本さんがブラウザで保存したもの）
      Wix製。大会1件 = 1つの <h2 class="font_3 wixui-rich-text__text">（見出し）。見出しの文字は多数の <span> に分割されているため、
      空白なしで連結して読む。見出しの中の <a> が《要項》《ドロー》《リザルト》等のリンク。
      読むのは「第N回」を含む <h2> だけ（会員募集・協会事務局の見出し、メニュー、拡張機能の注入要素は読まない）。
  - 要項PDFのテキスト層（pdftotext -layout の出力）。画像だけのPDFは対象外（その場合は確定情報にしない）。

方針
  - 開催日は見出しの「2026/11/8(日)」の形（西暦年が明記）。予備日は「予備日11/15(日)」で年が無いため、同じ見出しの開催日の年を使い、
    曜日で検算する（曜日から年は決めない）。根拠は year_basis に記録する。
  - 見出しの区分（［オープン］［豊川市民］［協会員限定］）は、サイトの表示としてそのまま保持する。［オープン］だけでは参加資格を確定しない。
  - 要項PDFの内容は、PDFの文字に明記された項目だけを取り出す。無い項目は None。
  - 通信しない（リンクのURLと表示名を保持するだけ）。既存の PC版・モバイル版には接続しない。
"""
from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .common import absolutize_url, detect_charset, strict_decode

SOURCE_ID = "toyokawa_tennis_association"
SOURCE_NAME = "豊川テニス協会"
OFFICIAL_URL = "https://toyokawatennis.wixsite.com/index"
WEEKDAY_KANJI = "月火水木金土日"

_EVENT_H2 = re.compile(r"第\s*[0-9０-９]+\s*回")
_TAG = re.compile(r"^［([^］]+)］")
_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\(([月火水木金土日])\)")
_RESERVE = re.compile(r"予備日\s*(\d{1,2})/(\d{1,2})\(([月火水木金土日])\)")


def _weekday_ok(d: datetime.date, kanji: str) -> str:
    return "ok" if WEEKDAY_KANJI[d.weekday()] == kanji else "mismatch"


def parse_top_page(raw: bytes, page_url: str = OFFICIAL_URL) -> Dict[str, Any]:
    cs = detect_charset(raw)
    if not cs.confident or not cs.charset:
        return {"fatal_error": "文字コードを確定できません（" + cs.detail + "）", "events": [], "charset": cs.__dict__}
    text = strict_decode(raw, cs.charset)
    soup = BeautifulSoup(text, "lxml")
    events: List[Dict[str, Any]] = []
    for h in soup.find_all("h2"):
        full = re.sub(r"\s+", " ", h.get_text("")).strip()
        if not _EVENT_H2.search(full):
            continue
        links = []
        for a in h.find_all("a"):
            label = re.sub(r"\s+", "", a.get_text(""))
            url = absolutize_url(page_url, a.get("href"))
            if url:
                links.append({"label": label, "url": url})
        # タグ・大会名・日付・注記に分解（見出しの文字列だけを根拠にする）
        rest = full
        tag = None
        m = _TAG.match(rest)
        if m:
            tag, rest = m.group(1), rest[m.end():].strip()
        dm = _DATE.search(rest)
        title_part = rest[:dm.start()].strip() if dm else rest
        cancelled = "大会中止" in full   # 見出しのどこに書かれていても（大会名の後ろ／末尾の《大会中止》）検出する
        title = re.sub(r"\s*大会中止\s*", " ", title_part).strip()
        main_dates, warn = [], []
        date_zone = rest[dm.start():] if dm else ""
        rm = _RESERVE.search(date_zone)
        main_zone = date_zone[:rm.start()] if rm else date_zone
        for g in _DATE.finditer(main_zone):
            y, mo, d, k = int(g.group(1)), int(g.group(2)), int(g.group(3)), g.group(4)
            raw_txt = g.group(0)
            try:
                dd = datetime.date(y, mo, d)
                main_dates.append({"raw": raw_txt, "normalized": dd.isoformat(), "year": y, "weekday_check": _weekday_ok(dd, k), "warnings": [] if _weekday_ok(dd, k) == "ok" else ["原文の曜日と日付が一致しません（日付の数字は原文どおり保持）"]})
            except ValueError:
                main_dates.append({"raw": raw_txt, "normalized": None, "year": None, "weekday_check": "not_available", "warnings": ["存在しない日付のため正規化していません"]})
        reserve = None
        if rm and main_dates and main_dates[0]["normalized"]:
            base = datetime.date.fromisoformat(main_dates[-1]["normalized"])
            mo, d, k = int(rm.group(1)), int(rm.group(2)), rm.group(3)
            y = base.year
            try:
                rd = datetime.date(y, mo, d)
                if rd < base:  # 開催日より前になる場合は翌年とみなさず、正規化しない
                    reserve = {"raw": rm.group(0), "normalized": None, "year": None, "weekday_check": "not_available", "warnings": ["予備日が開催日より前になるため正規化していません（原文のみ保持）"]}
                else:
                    reserve = {"raw": rm.group(0), "normalized": rd.isoformat(), "year": y, "weekday_check": _weekday_ok(rd, k), "warnings": [] if _weekday_ok(rd, k) == "ok" else ["原文の曜日と日付が一致しません（日付の数字は原文どおり保持）"]}
            except ValueError:
                reserve = {"raw": rm.group(0), "normalized": None, "year": None, "weekday_check": "not_available", "warnings": ["存在しない日付のため正規化していません"]}
        elif rm:
            reserve = {"raw": rm.group(0), "normalized": None, "year": None, "weekday_check": "not_available", "warnings": ["開催日を正規化できないため予備日も原文のみ保持します"]}
        events.append({
            "heading_text": full, "tag": tag, "title": title, "cancelled": cancelled,
            "date_text": re.sub(r"\s*(［|《).*$", "", main_zone).strip().rstrip("／ ") if main_zone else None,
            "periods": main_dates, "reserve": reserve,
            "links": links,
            "guideline_url": next((l["url"] for l in links if l["label"] == "《要項》"), None),
            "related_links": [l for l in links if l["label"] != "《要項》"],
            "no_link_markers": [m.group(0) for m in re.finditer(r"《(ドロー|リザルト|大会中止)》", full) if m.group(1) not in [l["label"].strip("《》") for l in links]],
        })
    return {"fatal_error": None, "charset": cs.__dict__, "events": events,
            "year_basis_main": "見出しの日付に西暦年が明記されている。曜日で検算",
            "year_basis_reserve": "予備日には年の表記が無いため、同じ見出しの開催日と同じ年を使用。曜日で検算"}


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _label(name: str) -> str:
    return r"\s*".join(re.escape(c) for c in name)


def _block(lines: List[str], start_idx: int, label_rx: str) -> List[str]:
    """ラベル行から、次の空行までを、ラベル部分を除いて返す（pdftotext -layout の段組）。"""
    out = []
    first = re.sub(r"^\s*" + label_rx + r"\s*", "", lines[start_idx], count=1).strip()
    out.append(first)
    for ln in lines[start_idx + 1:]:
        if not ln.strip():
            break
        out.append(ln.strip())
    return [x for x in out if x]


def parse_youkou_text(text: str) -> Dict[str, Any]:
    """要項PDFのテキスト（pdftotext -layout）から、明記された項目だけを取り出す。1ページ目（要項本体）だけを読む。"""
    page1 = text.split("\f")[0]
    t = _nfkc(page1)
    lines = t.split("\n")
    res: Dict[str, Any] = {"title": None}
    for ln in lines[:3]:
        if "回" in ln and "大会" in ln:
            res["title"] = re.sub(r"\s+", " ", ln).strip()
            break

    def find(label: str) -> Optional[int]:
        rx = re.compile(r"^\s*" + _label(label) + r"(?:\s{2,}|\s*$)")
        for i, ln in enumerate(lines):
            if rx.match(ln):
                return i
        return None

    def get(label: str) -> Optional[List[str]]:
        i = find(label)
        return _block(lines, i, _label(label)) if i is not None else None

    res["event_types_text"] = get("種目")
    res["date_block"] = get("開催日")
    res["venue_block"] = get("会場")
    res["eligibility_block"] = get("資格")
    res["capacity_block"] = get("定員")
    res["fee_block"] = get("参加費")
    res["entry_to_block"] = get("申込先")
    res["period_block"] = get("申込期間")
    # 開催日・予備日
    main = reserve = None
    for ln in res["date_block"] or []:
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\((.)\)(\(予備日\))?", ln)
        if m:
            d = {"raw": m.group(0), "normalized": datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat(), "weekday": m.group(4)}
            if m.group(5):
                reserve = d
            elif main is None:
                main = d
    res["main_date"], res["reserve_date"] = main, reserve
    # 申込期間: 10月4日(日)~10月25日(日) 17:00 まで（年は同じ要項の開催日の年）
    pm = re.search(r"(\d{1,2})月(\d{1,2})日\((.)\)\s*~\s*(\d{1,2})月(\d{1,2})日\((.)\)\s*(\d{1,2}):(\d{2})\s*まで", " ".join(res["period_block"] or []))
    res["entry_period"] = None
    if pm and main:
        y = int(main["normalized"][:4])
        s = datetime.date(y, int(pm.group(1)), int(pm.group(2)))
        e = datetime.date(y, int(pm.group(4)), int(pm.group(5)))
        res["entry_period"] = {
            "raw": pm.group(0), "start": s.isoformat(), "end": e.isoformat(), "end_time": "%02d:%s" % (int(pm.group(7)), pm.group(8)),
            "start_weekday_check": _weekday_ok(s, pm.group(3)), "end_weekday_check": _weekday_ok(e, pm.group(6)),
            "year_basis": "要項の開催日に明記された年（%d年）を使用。曜日で検算" % y,
        }
    return res
