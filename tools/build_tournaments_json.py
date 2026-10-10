#!/usr/bin/env python3
"""
共通大会JSON 生成スクリプト（TTA-MOBILE-012 → 014-R2）

入力は、{論理ファイル名: bytes} の辞書（PROVIDER）。自動取得（pipeline/run_update.py）が、公式ページを取得して渡す。
公開リポジトリには、公式サイトから保存した完全なHTML・PDF・テキストを入れない。パーサーのテスト用には、
架空の大会による「DOM構造だけを再現した」合成フィクスチャ（tests/fixtures/synthetic/）を使う。

  愛知県テニス協会  aichi_tennis_schedule.html                                   → 承認済みパーサー(TTA-MOBILE-006-R1)
  豊橋テニス協会    toyohashi_guidelines_list(.html/_page2.html), toyohashi_guideline_<投稿ID>.html（任意）
                                                                                  → 承認済みパーサー(TTA-MOBILE-007)
  蒲郡テニス協会    gamagori_tournament.html, gamagori_entry.html（任意）            → パーサー(TTA-MOBILE-012)
  豊川テニス協会    toyokawa_top.html, toyokawa_<キー>_youkou.txt（要項PDFの文字情報。任意）→ パーサー(TTA-MOBILE-013-A1)
  浜松市テニス協会  hamamatsu_tournament.html, hamamatsu_<URLのSHA-256先頭12桁>_youkou.txt（要項PDFの文字情報。任意）→ パーサー(TTA-MOBILE-021)

方針
  - 取得できない値は推測せず null（不明）のままにする。空文字は使わない。
  - 年度は、実ページの見出し（愛知県「YYYY年度」・蒲郡「令和N年度」）または開催日（豊川）から取得する。固定値は持たない。取得できなければ失敗（BuildError）。
  - 豊橋は、一覧ページに開催日が無いため「日付未取得」。タイトルの年月表記は date_hint として保持するだけで、開催日にしない。
  - 分類（対象者区分・種目・参加資格）は tools/tournament_classify.py のルールで行い、根拠を classification_basis に記録する。
  - 大会IDは、情報源・年度・大会固有キーから決める安定ID（tools/stable_ids.py）。

使い方（合成フィクスチャでの再生成・確認。テスト用）
   python3 tools/build_tournaments_json.py --fixtures tests/fixtures/synthetic            # 期待データを再生成（上書き）
   python3 tools/build_tournaments_json.py --fixtures tests/fixtures/synthetic --check    # 期待データと完全一致するか確認（差異があれば終了コード1）
"""
import collections
import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "data-source"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from parsers import aichi_tennis as at          # noqa: E402  承認済み(006-R1)・無改変
from parsers import toyohashi_tennis as tt      # noqa: E402  承認済み(007)・無改変
from parsers import gamagori_tennis as gt       # noqa: E402  新規(012)
from parsers import toyokawa_tennis as tk       # noqa: E402  新規(013-A1)
from parsers import hamamatsu_tennis as hm      # noqa: E402  新規(021)
from parsers import okazaki_tennis as ok        # noqa: E402  岡崎（読み取り・候補生成）
from parsers import okazaki_pdf as op           # noqa: E402  要項の費用・方法別締切
from parsers import hamamatsu_pdf_table as ht   # noqa: E402  複数種目を含む表形式の補足
import stable_ids                               # noqa: E402  安定ID（TTA-MOBILE-014-R1）
import tournament_classify as tc                # noqa: E402

SNAPSHOT_DATE = {}                 # 取得日（資料時点）。呼び出し側が configure() で指定する（固定値は持たない）
PDF_CONFIRMED_ON = None            # 要項PDFの確認日。同上
FIXED_RETRIEVED_AT = None          # パーサーに渡す取得日時。同上

# 入力の論理名
AICHI_INPUT = "aichi_tennis_schedule.html"
TOYOHASHI_LIST1 = "toyohashi_guidelines_list.html"
TOYOHASHI_LIST2 = "toyohashi_guidelines_list_page2.html"
GAMAGORI_TOURNAMENT = "gamagori_tournament.html"
GAMAGORI_ENTRY = "gamagori_entry.html"
TOYOKAWA_TOP = "toyokawa_top.html"
HAMAMATSU_PAGE = "hamamatsu_tournament.html"
OKAZAKI_PAGE = "okazaki_tournament.html"

WEEKDAY_KANJI = "月火水木金土日"
WEEKDAY_TOKEN = re.compile(r"\(([月火水木金土日])\)")
TITLE_YEAR_RE = re.compile(r"^(\d{4})(?:-(\d{1,2}))?_")


def sha(b):
    return hashlib.sha256(b).hexdigest()


class BuildError(Exception):
    """情報源1つの生成に失敗したことを表す。パイプラインは、この情報源だけ前回成功データを保持して続行する。"""


class MissingInput(BuildError):
    pass


# 入力: {論理ファイル名: bytes}。自動取得、またはテストの合成フィクスチャ（load_fixtures）が設定する。
PROVIDER = None
READ_LOG = {}        # 今回の生成で読んだ入力 {名前: sha256}
RUN_WARNINGS = []    # 生成中の注意（要項PDFを対応づけられなかった等）。失敗ではない


def configure(snapshot_dates=None, pdf_confirmed_on=None, retrieved_at=None):
    """取得日（資料時点）などを、呼び出し側が指定する。"""
    global PDF_CONFIRMED_ON, FIXED_RETRIEVED_AT
    if snapshot_dates:
        SNAPSHOT_DATE.update(snapshot_dates)
    if pdf_confirmed_on:
        PDF_CONFIRMED_ON = pdf_confirmed_on
    if retrieved_at:
        FIXED_RETRIEVED_AT = retrieved_at


def read(name, optional=False):
    raw = (PROVIDER or {}).get(name)
    if raw is None:
        if optional:
            return None
        raise MissingInput("入力がありません: " + name)
    READ_LOG[name] = sha(raw)
    return raw


def provider_names(prefix, suffix=""):
    """prefix/suffix に合う入力名（名前順）。"""
    return sorted(n for n in (PROVIDER or {}) if n.startswith(prefix) and n.endswith(suffix))


def input_files(key):
    return sorted((n, h) for n, h in READ_LOG.items() if n.startswith(key + "_"))


def notnull(s):
    s = (s or "").strip() if isinstance(s, str) else s
    return s if s else None


def aichi_weekday_check(p):
    norm = p.get("normalized")
    if not norm:
        return "not_available"
    dates = [datetime.date.fromisoformat(x) for x in norm.split("/")]
    tokens = WEEKDAY_TOKEN.findall(p.get("raw", ""))
    if len(tokens) != len(dates):
        return "not_available"
    return "ok" if all(WEEKDAY_KANJI[d.weekday()] == t for d, t in zip(dates, tokens)) else "mismatch"


def aichi_mismatch_note(p):
    dates = [datetime.date.fromisoformat(x) for x in p["normalized"].split("/")]
    tokens = WEEKDAY_TOKEN.findall(p["raw"])
    parts = []
    for d, t in zip(dates, tokens):
        if WEEKDAY_KANJI[d.weekday()] != t:
            parts.append("原文の「%s(%s)」は%d年%d月%d日の曜日（%s）と一致しません" % (d.day, t, d.year, d.month, d.day, WEEKDAY_KANJI[d.weekday()]))
    return "曜日の検算: " + "、".join(parts) + "。日付の数字は原文どおり保持しています。要項で確認してください。"


def make_record(**kw):
    """共通形式（TTA-MOBILE-012 §10）。不明値は null。"""
    base = {
        "id": None, "fiscal_year": None, "event_key": None, "title": None, "source_id": None, "source_name": None, "source_area": None, "event_area": None,
        "date_text": None, "periods": [], "reserve_text": None, "reserve_periods": [], "date_hint": None, "year_basis": None,
        "deadline_text": None, "deadline_date": None, "deadline_time": None,
        "venue": None, "event_types": None, "primary_event_types": None, "component_match_types": [], "audience_types": ["不明"],
        "eligibility_status": "参加資格要確認", "eligibility_text": None, "eligibility_note": None,
        "official_url": None, "guideline_url": None, "entry_url": None, "entry_text": None,
        "parse_status": "partial", "classification_basis": [], "warnings": [], "notes": [],
        "source_snapshot_date": None,
    }
    base.update(kw)
    return base


def apply_common_classification(rec, extra_audience_texts=None, extra_event_texts=None, table_eligibility=None):
    atypes, abasis = tc.classify_audience(rec["title"], extra_audience_texts)
    etypes, ebasis = tc.classify_event_types(rec["title"], extra_event_texts)
    rec["audience_types"] = atypes
    rec["event_types"] = etypes
    rec["classification_basis"] = abasis + ebasis
    if table_eligibility is not None:
        status, text, note, basis = tc.classify_eligibility_from_table(table_eligibility)
        rec["eligibility_status"], rec["eligibility_text"], rec["eligibility_note"] = status, text, note
        rec["classification_basis"] += basis
    else:
        rec["eligibility_status"], rec["eligibility_text"] = "参加資格要確認", None
        hints = tc.eligibility_hint(rec["title"])
        rec["eligibility_note"] = "（タイトルの語から）" + "／".join(hints) if hints else "参加資格を確認できる情報が取得できていません（要項で確認）"
        rec["classification_basis"].append({"field": "eligibility", "value": "参加資格要確認", "matched": None, "source": "—",
                                            "rule": "要項の本文を解析していないため、参加資格は確定しない"})
    return rec


def build_aichi():
    raw = read(AICHI_INPUT)
    result = at.parse(raw, page_url=at.DEFAULT_PAGE_URL, retrieved_at=FIXED_RETRIEVED_AT)
    if result.fatal_error:
        raise BuildError("愛知県パーサー失敗: " + result.fatal_error)
    recs = []
    for i, r in enumerate(result.records, 1):
        periods, notes = [], []
        for p in r.event_date_periods:
            chk = aichi_weekday_check(p)
            if chk == "mismatch":
                notes.append(aichi_mismatch_note(p))
            periods.append({"raw": p["raw"], "normalized": p["normalized"], "year": p["year"], "weekday_check": chk, "warnings": p["warnings"]})
        rec = make_record(
            id="a-%d" % i, title=r.tournament_name, source_id=at.SOURCE_ID, source_name=at.SOURCE_NAME,
            source_area="愛知県", event_area="愛知県協会掲載", date_text=notnull(r.event_date_text), periods=periods,
            year_basis=r.year_basis, venue=notnull(r.venue_text), official_url=r.source_page_url, guideline_url=r.guideline_url,
            parse_status=r.parse_status, warnings=list(r.warnings), notes=notes, source_snapshot_date=SNAPSHOT_DATE["aichi"],
        )
        recs.append(apply_common_classification(rec))
    fys = collections.Counter(int(m.group(1)) for r in result.records for m in [re.search(r"(\d{4})年度見出しから導出", r.year_basis)] if m)
    if len(fys) != 1:
        raise BuildError("愛知県: 年度見出しから年度を1つに特定できません（%s）。年度の推測は行いません" % (dict(fys) or "取得できず"))
    fy = next(iter(fys))
    stable_ids.assign_ids("aichi", recs, [fy] * len(recs))
    return recs, result.to_dict()


def build_toyohashi():
    raw1 = read(TOYOHASHI_LIST1)
    raw2 = read(TOYOHASHI_LIST2)
    l1 = tt.parse_listing(raw1, "https://www.toyohashi-tennis.net/c/taikai/guidelines/", retrieved_at=FIXED_RETRIEVED_AT)
    l2 = tt.parse_listing(raw2, "https://www.toyohashi-tennis.net/c/taikai/guidelines/page/2/", retrieved_at=FIXED_RETRIEVED_AT)
    for r in (l1, l2):
        if r.fatal_error:
            raise BuildError("豊橋パーサー失敗: " + r.fatal_error)
    details, detail_raw = {}, {}
    for name in provider_names("toyohashi_guideline_", ".html"):
        raw3 = read(name, optional=True)
        pid = re.search(r"toyohashi_guideline_(\d+)\.html$", name)
        if raw3 is None or not pid:
            continue
        d = tt.parse_guideline_detail(raw3, "https://www.toyohashi-tennis.net/taikai/guidelines/%s/" % pid.group(1), retrieved_at=FIXED_RETRIEVED_AT)
        if d.fatal_error or not d.record:
            RUN_WARNINGS.append("豊橋: 詳細ページ(%s)を解析できなかったため、その大会の要項・申込URLは未取得のまま: %s" % (pid.group(1), d.fatal_error))
            continue
        details[d.record.post_id] = d.record
        detail_raw["detail_%s" % pid.group(1)] = d.to_dict()
    recs = []
    for e in l1.entries + l2.entries:
        m = TITLE_YEAR_RE.match(e.title_text)
        hint = None
        if m:
            hint = "タイトルの年月表記: %s-%s（開催日ではありません）" % (m.group(1), m.group(2)) if m.group(2) else "タイトルの年表記: %s年（開催日ではありません）" % m.group(1)
        guideline_url = entry_url = None
        notes = []
        detail = details.get(e.post_id)
        if detail:
            guideline_url = detail.guideline_links[0]["url"] if detail.guideline_links else None
            entry_url = detail.entry_form_links[0]["url"] if detail.entry_form_links else None
        else:
            notes.append("この大会の要項PDF・申込フォームのURLは、投稿ページ（公式情報URL）で確認してください（投稿ページの本文は今回取得していません）")
        rec = make_record(
            id="toyohashi-%s" % e.post_id, title=e.title_text, source_id=tt.SOURCE_ID, source_name=tt.SOURCE_NAME,
            source_area="豊橋", event_area="豊橋", date_hint=hint,
            official_url=e.post_url, guideline_url=guideline_url, entry_url=entry_url,
            parse_status="partial",
            warnings=["開催日・会場・申込締切は一覧ページに記載がなく未取得（要項PDFの本文は解析していません）。投稿の表示日は開催日として使っていません"],
            notes=notes, source_snapshot_date=SNAPSHOT_DATE["toyohashi"],
        )
        recs.append(apply_common_classification(rec))
    for r in recs:     # 豊橋は、公式の投稿ID（投稿URL）が固有キー。IDは従来どおり
        r["fiscal_year"] = None
        r["event_key"] = "post:" + r["id"].split("-", 1)[1]
    out = {"listing_page1": l1.to_dict(), "listing_page2": l2.to_dict()}
    out.update(detail_raw)
    return recs, out


def build_gamagori():
    raw_t = read(GAMAGORI_TOURNAMENT)
    raw_e = read(GAMAGORI_ENTRY, optional=True)
    entry_pages = gt.parse_entry_page(raw_e) if raw_e else {}
    res = gt.parse_tournament_page(raw_t, entry_pages=entry_pages)
    if res.fatal_error:
        raise BuildError("蒲郡パーサー失敗: " + res.fatal_error)
    if res.fiscal_year is None or not res.records:
        raise BuildError("蒲郡: 見出し「令和N年度」から年度を取得できません（年度の推測は行いません）")
    recs = []
    for r in res.records:
        periods = [{"raw": r.date.raw, "normalized": r.date.normalized, "year": r.date.year, "weekday_check": r.date.weekday_check, "warnings": r.date.warnings}]
        reserve = [{"raw": r.reserve.raw, "normalized": r.reserve.normalized, "year": r.reserve.year, "weekday_check": r.reserve.weekday_check, "warnings": r.reserve.warnings}]
        rec = make_record(
            id="g-%d" % r.row_index, title=r.title, source_id=gt.SOURCE_ID, source_name=gt.SOURCE_NAME,
            source_area="蒲郡", event_area="蒲郡", date_text=notnull(r.date.raw), periods=periods,
            reserve_text=notnull(r.reserve.raw), reserve_periods=reserve, year_basis=r.year_basis,
            deadline_text=r.deadline_text, deadline_date=r.deadline_date, deadline_time=r.deadline_time,
            venue=notnull(r.venue_text), official_url=gt.TOURNAMENT_PAGE_URL, guideline_url=r.guideline_url,
            entry_url=r.entry_url, entry_text=("エントリー先: " + r.entry_place_text) if r.entry_place_text else None,
            parse_status=r.parse_status, warnings=list(r.warnings), source_snapshot_date=SNAPSHOT_DATE["gamagori"],
        )
        cell_texts = []
        if r.eligibility_text:
            cell_texts.append(("一覧表の参加資格欄", r.eligibility_text))
        for line in r.event_lines:
            cell_texts.append(("一覧表の種目欄", line))
        apply_common_classification(rec, extra_audience_texts=[(w, t) for w, t in cell_texts if w == "一覧表の参加資格欄"],
                                    extra_event_texts=cell_texts, table_eligibility=r.eligibility_text)
        recs.append(rec)
    stable_ids.assign_ids("gamagori", recs, [res.fiscal_year] * len(recs))
    return recs, res.to_dict()


def _nfkc(s):
    import unicodedata
    return unicodedata.normalize("NFKC", s)


def _clean_label_block(block):
    """PDFのラベル付きブロックを、行ごとの文字列にする（先頭のラベル残りは parse 側で除去済み）。"""
    return [re.sub(r"\s+", " ", x).strip() for x in (block or []) if x.strip()]


def build_toyokawa():
    raw_h = read(TOYOKAWA_TOP)
    txts = []
    for name in provider_names("toyokawa_", "_youkou.txt"):
        t = read(name, optional=True)
        if t is not None:
            txts.append((name, t.decode("utf-8")))
    top = tk.parse_top_page(raw_h)
    if top["fatal_error"]:
        raise BuildError("豊川パーサー失敗: " + top["fatal_error"])
    if not top["events"]:
        raise BuildError("豊川: 大会の見出しが1件も見つかりません（ページの構造が変わった可能性）")
    youkou_all = [tk.parse_youkou_text(t) for _n, t in txts]
    norm_title = lambda s: re.sub(r"\s+|【[^】]*】", "", _nfkc(s))
    youkou_by_idx = {}
    for (name, _t), y in zip(txts, youkou_all):
        hits = [i for i, e in enumerate(top["events"]) if norm_title(e["title"]) == norm_title(y["title"] or "") and e["periods"] and y["main_date"] and e["periods"][0]["normalized"] == y["main_date"]["normalized"]]
        if len(hits) != 1:
            RUN_WARNINGS.append("豊川: 要項PDF(%s)を保存HTMLの大会と一意に対応づけられないため使用しません" % name)
            continue
        e = top["events"][hits[0]]
        ep = y["entry_period"]
        problems = []
        if not ep or ep["end_weekday_check"] != "ok" or ep["start_weekday_check"] != "ok":
            problems.append("申込期間を解釈できません")
        if not (y["eligibility_block"] and y["venue_block"] and y["fee_block"] and y["entry_to_block"]):
            problems.append("資格・会場・参加費・申込先のいずれかを読み取れません")
        if (y["reserve_date"] or {}).get("normalized") != (e["reserve"] or {}).get("normalized"):
            problems.append("予備日が保存HTMLと一致しません")
        if problems:
            RUN_WARNINGS.append("豊川: 要項PDF(%s)は %s ため使用しません（その大会は一覧の情報のみ）" % (name, "・".join(problems)))
            continue
        youkou_by_idx[hits[0]] = y

    recs = []
    for idx, e in enumerate(top["events"]):
        y = youkou_by_idx.get(idx)
        periods = e["periods"]
        reserve_periods = [e["reserve"]] if e["reserve"] else []
        warnings, notes = [], []
        if e["cancelled"]:
            warnings.append("サイトの見出しに「大会中止」の表記があります（開催されない可能性）")
            notes.append("サイトの見出しに「大会中止」の表記があります")
        if e["no_link_markers"]:
            notes.append("サイトの見出しに、リンクの無い表示があります: " + " ".join(e["no_link_markers"]))
        rec = make_record(
            id="t-%d" % idx, title=e["title"], source_id=tk.SOURCE_ID, source_name=tk.SOURCE_NAME,
            source_area="豊川", event_area="豊川", date_text=e["date_text"], periods=periods,
            reserve_text=(e["reserve"]["raw"] if e["reserve"] else None), reserve_periods=reserve_periods,
            year_basis=top["year_basis_main"] + "／" + top["year_basis_reserve"],
            official_url=tk.OFFICIAL_URL, guideline_url=e["guideline_url"], entry_url=None,
            parse_status="partial", warnings=warnings, notes=notes, source_snapshot_date=SNAPSHOT_DATE["toyokawa"],
        )
        rec["fee_text"] = None
        rec["related_links"] = [{"label": l["label"].strip("《》"), "url": l["url"]} for l in e["related_links"]]
        apply_common_classification(rec)
        if "小中学生" in e["title"] and "小学生" not in rec["audience_types"]:   # 既存の分類ルールは変更せず、豊川の記録にだけ適用
            rec["audience_types"] = [a for a in rec["audience_types"] if a != "不明"] + ["小学生"]
            rec["audience_types"].sort(key=lambda a: ["一般", "ベテラン", "ジュニア", "小学生", "中学生", "高校生", "学生", "実業団・企業", "教職員", "団体限定", "不明"].index(a))
            rec["classification_basis"].append({"field": "audience", "value": "小学生", "matched": "小中学生", "source": "タイトル", "rule": "「小中学生」は小学生と中学生を含む（既存の分類ルールは変更せず、この記録にだけ追加）"})
        tag = e["tag"]
        rec["classification_basis"].append({"field": "eligibility", "value": "サイトの区分表示", "matched": "［%s］" % tag if tag else None, "source": "保存HTMLの見出し",
                                            "rule": "サイトの見出しの区分表示。参加資格の確定には使わない（要項で確認）"})
        if tag == "協会員限定":
            rec["eligibility_status"], rec["eligibility_text"] = "協会登録必要", "協会員限定"
            rec["eligibility_note"] = "サイトの見出しに［協会員限定］とあります（要項PDFは未確認）。"
            rec["classification_basis"].append({"field": "eligibility", "value": "協会登録必要", "matched": "［協会員限定］", "source": "保存HTMLの見出し", "rule": "公式サイトの見出しに明記"})
        elif tag == "オープン":
            rec["eligibility_note"] = "見出しに［オープン］とありますが、参加資格は要項（PDF）での確認が必要です（このPDFは未確認）。"
        elif tag == "豊川市民":
            rec["eligibility_note"] = "見出しに［豊川市民］とあり、市民を対象とする大会の可能性があります（参加資格は要項PDFで確認が必要。このPDFは未確認）。"
        if y is None:
            rec["warnings"].append("会場・参加資格・申込締切・参加費は一覧に無く未取得（要項PDFは未確認）")
        else:
            rec["parse_status"] = "success"
            ev_lines = _clean_label_block(y["event_types_text"])
            rec["venue"] = _clean_label_block(y["venue_block"])[0]
            rec["event_types"] = ["ダブルス", "ミックス"]
            rec["classification_basis"] = [b for b in rec["classification_basis"] if b["field"] != "event_type"] + [
                {"field": "event_type", "value": "ダブルス", "matched": "男子，女子", "source": "要項PDFの種目欄", "rule": "ダブルス大会の男子・女子の種目（大会名にもダブルスと明記）"},
                {"field": "event_type", "value": "ミックス", "matched": "ミックス", "source": "要項PDFの種目欄", "rule": "「ミックス」の種目"}]
            rec["classification_basis"] = [b for b in rec["classification_basis"] if not (b["field"] == "eligibility" and b["value"] in ("参加資格要確認",))]
            elig_lines = _clean_label_block(y["eligibility_block"])
            elig_text = " ／ ".join(elig_lines)
            first = elig_lines[0]
            if first.startswith("男子50歳以上"):
                rec["eligibility_status"] = "年齢条件あり"
                rec["eligibility_note"] = "要項の資格欄に明記（ペアの両名とも）。見出しの［オープン］は、参加費に非会員の区分があることと合わせて、協会員以外も参加できることを示しますが、年齢条件を満たす必要があります。"
            else:
                rec["eligibility_status"] = "その他の条件あり"
                rec["eligibility_note"] = "要項の資格欄に明記（レベル条件）。［オープン］の表記があっても、誰でも無条件に参加できるわけではありません。"
            rec["eligibility_text"] = elig_text
            rec["classification_basis"].append({"field": "eligibility", "value": rec["eligibility_status"], "matched": first, "source": "要項PDFの資格欄", "rule": "要項に明記（全角英数字・記号は半角に正規化して保持）"})
            ep = y["entry_period"]
            if not ep or ep["end_weekday_check"] != "ok" or ep["start_weekday_check"] != "ok":
                raise BuildError("申込期間を解釈できません: " + str(y["title"]))
            rec["deadline_text"] = re.sub(r"^.*~\s*", "", ep["raw"]).strip()
            rec["deadline_date"], rec["deadline_time"] = ep["end"], ep["end_time"]
            fee = _clean_label_block(y["fee_block"])
            rec["fee_text"] = " ／ ".join(fee)
            to = _clean_label_block(y["entry_to_block"])
            how = [x.lstrip("※") for x in to if x.startswith("※")]
            addr = " ".join(x for x in to if not x.startswith("※")).replace("☎", "TEL ")
            rec["entry_text"] = "申込先: %s／申込方法: %s／申込期間: %s年%s" % (addr, "。".join(how), ep["start"][:4], ep["raw"].replace("~", "〜").replace(" まで", "まで"))
            rec["notes"].append("要項PDFの確認日: %s（提供された要項PDFの文字情報を読み取り。大会名・開催日が保存HTMLの見出しと一致）" % PDF_CONFIRMED_ON)
            rec["notes"].append("申込期間: %s〜%s %s（年は要項の開催日の年。曜日で検算一致）" % (ep["start"], ep["end"], ep["end_time"]))
            rec["year_basis"] += "／申込期間・申込締切: " + ep["year_basis"]
            if y["main_date"]["normalized"] != periods[0]["normalized"] or (y["reserve_date"] or {}).get("normalized") != (e["reserve"] or {}).get("normalized"):
                raise BuildError("要項PDFの開催日・予備日が保存HTMLと一致しません: " + str(y["title"]))
            rec["warnings"] = [w for w in rec["warnings"] if "要項PDFは未確認" not in w]
        recs.append(rec)
    fys = []
    for r in recs:
        n = r["periods"][0].get("normalized") if r["periods"] else None
        if not n:
            raise BuildError("豊川: 開催日（年）を取得できない大会があります（%s）。年の推測は行いません" % r["title"])
        fys.append(stable_ids.fiscal_year_of(n))
    stable_ids.assign_ids("toyokawa", recs, fys)
    raw = {"top_page": top, "youkou": youkou_all}
    return recs, raw


def url_key(url):
    """要項PDFのURLから、入力名に使うキー（取得計画と共通）。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _deadline_text(dl):
    t = "%d月%d日(%s)" % (dl["month"], dl["day"], dl["weekday"])
    if dl.get("time"):
        t += " " + dl["time"]
    return t + (dl.get("suffix") or "")


def _refine_doubles_and_mixed(rec):
    """大会名に「混合ダブルス」と、それとは別の「ダブルス」の記載が両方ある大会（例: 一般ダブルスの部・混合ダブルスの部）は、種目をダブルス＋ミックスにする。"""
    title = rec["title"] or ""
    stripped = re.sub(r"(?:ミックス|混合)(?:ダブルス)?", "", title)
    m = re.search(r"ダブルス", stripped)
    if "ミックス" in (rec["event_types"] or []) and m and "ダブルス" not in rec["event_types"]:
        rec["event_types"] = sorted(rec["event_types"] + ["ダブルス"], key=lambda t: tc.EVENT_TYPES.index(t))
        rec["classification_basis"].append({"field": "event_type", "value": "ダブルス", "matched": m.group(0), "source": "タイトル",
                                            "rule": "「ダブルス」の語（混合ダブルスとは別に、ダブルスの部の記載がある）"})


PREVIOUS_RECORDS = []      # 前回の同じ情報源の記録（失敗時の保持＝staleの判定に使う）。run_update が、取得の前に設定する

_PDF_STATE_JP = {"parsed": "取得成功・解析成功", "partial_details": "取得成功・費用と種目別締切を補足", "fetched_unparsed": "取得成功・解析未取得", "fetch_failed": "取得失敗", "not_attempted": "今回は取得していない", "none": "要項PDFのリンクなし",
                 "reference_only": "取得成功・この大会の項目には使わない（同じ公式フォルダの要項PDF）"}
_PDF_FIELDS = ("deadline_text", "deadline_date", "deadline_time", "eligibility_status", "eligibility_text", "eligibility_note", "fee_text", "entry_text")
_PDF_NOTE_PREFIXES = ("申込締切 ", "要項PDFの会場の表記", "要項PDFの確認日")


def _prev_manual_origin(prev):
    acq = prev.get("acquisition") or {}
    if (acq.get("stale") or {}).get("values_origin"):
        return acq["stale"]["values_origin"], acq["stale"].get("since")
    if acq.get("method") == "manual":
        return "手動取得（確認日時 %s）" % acq.get("confirmed_at", "不明"), None
    return "自動取得（取得日 %s）" % (prev.get("source_snapshot_date") or "不明"), None


def _find_previous(ev):
    """同一大会・同一公式URL（要項PDF）の前回の記録。名前が似ているだけでは対応づけない。"""
    n = lambda x: re.sub(r"\s+", "", x or "")
    for p in PREVIOUS_RECORDS:
        if p.get("source_id") == hm.SOURCE_ID and p.get("guideline_url") and p.get("guideline_url") == ev["guideline_url"] and n(p.get("title")) == n(ev["title"]):
            return p
    return None


def _hold_previous_pdf_values(rec, prev, reason):
    """今回、要項PDFを取得・解析できなかったとき、前回成功した詳細項目を保持して、stale と記録する（同一大会・同一公式URLの場合だけ）。"""
    if not (prev.get("deadline_text") or prev.get("eligibility_text") or prev.get("fee_text")):
        return False
    matched = [b["matched"] for b in prev.get("classification_basis", []) if b.get("field") == "audience" and b.get("source") == "要項PDFの種目欄" and b.get("matched")]
    if matched:       # 対象者は、前回の根拠（要項PDFの種目欄の語）から、同じ規則で再分類する
        rec["classification_basis"] = [b for b in rec["classification_basis"] if not (b["field"] == "audience")]
        apply_common_classification(rec, extra_audience_texts=[("要項PDFの種目欄", " ／ ".join(matched))])
        _refine_doubles_and_mixed(rec)
    for k in _PDF_FIELDS:          # 再分類（対象者）の後に、前回の詳細項目を戻す（再分類が参加資格を上書きしないように）
        rec[k] = prev.get(k)
    rec["classification_basis"] = [b for b in rec["classification_basis"] if not (b["field"] == "eligibility")]
    rec["classification_basis"] += [b for b in prev.get("classification_basis", []) if b.get("field") == "eligibility"]
    rec["notes"] += [n for n in prev.get("notes", []) if n.startswith(_PDF_NOTE_PREFIXES)]
    origin, since = _prev_manual_origin(prev)
    since = since or SNAPSHOT_DATE["hamamatsu"]
    rec["parse_status"] = "partial"
    rec["warnings"] = [w for w in rec["warnings"] if "要項PDFから取得できていません" not in w]
    rec["warnings"].append("申込締切・参加資格・参加費・申込先は、今回は要項PDFを取得・解析できなかったため、前回成功した値を保持しています（最新ではない可能性があります。公式の要項で確認してください）")
    rec["notes"].insert(1, "前回成功値を保持（stale。%s以降）: %s。保持している値の出所: %s" % (since, reason, origin))
    rec["_stale"] = {"since": since, "reason": reason, "values_origin": origin, "fields": ["申込締切", "参加資格", "参加費", "申込先"]}
    return True


def build_hamamatsu():
    raw = read(HAMAMATSU_PAGE)
    page = hm.parse_tournament_page(raw)
    if page["fatal_error"]:
        raise BuildError("浜松パーサー失敗: " + page["fatal_error"])
    if not page["events"]:
        raise BuildError("浜松: 大会が1件も見つかりません（ページの構造が変わった可能性）")
    pdfs, fetchstat = {}, {}
    for name in provider_names("hamamatsu_", "_youkou.txt"):
        t = read(name, optional=True)
        if t is not None:
            pdfs[name] = hm.parse_youkou_text(t.decode("utf-8"))
    for name in provider_names("hamamatsu_", "_fetch.json"):
        t = read(name, optional=True)
        if t is not None:
            d = json.loads(t.decode("utf-8"))
            fetchstat[name] = d
    recs, fys, used = [], [], set()
    pdf_states = {}
    for ev in page["events"]:
        if ev["folder_year"] is None:
            RUN_WARNINGS.append("浜松: 公式PDFのフォルダ年を特定できない大会は取り込みません（年の推測は行いません）: %s" % ev["title"])
            continue
        warnings, notes = [], []
        for p in ev["periods"]:
            for w in p["warnings"]:
                notes.append("開催予定日の表記（原文: %s）: %s" % (p["raw"], w))
        if ev["origin"] == "predraw_only":
            warnings.append("この大会は「大会要項」の表に無く、「仮ドロー」の表だけに掲載されています。開催日・会場は仮ドローの表の記載です")
            notes.append("要項は掲載されていません。紐づけたPDFは、同じ年・同じ公式フォルダにある要項PDFです（この大会の要項かは、PDFで確認してください）" if ev["guideline_url"] else "要項PDF・申込用紙のURLは取得できていません")
        rec = make_record(
            id="h-?", title=ev["title"], source_id=hm.SOURCE_ID, source_name=hm.SOURCE_NAME, source_area="浜松", event_area="浜松",
            date_text=notnull(ev["date_text"]), periods=[dict(p) for p in ev["periods"]],
            year_basis="開催予定日の表記に西暦年が明記されている（曜日で検算）。大会IDに使う年は、公式PDFのフォルダ年（%d）" % ev["folder_year"],
            venue=notnull(ev["venue"]), official_url=hm.PAGE_URL, guideline_url=ev["guideline_url"], entry_url=ev["entry_url"],
            parse_status="partial", warnings=warnings, notes=notes, source_snapshot_date=SNAPSHOT_DATE["hamamatsu"],
        )
        rec["fee_text"] = None
        rec["related_links"] = [{"label": "仮ドロー", "url": u} for u in ev["predraw_urls"]]
        key = url_key(ev["guideline_url"]) if ev["guideline_url"] else None
        y = pdfs.get("hamamatsu_%s_youkou.txt" % key) if key else None
        fs = fetchstat.get("hamamatsu_%s_fetch.json" % key) if key else None
        layout_raw = read("hamamatsu_%s_layout.txt" % key, optional=True) if key else None
        table_detail = ht.parse(layout_raw.decode("utf-8"), ev) if layout_raw is not None else None
        if y is not None and ev["origin"] == "guidelines":
            used.add("hamamatsu_%s_youkou.txt" % key)
        row_dates = {p["normalized"] for p in ev["periods"] if p["normalized"]}
        use_pdf = False
        if y is not None and ev["origin"] == "guidelines":
            if not y["ok"]:
                RUN_WARNINGS.append("浜松: 要項PDFを読み取れません（%s）: %s" % ("・".join(y["problems"]), ev["title"]))
            elif not ({d["normalized"] for d in y["dates"]} & row_dates):
                RUN_WARNINGS.append("浜松: 要項PDFの開催日が表の開催予定日と一致しないため使用しません: %s" % ev["title"])
            else:
                use_pdf = True
        # ---- 要項PDFの状態 ----
        if not key:
            pdf_state, detail = "none", None
        elif use_pdf:
            pdf_state, detail = "parsed", None
        elif table_detail:
            pdf_state, detail = "partial_details", None
        elif fs and fs.get("state") == "fetch_failed":
            pdf_state, detail = "fetch_failed", "%s: %s" % (fs.get("stage"), fs.get("message"))
        elif ev["origin"] == "predraw_only" and fs and fs.get("state") == "fetched" and y is not None and y["ok"]:
            pdf_state, detail = "reference_only", None
        elif (fs and fs.get("state") in ("fetched", "text_failed")) or y is not None:
            pdf_state, detail = "fetched_unparsed", (fs or {}).get("message")
        else:
            pdf_state, detail = "not_attempted", None
        if key:
            pdf_states[ev["guideline_url"]] = pdf_state if ev["origin"] == "guidelines" or ev["guideline_url"] not in pdf_states else pdf_states[ev["guideline_url"]]
        extra_aud = [("要項PDFの種目欄", " ／ ".join(y["events_text"]))] if use_pdf and y["events_text"] else None
        apply_common_classification(rec, extra_audience_texts=extra_aud)
        _refine_doubles_and_mixed(rec)
        held = False
        if use_pdf:
            rec["parse_status"] = "success"
            el = hm.classify_youkou_eligibility(y["eligibility_lines"])
            rec["eligibility_status"], rec["eligibility_text"], rec["eligibility_note"] = el["status"], " ／ ".join(y["eligibility_lines"]), el["note"]
            rec["classification_basis"] = [b for b in rec["classification_basis"] if not (b["field"] == "eligibility" and b["value"] == "参加資格要確認")]
            if el["status"] != "参加資格要確認":
                rec["classification_basis"].append({"field": "eligibility", "value": el["status"], "matched": el["matched"], "source": "要項PDFの資格欄", "rule": "要項に明記（全角英数字・記号は半角に正規化して保持）"})
            else:
                rec["classification_basis"].append({"field": "eligibility", "value": "参加資格要確認", "matched": None, "source": "要項PDFの資格欄", "rule": "資格欄の内容を分類できないため確定しない（原文を表示）"})
            dl = y["deadline"]
            rec["deadline_text"] = _deadline_text(dl)
            rec["deadline_time"] = dl.get("time")
            main = y["dates"][0]["normalized"]
            iso = hm.resolve_deadline_year(main, dl["month"], dl["day"], dl["weekday"])
            if iso:
                rec["deadline_date"] = iso
                rec["notes"].append("申込締切 %s %s（年は要項の開催日の年から決定。曜日で検算一致）" % (iso, dl.get("time") or "時刻の記載なし"))
            else:
                rec["warnings"].append("申込締切の年を決められません（要項に年の記載がなく、曜日の検算で1つに決まらない）。原文: %s" % rec["deadline_text"])
            rec["fee_text"] = " ／ ".join(y["fee_lines"])
            addr = " ／ ".join(y["entry_to_lines"])
            rec["entry_text"] = "申込先: %s（申込用紙に記入し、参加料を添えて申込）" % addr
            if y["venue"] and y["venue"] not in (rec["venue"] or ""):
                rec["notes"].append("要項PDFの会場の表記: %s（表の開催場所: %s）" % (y["venue"], rec["venue"] or "未取得"))
            rec["notes"].append("要項PDFの確認日: %s（要項PDFの文字情報を読み取り。大会の開催日が表の開催予定日と一致）" % (PDF_CONFIRMED_ON or "不明"))
        else:
            if ev["origin"] == "guidelines" and pdf_state in ("fetch_failed", "fetched_unparsed"):
                prev = _find_previous(ev)
                if prev:
                    reason = ("要項PDFを取得できなかった（%s）" % detail) if pdf_state == "fetch_failed" else "要項PDFを取得できたが、読み取れなかった（PDFが変更された可能性）"
                    held = _hold_previous_pdf_values(rec, prev, reason)
            if not held:
                rec["warnings"].append("申込締切・参加資格・参加費は、要項PDFから取得できていません（未取得・要確認）")
        if table_detail and not use_pdf:
            rec["fee_text"] = table_detail["fee_text"]
            rec["entry_text"] = table_detail["entry_text"]
            rec["notes"].extend(table_detail["deadline_notes"])
            rec["notes"].append("要項PDFの確認日: %s（表の列配置を保持して読み取り。参加資格の分類と原文の日付は変更していません）" % (PDF_CONFIRMED_ON or "不明"))
            rec["warnings"] = [w for w in rec["warnings"] if "申込締切・参加資格・参加費は、要項PDFから取得できていません" not in w]
            rec["warnings"].append("参加資格の詳細と、複数種目に共通する単一の申込締切は未確定です。種目別の締切と公式要項を確認してください")
        # ---- 取得の記録（自動取得）。手動取得の記録は、実取得の全工程が成功したデータでは、この記録に置き換わる ----
        urls = [hm.PAGE_URL] + [u for u in [ev["guideline_url"], ev["entry_url"]] + ev["predraw_urls"] if u]
        acq = {"method": "http", "auto_fetch": True, "tool": "通常のHTTP取得（pipeline）", "official_urls": urls, "pdf_state": pdf_state, "pdf_state_jp": _PDF_STATE_JP[pdf_state],
            "pdf_extractor": (fs or {}).get("layout_extractor") if table_detail else (fs or {}).get("extractor")}
        st = rec.pop("_stale", None)
        if st:
            acq["stale"] = st
        rec["acquisition"] = acq
        rec["notes"].insert(0, "自動取得（通常のHTTP取得）: このツールが公式ページ（%s）を取得して解析しました（取得日: %s）。要項PDF: %s%s" % (
            hm.PAGE_URL, SNAPSHOT_DATE["hamamatsu"], _PDF_STATE_JP[pdf_state], ("（%s）" % detail) if detail and pdf_state == "fetch_failed" else ""))
        recs.append(rec)
        fys.append(ev["folder_year"])
    if not recs:
        raise BuildError("浜松: 取り込める大会がありません")
    stable_ids.assign_ids("hamamatsu", recs, fys)
    summ = {"pdf_total": len(pdf_states), "pdf_parsed": sum(1 for v in pdf_states.values() if v == "parsed"), "pdf_partial_details": sum(1 for v in pdf_states.values() if v == "partial_details"), "pdf_unparsed": sum(1 for v in pdf_states.values() if v in ("fetched_unparsed", "reference_only")),
            "pdf_fetch_failed": sum(1 for v in pdf_states.values() if v == "fetch_failed"), "pdf_not_attempted": sum(1 for v in pdf_states.values() if v == "not_attempted"),
            "stale_records": sum(1 for r in recs if (r.get("acquisition") or {}).get("stale")), "fetch_status_present": bool(fetchstat)}
    return recs, {"page": {k: v for k, v in page.items() if k not in ("guidelines", "predraw")}, "youkou": {k: {"ok": v["ok"], "problems": v["problems"]} for k, v in pdfs.items()}, "pdf_summary": summ}


def build_okazaki():
    raw = read(OKAZAKI_PAGE)
    page = ok.parse_page(raw, page_url=ok.PAGE_URL)
    if page["fatal_error"]:
        raise BuildError("岡崎パーサー失敗: " + page["fatal_error"])
    if len(page["events"]) < 5:
        raise BuildError("岡崎: 大会カードが5件未満です（ページ構造変更や取得欠落の可能性）")
    recs, fiscal_years = [], []
    for ev in page["events"]:
        periods = ev["periods"]
        first_date = next((p["normalized"] for p in periods if p.get("normalized")), None)
        if not first_date:
            raise BuildError("岡崎: 開催日を正規化できません: " + ev["title"])
        event_year = int(first_date[:4])
        deadline_text = ev.get("deadline_text")
        deadline_date = None
        deadline_time = None
        warnings = []
        if deadline_text:
            m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", _nfkc(deadline_text))
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                deadline_year = event_year - 1 if month > int(first_date[5:7]) else event_year
                try:
                    d = datetime.date(deadline_year, month, day)
                    if d < datetime.date.fromisoformat(first_date):
                        deadline_date = d.isoformat()
                    else:
                        warnings.append("申込締切の年・日付を開催日より前と確認できないため、原文のみ保持")
                except ValueError:
                    warnings.append("申込締切の日付が不正のため原文のみ保持")
            else:
                warnings.append("申込締切を解釈できないため原文のみ保持")
        rec = make_record(
            id="okazaki-?", title=ev["title"], source_id=ok.SOURCE_ID, source_name=ok.SOURCE_NAME,
            source_area="岡崎", event_area="岡崎", date_text=ev["date_text"], periods=periods,
            year_basis="大会ページURLの令和%s年度表記（4月始まり。1〜3月は年度の翌年）" % (page["fiscal_year"] - 2018),
            deadline_text=deadline_text, deadline_date=deadline_date, deadline_time=deadline_time,
            venue=notnull(ev["venue"]), official_url=ok.PAGE_URL, guideline_url=ev.get("guideline_url"),
            parse_status="partial", warnings=warnings, source_snapshot_date=SNAPSHOT_DATE["okazaki"],
            notes=["公式大会一覧のカードから取得。要項PDF本文・申込先ページは未解析です"],
        )
        event_text = [("公式大会一覧の種目欄", ev["events_text"])]
        apply_common_classification(rec, extra_audience_texts=[("公式大会一覧の参加資格欄", ev["eligibility"])],
                                    extra_event_texts=event_text, table_eligibility=ev["eligibility"])
        # 共通分類器の説明文には「在学」が含まれるが、岡崎の一覧に
        # 在学条件が書かれていない大会へ、その条件を付け足さない。
        if "在学" not in ev["eligibility"] and "在学" in str(rec.get("eligibility_note") or ""):
            rec["eligibility_note"] = "参加資格は一覧表の原文「%s」です。原文にない条件は加えず、詳細は公式要項で確認してください。" % ev["eligibility"]
            rec["classification_basis"] = [
                dict(item, matched=ev["eligibility"],
                     rule="一覧表の参加資格欄の原文に基づく。原文にない対象条件は加えない")
                if item.get("field") in ("audience", "eligibility")
                   and item.get("source") == "一覧表の参加資格欄"
                   and "在住在勤在学" in str(item.get("matched") or "")
                else item
                for item in rec.get("classification_basis", [])
            ]
        # 岡崎の一覧には「加盟員のみ」と明記される大会がある。
        # 共通分類器を他地域のデータに波及させず、岡崎の公式原文だけを登録条件として扱う。
        if _nfkc(ev["eligibility"]).replace(" ", "") == "加盟員のみ":
            rec["eligibility_status"] = "協会登録必要"
            rec["eligibility_text"] = ev["eligibility"]
            rec["eligibility_note"] = "岡崎市テニス協会の加盟員に限ると一覧に明記されています。登録条件の詳細は公式要項で確認してください。"
            rec["classification_basis"] = [b for b in rec.get("classification_basis", []) if b.get("field") != "eligibility"]
            rec["classification_basis"].append({"field": "eligibility", "value": "協会登録必要", "matched": "加盟員のみ", "source": "一覧表の参加資格欄", "rule": "岡崎公式一覧に加盟員のみと明記"})
        # 一覧の分類は維持し、タイトル・開催日・曜日が一致したPDFだけを補足する。
        guideline = ev.get("guideline_url")
        text = read("okazaki_%s_youkou.txt" % url_key(guideline), optional=True) if guideline else None
        detail = op.parse(text.decode("utf-8"), ev) if text is not None else None
        status_raw = read("okazaki_%s_fetch.json" % url_key(guideline), optional=True) if guideline else None
        status = json.loads(status_raw.decode("utf-8")) if status_raw else {}
        if detail and detail["ok"]:
            main = next((d for d in detail["deadlines"] if d["mode"] == "インターネット"),
                        next((d for d in detail["deadlines"] if d["mode"] == "共通"), None))
            if main and (not rec["deadline_date"] or main["date"] == rec["deadline_date"]):
                rec["deadline_date"] = main["date"]
                rec["deadline_text"] = main["text"]
            elif main:
                rec["warnings"].append("一覧と要項の申込締切が異なるため、一覧の締切を維持しています。要項で確認してください")
            rec["fee_text"] = " ／ ".join(detail["fee_lines"])
            rec["notes"] = ["公式大会一覧から取得。要項PDFの参加費・方法別締切を確認しました。申込先ページは未解析です"]
            rec["notes"].extend("要項PDFの%s申込締切: %s" % (d["mode"], d["text"]) for d in detail["deadlines"])
            rec["notes"].append("要項PDFの確認日: %s（大会名・開催日・曜日を一覧と照合）" % (PDF_CONFIRMED_ON or "不明"))
        elif detail:
            rec["warnings"].append("要項PDFの詳細は未採用: " + "・".join(detail["problems"]))
        rec["acquisition"] = {"method": "http", "auto_fetch": True,
                              "pdf_state": "parsed_details" if detail and detail["ok"] else status.get("state", "not_attempted"),
                              "pdf_extractor": status.get("extractor")}
        recs.append(rec)
        fiscal_years.append(page["fiscal_year"])
    stable_ids.assign_ids("okazaki", recs, fiscal_years)
    return recs, {"page_url": ok.PAGE_URL, "fiscal_year": page["fiscal_year"], "event_count": len(recs),
                  "pdf_details_parsed": sum(1 for r in recs if r.get("acquisition", {}).get("pdf_state") == "parsed_details")}



SOURCE_ORDER = ["aichi", "toyohashi", "gamagori", "toyokawa", "hamamatsu", "okazaki"]


def source_meta(key, sid, name, area, event_area, coverage, label, note, records, files, parser):
    return {
        "id": sid, "name": name, "area": area, "event_area": event_area,
        "coverage": coverage, "coverage_label": label, "coverage_note": note,
        "snapshot_date": SNAPSHOT_DATE[key],
        "snapshot_note": "保存HTMLの受領日（ページ内に更新日の記載なし）",
        "record_count": len(records),
        "files": [{"file": f, "sha256": h} for f, h in files],
        "parser": parser,
    }


def _heading_year(raw):
    m = re.search(r"令和\s*(\d+|元)\s*年度", str(raw.get("heading") or ""))
    if not m:
        raise BuildError("蒲郡: 見出しから令和の年度を取得できません")
    return "令和%s年度" % m.group(1)


SOURCE_DEFS = {
    "aichi": dict(builder=build_aichi, id=at.SOURCE_ID, name="愛知県協会", area="愛知県", event_area="愛知県協会掲載", coverage="supported", label="対応済み",
                  note=lambda recs, raw: "%d年度競技日程の表（%d件）。要項PDFの本文は解析していません" % (recs[0]["fiscal_year"], len(recs)), parser="TTA-MOBILE-006-R1（承認済み・無改変）"),
    "toyohashi": dict(builder=build_toyohashi, id=tt.SOURCE_ID, name="豊橋", area="豊橋", event_area="豊橋", coverage="partial", label="部分対応",
                      note=lambda recs, raw: "大会要項の一覧（全%sページのうち%dページ・%d件）。開催日・会場・申込締切は一覧に無く未取得（日付未取得として表示）。要項PDFの本文は解析していません" % (
                          ((raw.get("listing_page1") or {}).get("pagination") or {}).get("total_pages") or 7, sum(1 for k in raw if k.startswith("listing_page")), len(recs)),
                      parser="TTA-MOBILE-007（承認済み・無改変）"),
    "gamagori": dict(builder=build_gamagori, id=gt.SOURCE_ID, name="蒲郡", area="蒲郡", event_area="蒲郡", coverage="supported", label="対応済み",
                     note=lambda recs, raw: "%sの大会一覧表（%d件。開催日・参加資格・種目・会場を取得）。要項PDFの本文は解析していません" % (_heading_year(raw), len(recs)), parser="TTA-MOBILE-012（新規）"),
    "toyokawa": dict(builder=build_toyokawa, id=tk.SOURCE_ID, name="豊川", area="豊川", event_area="豊川", coverage="supported", label="対応済み",
                     note=lambda recs, raw: "%d年度の大会%d件（トップページの一覧）。要項PDFの本文は、日程が先の%d件だけ確認（他の%d件は未確認）。"
                          "「対応済み」は取り込み済みという意味で、公式サイトの最新状態を常時取得しているわけではありません" % (
                              recs[0]["fiscal_year"], len(recs),
                              sum(1 for r in recs if r["parse_status"] == "success"), sum(1 for r in recs if r["parse_status"] != "success")),
                     parser="TTA-MOBILE-013-A1（新規）"),
    "hamamatsu": dict(builder=build_hamamatsu, id=hm.SOURCE_ID, name="浜松", area="浜松", event_area="浜松", coverage="partial", label="部分対応",
                      note=lambda recs, raw: "「大会情報」ページの大会要項・仮ドローの表（%d件）。要項PDFの読み取りができた大会は、申込締切・参加資格・参加費を取得（%d件）。"
                      "年間の事業予定（画像・PDF）・ジュニア大会・終了済みの大会結果（表%d件）は取り込んでいません" % (
                          len(recs), sum(1 for r in recs if r["parse_status"] == "success"), (raw.get("page") or {}).get("results_count") or 0),
                      parser="TTA-MOBILE-021（新規）"),
    "okazaki": dict(builder=build_okazaki, id=ok.SOURCE_ID, name="岡崎", area="岡崎", event_area="岡崎", coverage="partial", label="部分対応",
                     note=lambda recs, raw: "令和%s年度の大会一覧カード（%d件）。開催日・種目・参加資格・会場・一覧記載の締切を取得。要項PDFの参加費・方法別締切を確認した大会は%d件。申込先ページは未解析" % (raw["fiscal_year"] - 2018, len(recs), raw.get("pdf_details_parsed", 0)),
                     parser="岡崎大会カード・要項の費用／方法別締切 parser v0.2"),
}


def build_source(key):
    """情報源1つ分を生成する。戻り値: records / raw（パーサーの生の出力）/ meta / warnings。失敗時は BuildError。"""
    READ_LOG.clear()
    del RUN_WARNINGS[:]
    d = SOURCE_DEFS[key]
    recs, raw = d["builder"]()
    for r in recs:
        tc.finalize_record(r)      # 検索に使う分類の仕上げ（主種目・構成種目・対象者「一般」の確定）。再分類ツールと同じ関数
    ids = [t["id"] for t in recs]
    if len(ids) != len(set(ids)):
        raise BuildError("%s: idが重複しています" % key)
    meta = source_meta(key, d["id"], d["name"], d["area"], d["event_area"], d["coverage"], d["label"], d["note"](recs, raw), recs, input_files(key), d["parser"])
    if key == "hamamatsu":
        ps = (raw or {}).get("pdf_summary") or {}
        meta["pdf_confirmed_on"] = PDF_CONFIRMED_ON
        full = ps.get("pdf_fetch_failed", 0) == 0 and ps.get("stale_records", 0) == 0
        meta["acquisition"] = "automatic_http" if full else "automatic_http_partial"
        meta["snapshot_note"] = ("公式ページ・要項PDFを、通常のHTTP取得で自動取得（取得日: %s。ページ内に更新日の記載なし）。要項PDF %d件: 取得・解析成功 %d件／費用・種目別締切を補足 %d件／取得成功・解析未取得 %d件／取得失敗 %d件。前回成功値を保持している大会: %d件" % (
            SNAPSHOT_DATE["hamamatsu"], ps.get("pdf_total", 0), ps.get("pdf_parsed", 0), ps.get("pdf_partial_details", 0), ps.get("pdf_unparsed", 0), ps.get("pdf_fetch_failed", 0), ps.get("stale_records", 0)))
    if key == "toyokawa":
        meta["pdf_confirmed_on"] = PDF_CONFIRMED_ON
        meta["snapshot_note"] = "保存HTMLの受領日（要項PDFの確認日: %s）" % PDF_CONFIRMED_ON
    return {"key": key, "records": recs, "raw": raw, "meta": meta, "warnings": list(RUN_WARNINGS)}


OTHER_AREAS = {"label": "その他エリア", "coverage": "unsupported", "coverage_label": "未対応",
               "coverage_note": "この版では、上記以外の地域の大会情報は取得していません（検索結果0件は「大会がない」ではなく「未対応」の可能性があります）"}


def assemble(parts):
    """parts: [{records, meta}]（SOURCE_ORDER の順）から、共通JSONを組み立てる。"""
    tournaments = [t for p in parts for t in p["records"]]
    ids = [t["id"] for t in tournaments]
    if len(ids) != len(set(ids)):
        raise BuildError("全体でidが重複しています")
    sources = [p["meta"] for p in parts]
    statuses = {}
    for t in tournaments:
        statuses[t["parse_status"]] = statuses.get(t["parse_status"], 0) + 1
    return {
        "schema_version": 3,
        "sources": sources,
        "other_areas": dict(OTHER_AREAS),
        "summary": {"total": len(tournaments), "by_source": {m["name"]: m["record_count"] for m in sources}, "by_parse_status": statuses},
        "tournaments": tournaments,
    }


def build(keys=None):
    outs = [build_source(k) for k in (keys or SOURCE_ORDER)]
    data = assemble(outs)
    raw_results = {o["key"]: o["raw"] for o in outs}
    return data, raw_results


def dumps(o):
    return json.dumps(o, ensure_ascii=False, indent=1) + "\n"


def load_fixtures(fixtures_dir):
    """合成フィクスチャ（manifest.json に記録された論理名・ファイル・SHA-256）を読み込む。戻り値: (provider, manifest)。"""
    with open(os.path.join(fixtures_dir, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    provider = {}
    for logical, meta in man["files"].items():
        with open(os.path.join(fixtures_dir, meta["file"]), "rb") as f:
            raw = f.read()
        if sha(raw) != meta["sha256"]:
            raise BuildError("フィクスチャのSHA-256が一致しません: " + meta["file"])
        provider[logical] = raw
    return provider, man


def build_from_fixtures(fixtures_dir):
    global PROVIDER
    provider, man = load_fixtures(fixtures_dir)
    PROVIDER = provider
    try:
        configure(snapshot_dates=man["snapshot_dates"], pdf_confirmed_on=man["pdf_confirmed_on"], retrieved_at=man["retrieved_at"])
        data, _raw = build()
    finally:
        PROVIDER = None
    return data


def main():
    argv = sys.argv[1:]
    if "--fixtures" not in argv:
        sys.exit("使い方: build_tournaments_json.py --fixtures <合成フィクスチャのフォルダ> [--out PATH] [--check]")
    fx = os.path.abspath(argv[argv.index("--fixtures") + 1])
    out = os.path.abspath(argv[argv.index("--out") + 1]) if "--out" in argv else os.path.join(fx, "expected_tournaments.json")
    try:
        text = dumps(build_from_fixtures(fx))
    except BuildError as e:
        print("[NG] 生成に失敗しました:", e)
        sys.exit(1)
    if "--check" in argv:
        if not os.path.exists(out) or open(out, encoding="utf-8").read() != text:
            print("[NG] 再生成結果と一致しません:", os.path.relpath(out, ROOT))
            sys.exit(1)
        print("[OK] %s は、合成フィクスチャ＋パーサー＋分類ルールの再生成結果と完全一致" % os.path.basename(out))
        return
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("生成しました: %s" % os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
