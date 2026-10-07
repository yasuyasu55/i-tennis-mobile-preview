"""
TTA-MOBILE-021 浜松市テニス協会 専用パーサー（HTML＋要項PDFのテキスト。独立モジュール）

対象
  - 「大会情報」ページ（https://www.h-ta.jp/tournament.html）の3つの表。表は、見出しの文字ではなく、列の見出しで種類を判定する。
      「大会要項」  列: 開催予定日｜大会名｜開催場所｜要項（PDF）｜申込用紙（PDF）      → 今後の大会（取り込む）
      「仮ドロー」  列: 開催予定日｜大会名｜開催場所｜仮ドロー（PDF）                   → 要項の行と照合し、重複は統合。要項に無い大会だけ追加
      「大会結果」  列: 開催日｜大会名｜開催場所｜結果（PDF）                           → 終了済みの結果。取り込まない（件数だけ記録。終了済みで件数を増やさない）
  - 要項PDFのテキスト層（pdftotext -layout の出力）。「日時／会場／参加資格／参加料／申込先／申込締切」のラベルが並ぶ単純な形式だけ読む。
    表形式で文字の並びが崩れるPDF（市民スポーツ祭など）は、読み取れないものとして扱い、推測しない。

方針
  - 開催日は、表の「YYYY/M/D(曜)」表記（西暦が明記）。「,」で続く「M/D(曜)」「D(曜)」は、直前の年・月を引き継ぐ。
    曜日で検算し、一致しない日付は、原文のまま保持して注意を付ける（曜日から年・日付を決めない）。
    翌年の同じ月日なら曜日が一致する場合は、「年の表記ミスの可能性」と注記する（値は直さない）。
  - 要項PDFは、表の「要項」のリンク先として取得したものだけを、その大会に対応づける（大会名の似ている・似ていないで対応づけない）。
    PDFの開催日が、表の開催予定日のいずれかと一致しなければ使わない。
  - 申込締切の年は、PDFに書かれていない。開催日の年またはその前年のうち、締切が開催日より前で、曜日が一致するものが1つだけのときに限り決める。
  - 「オープン」の語だけで、誰でも参加できるとは判定しない（参加資格の本文にレベル・年齢などの条件があれば、そのとおりに区分する）。
  - 通信しない（リンクのURLを保持するだけ）。公式サイトの保存HTML・PDFは、公開物に含めない。
"""
from __future__ import annotations

import datetime
import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .common import absolutize_url, detect_charset, strict_decode

SOURCE_ID = "hamamatsu_tennis_association"
SOURCE_NAME = "浜松市テニス協会"
PAGE_URL = "https://www.h-ta.jp/tournament.html"
ALLOWED_HOST_SUFFIXES = ("h-ta.jp",)
WEEKDAY_KANJI = "月火水木金土日"

_TOKEN = re.compile(r"(?:(\d{4})\s*/\s*)?(?:(\d{1,2})\s*/\s*)?(\d{1,2})\s*[（(]+\s*([月火水木金土日])\s*[）)]+")
_FOLDER = re.compile(r"/tournament/(\d{4})/([^/]+)/")


def _text(tag) -> str:
    """セルの文字（<br>は空白に。連続する空白・全角空白・NBSPは1つの半角空白に）。"""
    for br in tag.find_all("br"):
        br.replace_with(" ")
    return re.sub(r"\s+", " ", tag.get_text(" ")).strip()


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _weekday(d: datetime.date) -> str:
    return WEEKDAY_KANJI[d.weekday()]


def parse_dates(cell: str) -> List[Dict[str, Any]]:
    """開催予定日のセル → 日付（または期間）の一覧。年・月が省略された日付は、直前の日付から引き継ぐ。"""
    text = unicodedata.normalize("NFKC", cell or "")
    out: List[Dict[str, Any]] = []
    year = month = None
    prev_end = 0
    pending_start: Optional[Dict[str, Any]] = None
    for m in _TOKEN.finditer(text):
        y, mo, d, k = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        if y:
            year = int(y)
        if mo:
            month = int(mo)
        gap = text[prev_end:m.start()] if out or pending_start else ""
        prev_end = m.end()
        raw = m.group(0)
        warn: List[str] = []
        norm = None
        wk = "not_available"
        if year is None or month is None:
            warn.append("年または月の表記が無いため、日付を正規化していません（原文のみ保持）")
        else:
            try:
                dd = datetime.date(year, month, d)
                norm = dd.isoformat()
                wk = "ok" if _weekday(dd) == k else "mismatch"
                if wk == "mismatch":
                    warn.append("原文の曜日と日付が一致しません（日付の数字は原文どおり保持）")
                    try:
                        alt = datetime.date(year + 1, month, d)
                        if _weekday(alt) == k:
                            warn.append("%s ならば曜日が一致します。年の表記ミスの可能性があります（原文は直していません。要項で確認してください）" % alt.isoformat())
                    except ValueError:
                        pass
            except ValueError:
                warn.append("存在しない日付のため正規化していません")
        # 「~」「〜」で結ばれた日付は、期間として扱う
        if out and re.fullmatch(r"\s*[~〜～\-−ー]\s*", gap) and out[-1]["normalized"] and norm and "/" not in out[-1]["normalized"]:
            out[-1] = {"raw": out[-1]["raw"] + gap.strip() + raw, "normalized": out[-1]["normalized"] + "/" + norm, "year": out[-1]["year"],
                       "weekday_check": out[-1]["weekday_check"] if out[-1]["weekday_check"] != "ok" else wk, "warnings": out[-1]["warnings"] + warn}
            continue
        out.append({"raw": raw, "normalized": norm, "year": year, "weekday_check": wk, "warnings": warn})
    return out


def _folder_of(*urls: Optional[str]):
    for u in urls:
        m = _FOLDER.search(u or "")
        if m:
            return int(m.group(1)), m.group(2)
    return None, None


def _first_link(cell, page_url: str) -> Optional[str]:
    for a in cell.find_all("a"):
        u = absolutize_url(page_url, a.get("href"))
        if u:
            return u
    return None


def _header_kind(headers: List[str]) -> Optional[str]:
    h = [_nospace(x) for x in headers]
    if all(x in h for x in ("開催予定日", "大会名", "開催場所", "要項")):
        return "guidelines"
    if all(x in h for x in ("開催予定日", "大会名", "開催場所", "仮ドロー")):
        return "predraw"
    if all(x in h for x in ("開催日", "大会名", "開催場所", "結果")):
        return "results"
    return None


def parse_tournament_page(raw: bytes, page_url: str = PAGE_URL) -> Dict[str, Any]:
    cs = detect_charset(raw)
    if not cs.confident or not cs.charset:
        return {"fatal_error": "文字コードを確定できません（" + cs.detail + "）", "guidelines": [], "predraw": [], "events": [], "results_count": 0}
    return parse_tournament_text(strict_decode(raw, cs.charset), page_url, cs.__dict__)


def parse_tournament_text(text: str, page_url: str = PAGE_URL, charset_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """HTMLの文字列（バイト列を復号したもの、またはブラウザが描画した後のDOMの文字列）から読み取る。"""
    charset_info = charset_info or {"charset": "dom(str)", "confident": True}
    soup = BeautifulSoup(text, "lxml")
    rows: Dict[str, List[Dict[str, Any]]] = {"guidelines": [], "predraw": [], "results": []}
    seen_kinds: List[str] = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        header_cells = trs[0].find_all(["th", "td"])
        headers = [_text(c) for c in header_cells]
        kind = _header_kind(headers)
        if kind is None:
            continue
        seen_kinds.append(kind)
        idx = {_nospace(h): i for i, h in enumerate(headers)}
        for n, tr in enumerate(trs[1:], 1):
            cells = tr.find_all(["td", "th"])
            if len(cells) < len(headers):
                continue
            def col(name):
                return cells[idx[name]] if name in idx else None
            date_cell = col("開催予定日") or col("開催日")
            title_c, venue_c = col("大会名"), col("開催場所")
            if date_cell is None or title_c is None:
                continue
            title = _text(title_c)
            if not title:
                continue
            rec = {"table": kind, "row": n, "date_text": _text(date_cell), "title": title, "venue": _text(venue_c) if venue_c is not None else None,
                   "periods": parse_dates(_text(date_cell))}
            if kind == "guidelines":
                rec["guideline_url"] = _first_link(col("要項"), page_url) if col("要項") is not None else None
                rec["entry_url"] = _first_link(col("申込用紙"), page_url) if col("申込用紙") is not None else None
                rec["folder_year"], rec["folder"] = _folder_of(rec["guideline_url"], rec["entry_url"])
            elif kind == "predraw":
                rec["predraw_url"] = _first_link(col("仮ドロー"), page_url)
                rec["folder_year"], rec["folder"] = _folder_of(rec["predraw_url"])
            else:
                rec["result_url"] = _first_link(col("結果"), page_url)
            rows[kind].append(rec)
    if "guidelines" not in seen_kinds:
        return {"fatal_error": "「大会要項」の表（列: 開催予定日・大会名・開催場所・要項）が見つかりません（ページの構造が変わった可能性）", "guidelines": [], "predraw": rows["predraw"],
                "events": [], "results_count": len(rows["results"]), "charset": charset_info}
    events = merge_rows(rows["guidelines"], rows["predraw"])
    return {"fatal_error": None, "charset": charset_info, "guidelines": rows["guidelines"], "predraw": rows["predraw"], "events": events,
            "results_count": len(rows["results"]), "tables_found": seen_kinds, "page_url": page_url}


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _same_event(p: Dict[str, Any], e: Dict[str, Any]) -> bool:
    """仮ドローの行 p が、要項の行 e の大会に含まれるか。大会名の共通部分（大会の名前）を除いた残りが、要項の大会名に含まれ、かつ日付が重なること。"""
    pn, en = _nospace(p["title"]), _nospace(e["title"])
    if not pn or not en:
        return False
    if pn in en:
        contained = True
    else:
        L = _common_prefix_len(pn, en)
        rest = pn[L:]
        contained = L >= 8 and len(rest) >= 4 and rest in en[L:]
    if not contained:
        return False
    pd = {x["normalized"] for x in p["periods"] if x["normalized"]}
    ed = {x["normalized"] for x in e["periods"] if x["normalized"]}
    return not pd or not ed or bool(pd & ed)       # 日付が分かる場合は、いずれかが一致すること（大会名が似ているだけでは統合しない）


def merge_rows(guidelines: List[Dict[str, Any]], predraw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    「大会要項」の行を1大会として取り込む。「仮ドロー」の行は、次の2つが両方成り立つときだけ、同じ大会として統合する（名前が似ているだけでは統合しない）。
      1) 仮ドローの大会名（空白除去）が、要項の行の大会名（空白除去）に含まれている
      2) 仮ドローのPDFと要項のPDFが、同じ公式フォルダ（/tournament/YYYY/<フォルダ>/）にある
    統合されなかった仮ドローの行は、要項が無い大会として追加する（同じフォルダに要項の行があれば、その要項PDFを参考として紐づける）。
    """
    events: List[Dict[str, Any]] = []
    for g in guidelines:
        events.append({"origin": "guidelines", "title": g["title"], "date_text": g["date_text"], "periods": g["periods"], "venue": g["venue"],
                       "guideline_url": g.get("guideline_url"), "entry_url": g.get("entry_url"), "predraw_urls": [], "folder_year": g.get("folder_year"), "folder": g.get("folder"),
                       "folder_has_guideline": True})
    for p in predraw:
        hit = None
        for e in events:
            if e["origin"] != "guidelines" or not p.get("folder") or p["folder"] != e["folder"] or p.get("folder_year") != e["folder_year"]:
                continue
            if _same_event(p, e):
                hit = e
                break
        if hit is not None:
            if p.get("predraw_url"):
                hit["predraw_urls"].append(p["predraw_url"])
            continue
        same_folder = next((e for e in events if e["origin"] == "guidelines" and p.get("folder") and e["folder"] == p["folder"] and e["folder_year"] == p.get("folder_year")), None)
        events.append({"origin": "predraw_only", "title": p["title"], "date_text": p["date_text"], "periods": p["periods"], "venue": p["venue"],
                       "guideline_url": same_folder["guideline_url"] if same_folder else None, "entry_url": None,
                       "predraw_urls": [p["predraw_url"]] if p.get("predraw_url") else [], "folder_year": p.get("folder_year"), "folder": p.get("folder"),
                       "folder_has_guideline": bool(same_folder)})
    return events


# ---------------------------------------------------------------------------
# 要項PDFのテキスト
# ---------------------------------------------------------------------------
_LABELS = ["日時", "開催日", "会場", "主催", "共催", "主管", "協賛", "後援", "種目", "試合方法", "使用球", "参加資格", "参加料", "参加費", "申込先", "申込締切", "申込期間", "注意事項", "集合時間", "予備日"]
_WK = "月火水木金土日"


def _label_rx(name: str) -> "re.Pattern[str]":
    return re.compile(r"^\s*" + r"\s*".join(re.escape(c) for c in name) + r"\s*[:：]\s*(.*)$")


_LABEL_RXS = [(n, _label_rx(n)) for n in _LABELS]


_CJK = "ぁ-んァ-ヶ一-龥々〆%％円"


def _tidy(ln: str) -> str:
    """PDFの文字間の空白を整える（数字と、後ろ・前の日本語の単位の間の空白を除く。例: 「45 歳以上」→「45歳以上」）。"""
    ln = re.sub(r"(?<=\d)\s+(?=[%s])" % _CJK, "", ln)
    ln = re.sub(r"(?<=[%s])\s+(?=\d)" % _CJK, "", ln)
    return re.sub(r"\s+", " ", ln).strip()


def parse_youkou_text(text: str) -> Dict[str, Any]:
    """要項PDFのテキストから、明記された項目だけを取り出す。ラベル付きの単純な形式でなければ ok=False（推測しない）。"""
    page1 = text.split("\f")[0]
    lines = unicodedata.normalize("NFKC", page1).split("\n")
    blocks: Dict[str, List[str]] = {}
    current = None
    title = None
    for ln in lines:
        hit = None
        for name, rx in _LABEL_RXS:
            m = rx.match(ln)
            if m:
                hit = (name, m.group(1).strip())
                break
        if hit:
            current = hit[0]
            blocks.setdefault(current, [])
            if hit[1]:
                blocks[current].append(_tidy(hit[1]))
        elif current is None:
            if ln.strip() and title is None:
                title = re.sub(r"\s+", " ", ln).strip()
        elif ln.strip():
            blocks[current].append(_tidy(ln))
    res: Dict[str, Any] = {"title": title, "blocks": blocks, "problems": []}
    dates = []
    for ln in blocks.get("日時", []) + blocks.get("開催日", []):
        for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[（(]\s*([月火水木金土日])\s*[）)]", ln):
            try:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
            dates.append({"raw": re.sub(r"\s+", "", m.group(0)), "normalized": d.isoformat(), "weekday_check": "ok" if _weekday(d) == m.group(4) else "mismatch"})
    res["dates"] = dates
    res["venue"] = (blocks.get("会場") or [None])[0]
    res["events_text"] = blocks.get("種目", [])
    res["eligibility_lines"] = blocks.get("参加資格", [])
    res["fee_lines"] = blocks.get("参加料", []) or blocks.get("参加費", [])
    res["entry_to_lines"] = blocks.get("申込先", [])
    res["notes_lines"] = blocks.get("注意事項", [])
    dl = None
    for ln in blocks.get("申込締切", []):
        m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[（(]\s*([月火水木金土日])\s*[）)]\s*(?:(\d{1,2})\s*[:：]\s*(\d{2}))?\s*([迄まで必着]*)", ln)
        if m:
            dl = {"month": int(m.group(1)), "day": int(m.group(2)), "weekday": m.group(3), "time": ("%02d:%s" % (int(m.group(4)), m.group(5))) if m.group(4) else None,
                  "suffix": (m.group(6) or "").strip() or None, "line": ln}
            break
    res["deadline"] = dl
    if not dates:
        res["problems"].append("日時を読み取れません")
    if not res["eligibility_lines"]:
        res["problems"].append("参加資格を読み取れません")
    if not res["fee_lines"]:
        res["problems"].append("参加料を読み取れません")
    if not res["entry_to_lines"]:
        res["problems"].append("申込先を読み取れません")
    if dl is None:
        res["problems"].append("申込締切を読み取れません")
    res["ok"] = not res["problems"]
    return res


def resolve_deadline_year(event_date_iso: str, month: int, day: int, weekday: str) -> Optional[str]:
    """締切の年（PDFに記載なし）。開催日の年またはその前年のうち、締切が開催日以前で曜日が一致するものが1つだけのときに限り決める。"""
    ev = datetime.date.fromisoformat(event_date_iso)
    cands = []
    for y in (ev.year, ev.year - 1):
        try:
            d = datetime.date(y, month, day)
        except ValueError:
            continue
        if d <= ev and _weekday(d) == weekday:
            cands.append(d)
    return cands[0].isoformat() if len(cands) == 1 else None


_RX_REGION = re.compile(r"市内在住|市内在勤|在住|在勤|在学")
_RX_ASSOC = re.compile(r"協会登録|協会員|会員(?!制)")
_RX_AGE = re.compile(r"\d+歳以上|\d+歳未満|年齢|生まれ|出生")
_RX_LEVEL = re.compile(r"未進出者|ベスト\s*\d+|\d+\s*回戦|クラス|レベル|ランク|\d+級|経験者|未経験者|シード")
_RX_TEAM = re.compile(r"チーム|団体|クラブ単位")


def classify_youkou_eligibility(lines: List[str]) -> Dict[str, Any]:
    """要項PDFの参加資格欄の文から、参加資格の区分を決める。複数の種類の条件があれば「その他の条件あり」。「オープン」だけでは、条件なしとは判定しない。"""
    text = " ".join(lines)
    age_text = re.sub(r"年齢\s*制限\s*(?:なし|無し)|年齢\s*不問|制限\s*なし", "", text)      # 「年齢制限なし」は、年齢条件ではない
    kinds = []
    if _RX_REGION.search(text):
        kinds.append("地域条件あり")
    if _RX_ASSOC.search(text):
        kinds.append("協会登録必要")
    if _RX_AGE.search(age_text):
        kinds.append("年齢条件あり")
    if _RX_LEVEL.search(text):
        kinds.append("その他の条件あり")
    if _RX_TEAM.search(text):
        kinds.append("団体・所属条件あり")
    kinds = list(dict.fromkeys(kinds))
    open_word = "オープン" in text
    if len(kinds) > 1:
        status, why = "その他の条件あり", "複数の種類の条件が明記されています（%s）" % "・".join(kinds)
    elif len(kinds) == 1:
        status, why = kinds[0], {"その他の条件あり": "レベル条件が明記されています", "年齢条件あり": "年齢条件が明記されています", "地域条件あり": "地域条件が明記されています",
                                 "協会登録必要": "協会登録（会員）の条件が明記されています", "団体・所属条件あり": "団体・所属の条件が明記されています"}[kinds[0]]
    elif open_word:
        status, why = "オープン参加（詳細条件は要項確認）", "「オープン」と明記されています（年齢・レベルなどの詳細条件は、要項で確認してください）"
    else:
        return {"status": "参加資格要確認", "note": "要項の資格欄の内容を分類できないため、原文を表示します。", "matched": None, "text": text}
    note = "要項PDFの資格欄に明記: %s。" % why
    if open_word and kinds:
        note += "「オープン」とありますが、誰でも無条件に参加できるわけではありません（原文の条件を確認してください）。"
    m = (_RX_LEVEL.search(text) or _RX_AGE.search(age_text) or _RX_REGION.search(text) or _RX_ASSOC.search(text) or _RX_TEAM.search(text) or re.search(r"オープン", text))
    return {"status": status, "note": note, "matched": m.group(0) if m else None, "text": text}
