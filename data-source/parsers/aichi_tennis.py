"""
TTA-MOBILE-006 愛知県テニス協会 専用パーサー（試作・独立モジュール）

対象：保存HTML中の「2026年度愛知県テニス協会競技日程」表のみ。
対象外：SCHOOL TOUR / 高校関連 / 中学関連 / 愛知ジュニアテニス実行委員会関連 / その他
        / 後援・公認・承認大会日程セクション全体 / PDF本文 / 過年度ページ

既存の app_auto_test.py・PC版UI・CSV・モバイル版・Supabaseには一切接続しません。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any

from bs4 import BeautifulSoup, Tag

from .common import (
    CharsetDetectionResult,
    detect_charset,
    strict_decode,
    absolutize_url,
    expand_table_with_rowspan,
    cell_text,
    cell_lines,
    now_iso,
)

SOURCE_ID = "aichi_tennis_association"
SOURCE_NAME = "愛知県テニス協会"
DEFAULT_PAGE_URL = "https://www7b.biglobe.ne.jp/~aichi-tennis/tournament/index.html"

TARGET_TITLE_MARKER = "競技日程"          # 対象タイトルを一意に識別する部分文字列
EXCLUDE_TITLE_MARKER = "後援"            # 対象外セクションのタイトルに含まれる文字列
EXPECTED_HEADER_TEXTS = ["開催月日", "大会名", "会場"]  # 列ヘッダーの最低限の一致確認用
FISCAL_YEAR_RE = re.compile(r"(\d{4})\s*年度")

MONTH_KANJI_RE = re.compile(r"^\s*(\d{1,2})\s*月")
EXPLICIT_MONTH_DAY_RE = re.compile(r"(\d{1,2})/(\d{1,2})")
DAY_TOKEN_RE = re.compile(r"(\d{1,2})(?:\([^)]*\))?")


@dataclass
class DatePeriod:
    raw: str
    month: Optional[int] = None
    day_start: Optional[int] = None
    day_end: Optional[int] = None
    year: Optional[int] = None
    normalized: Optional[str] = None  # "YYYY-MM-DD" または "YYYY-MM-DD/YYYY-MM-DD"
    warnings: List[str] = field(default_factory=list)


@dataclass
class RelatedLink:
    label: str
    url: Optional[str]
    link_type: str  # guideline / guideline_external / draw / result / tournament_page / entry / application_form / withdrawal / other / unsafe_excluded


@dataclass
class TournamentRecord:
    source_id: str
    source_name: str
    source_page_url: str
    tournament_name: str
    event_date_text: str
    event_date_periods: List[Dict[str, Any]]
    venue_text: str
    venue_normalized: Optional[str]
    guideline_url: Optional[str]
    related_links: List[Dict[str, Any]]
    retrieved_at: str
    parse_status: str
    warnings: List[str]
    year_basis: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ParseResult:
    records: List[TournamentRecord]
    charset_detection: CharsetDetectionResult
    excluded_table_count: int
    target_table_found: bool
    overall_status: str  # success / partial / failed
    fatal_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "overall_status": self.overall_status,
            "fatal_error": self.fatal_error,
            "target_table_found": self.target_table_found,
            "excluded_table_count": self.excluded_table_count,
            "charset_detection": asdict(self.charset_detection),
            "records": [r.to_dict() for r in self.records],
        }
        return d


# ---------------------------------------------------------------------------
# 年度変換（2026年度の当年度メイン表のみに適用する、承認済みの規則）
# ---------------------------------------------------------------------------

def resolve_year_for_month(month: int, fiscal_year: Optional[int]) -> tuple[Optional[int], str]:
    """
    対象見出し（例：「2026年度愛知県テニス協会競技日程」）から抽出した年度(fiscal_year)に基づく
    年度変換。4〜12月はfiscal_year年、1〜3月はfiscal_year+1年とする。
    fiscal_yearが見出しから抽出できなかった場合、または月が不正な場合は (None, 理由) を返す
    （この場合は正規化を行わず、原文のみ保持する）。
    """
    if fiscal_year is None:
        return None, "見出しから年度を抽出できなかったため年度変換を適用できません"
    if month is None or not (1 <= month <= 12):
        return None, "月が不明なため年度変換を適用できません"
    if 4 <= month <= 12:
        return fiscal_year, f"{fiscal_year}年度見出しから導出（4〜12月は{fiscal_year}年）"
    return fiscal_year + 1, f"{fiscal_year}年度見出しから導出（1〜3月は{fiscal_year + 1}年）"


# ---------------------------------------------------------------------------
# 日付セルの解析
# ---------------------------------------------------------------------------

def parse_date_cell(month_context_text: str, day_lines: List[str], fiscal_year: Optional[int]) -> List[DatePeriod]:
    """
    month_context_text: 行（またはrowspan元）の「開催月日」左側セルの原文（例："4月"）
    day_lines: 「開催月日」右側セルを<br>で分割した行のリスト

    仕様:
      - "、"でさらに分割し、サブ区間ごとに判定する
      - "M/D"形式が現れたら、以降は明示的に上書きされるまでその月が引き継がれる
        （同一セル内、<br>をまたいでも引き継ぐ）
      - 「予」を含む区間は、原文のみ保持し正規化は行わない（意味を確定できないため）
      - 解釈できない区間は normalized=None のまま原文を保持する
    """
    periods: List[DatePeriod] = []

    m = MONTH_KANJI_RE.match(month_context_text or "")
    current_month = int(m.group(1)) if m else None
    month_source = "row month cell" if current_month else "unresolved"

    if not day_lines:
        return periods

    for line in day_lines:
        sub_segments = [s.strip() for s in line.split("、") if s.strip()]
        if not sub_segments:
            sub_segments = [line.strip()]

        for seg in sub_segments:
            if not seg:
                continue

            if "予" in seg:
                dp = DatePeriod(raw=seg, month=current_month)
                dp.warnings.append(
                    "「予」の表記を含むため、意味を確定せず正規化していません（原文のみ保持）"
                )
                # 明示的な月指定があれば、以降の区間のために月だけは引き継ぐ
                em = EXPLICIT_MONTH_DAY_RE.search(seg)
                if em:
                    current_month = int(em.group(1))
                    month_source = "explicit M/D in segment (from 予-marked segment)"
                periods.append(dp)
                continue

            em = EXPLICIT_MONTH_DAY_RE.match(seg)
            if em:
                current_month = int(em.group(1))
                month_source = "explicit M/D in segment"
                rest = seg[em.end():]
                day_matches = DAY_TOKEN_RE.findall(em.group(2) + rest) if False else None

            dp = DatePeriod(raw=seg, month=current_month)

            working = seg
            em2 = EXPLICIT_MONTH_DAY_RE.match(working)
            if em2:
                working = str(em2.group(2)) + working[em2.end():]

            range_match = re.match(
                r"^(\d{1,2})(?:\([^)]*\))?\s*[〜~\-ー]\s*(\d{1,2})(?:\([^)]*\))?$", working
            )
            single_match = re.match(r"^(\d{1,2})(?:\([^)]*\))?$", working)

            if current_month is None:
                dp.warnings.append("月が特定できないため正規化していません")
                periods.append(dp)
                continue

            if range_match:
                d1, d2 = int(range_match.group(1)), int(range_match.group(2))
                dp.day_start, dp.day_end = d1, d2
                year, basis = resolve_year_for_month(current_month, fiscal_year)
                dp.year = year
                if year is not None:
                    dp.normalized = (
                        f"{year:04d}-{current_month:02d}-{d1:02d}/"
                        f"{year:04d}-{current_month:02d}-{d2:02d}"
                    )
                else:
                    dp.warnings.append(basis)
            elif single_match:
                d1 = int(single_match.group(1))
                dp.day_start = d1
                dp.day_end = d1
                year, basis = resolve_year_for_month(current_month, fiscal_year)
                dp.year = year
                if year is not None:
                    dp.normalized = f"{year:04d}-{current_month:02d}-{d1:02d}"
                else:
                    dp.warnings.append(basis)
            else:
                dp.warnings.append("日付形式を解釈できないため原文のみ保持します")

            periods.append(dp)

    return periods


# ---------------------------------------------------------------------------
# リンク分類
# ---------------------------------------------------------------------------

def classify_link(label: str) -> str:
    label = label or ""
    if label == "要項":
        return "guideline"
    if "要項" in label:
        return "guideline_external"
    if "ドロー" in label:
        return "draw"
    if "結果" in label:
        return "result"
    if "大会ページ" in label:
        return "tournament_page"
    if "エントリー" in label:
        return "entry"
    if "申込書" in label or "申込" in label:
        return "application_form"
    if "欠場届" in label:
        return "withdrawal"
    return "other"


def extract_related_links(cell_tag: Tag, base_url: str) -> List[RelatedLink]:
    links: List[RelatedLink] = []
    if cell_tag is None:
        return links
    for a in cell_tag.find_all("a"):
        label = a.get_text(strip=True)
        href = a.get("href")
        abs_url = absolutize_url(base_url, href)
        if href and abs_url is None:
            # 危険/非対応スキームのため採用しない
            links.append(RelatedLink(label=label, url=None, link_type="unsafe_excluded"))
            continue
        link_type = classify_link(label)
        links.append(RelatedLink(label=label, url=abs_url, link_type=link_type))
    return links


# ---------------------------------------------------------------------------
# 対象テーブルの特定（DOM構造ベース。列名の一致だけで判定しない）
# ---------------------------------------------------------------------------

def find_target_table(soup: BeautifulSoup) -> tuple[Optional[Tag], int, Optional[int]]:
    """
    'title'クラスのdivのうち、テキストに「競技日程」を含み「後援」を含まないものを
    対象見出しとする。その見出しから、次の'title'divが現れるまでの範囲にある<table>の中で、
    最初の行が複数の<th>から成る列ヘッダー行であり、かつ単一セルのcolspan=5見出し行
    （対象外セクションの特徴）でないものを対象表とする。
    見出しテキストから「YYYY年度」の年度も抽出する（日付の年度変換に使用）。

    戻り値: (対象table or None, 対象見出し〜次見出しの間でスキップした対象外table数, fiscal_year or None)
    """
    title_divs = soup.find_all("div", class_="title")
    target_title = None
    fiscal_year: Optional[int] = None
    for div in title_divs:
        text = div.get_text(strip=True)
        if TARGET_TITLE_MARKER in text and EXCLUDE_TITLE_MARKER not in text:
            target_title = div
            ym = FISCAL_YEAR_RE.search(text)
            if ym:
                fiscal_year = int(ym.group(1))
            break

    if target_title is None:
        return None, 0, None

    # 次のtitle divまでの間にある要素を走査する
    excluded_count = 0
    for sibling in target_title.find_all_next():
        if sibling.name == "div" and "title" in (sibling.get("class") or []):
            break  # 次のセクションに入った
        if sibling.name == "table":
            first_tr = sibling.find("tr")
            if first_tr is None:
                continue
            th_cells = first_tr.find_all("th", recursive=False)
            if len(th_cells) == 1 and (th_cells[0].get("colspan") == "5"):
                # 「高校関連」等、対象外セクションのカテゴリ見出し行を持つ表
                excluded_count += 1
                continue
            header_text = sibling.get_text()
            if all(h in header_text for h in EXPECTED_HEADER_TEXTS) and len(th_cells) >= 3:
                return sibling, excluded_count, fiscal_year
            # 列ヘッダーの形を満たさない表（注意書き等）はスキップ
            continue

    return None, excluded_count, fiscal_year


# ---------------------------------------------------------------------------
# メイン解析関数
# ---------------------------------------------------------------------------

def parse(
    raw_bytes: bytes,
    page_url: str = DEFAULT_PAGE_URL,
    http_content_type: Optional[str] = None,
    retrieved_at: Optional[str] = None,
) -> ParseResult:
    charset_result = detect_charset(raw_bytes, http_content_type=http_content_type)

    if not charset_result.confident or not charset_result.charset:
        return ParseResult(
            records=[],
            charset_detection=charset_result,
            excluded_table_count=0,
            target_table_found=False,
            overall_status="failed",
            fatal_error=f"文字コードを確信を持って判定できませんでした（{charset_result.detail}）。文字化けしたまま処理を続けないため停止します。",
        )

    try:
        text = strict_decode(raw_bytes, charset_result.charset)
    except UnicodeDecodeError as e:
        return ParseResult(
            records=[],
            charset_detection=charset_result,
            excluded_table_count=0,
            target_table_found=False,
            overall_status="failed",
            fatal_error=f"宣言された文字コード({charset_result.charset})での厳密デコードに失敗しました: {e}",
        )

    soup = BeautifulSoup(text, "lxml")

    table, excluded_count, fiscal_year = find_target_table(soup)
    if table is None:
        return ParseResult(
            records=[],
            charset_detection=charset_result,
            excluded_table_count=excluded_count,
            target_table_found=False,
            overall_status="failed",
            fatal_error="対象見出し「競技日程」に対応するメイン表が見つかりませんでした。",
        )
    if fiscal_year is None:
        return ParseResult(
            records=[],
            charset_detection=charset_result,
            excluded_table_count=excluded_count,
            target_table_found=True,
            overall_status="failed",
            fatal_error="対象見出しから「YYYY年度」の年度を抽出できませんでした。年度の推測は行わないため停止します。",
        )

    grid = expand_table_with_rowspan(table)
    retrieved_at = retrieved_at or now_iso()

    records: List[TournamentRecord] = []
    rows = grid.rows
    if not rows:
        return ParseResult(
            records=[], charset_detection=charset_result, excluded_table_count=excluded_count,
            target_table_found=True, overall_status="failed", fatal_error="対象表に行がありません。",
        )

    header_row = rows[0]
    data_rows = rows[1:]

    for row in data_rows:
        if len(row) < 4:
            continue  # 列数が足りない行（注意書きの混入等）は大会データとして扱わない

        month_cell, day_cell, name_cell, link_cell, venue_cell = (
            row[0], row[1], row[2], row[3], row[4] if len(row) > 4 else None
        )

        warnings: List[str] = []

        tournament_name = cell_text(name_cell)
        if not tournament_name:
            continue  # 大会名が空の行はデータ行とみなさない（注意書き・空行対策）

        month_context_text = cell_text(month_cell)
        day_lines = cell_lines(day_cell)
        event_date_text = (month_context_text + " " + " / ".join(day_lines)).strip()

        periods = parse_date_cell(month_context_text, day_lines, fiscal_year)
        year_basis_notes = set()
        for p in periods:
            if p.year is not None:
                year_basis_notes.add(f"{fiscal_year}年度見出しから導出")
            warnings.extend(p.warnings)
        year_basis = "; ".join(sorted(year_basis_notes)) if year_basis_notes else "年度を導出できた区間なし"

        venue_text = cell_text(venue_cell) if venue_cell is not None else ""
        if venue_text in ("", "　", "未定"):
            if venue_text == "未定":
                venue_normalized = None
                warnings.append("会場未定")
            else:
                venue_normalized = None
                warnings.append("会場欄が空欄です")
        else:
            venue_normalized = venue_text

        related_links = extract_related_links(link_cell.tag, page_url) if link_cell and link_cell.tag else []
        guideline_url = next((l.url for l in related_links if l.link_type == "guideline" and l.url), None)

        unsafe_links = [l for l in related_links if l.link_type == "unsafe_excluded"]
        if unsafe_links:
            warnings.append(f"安全でないURLを{len(unsafe_links)}件除外しました")

        record_status = "success"
        if any(p.normalized is None for p in periods) or not periods:
            record_status = "partial"
        if venue_normalized is None:
            record_status = "partial" if record_status == "success" else record_status

        records.append(
            TournamentRecord(
                source_id=SOURCE_ID,
                source_name=SOURCE_NAME,
                source_page_url=page_url,
                tournament_name=tournament_name,
                event_date_text=event_date_text,
                event_date_periods=[asdict(p) for p in periods],
                venue_text=venue_text,
                venue_normalized=venue_normalized,
                guideline_url=guideline_url,
                related_links=[asdict(l) for l in related_links],
                retrieved_at=retrieved_at,
                parse_status=record_status,
                warnings=warnings,
                year_basis=year_basis,
            )
        )

    overall_status = "success" if records and all(r.parse_status == "success" for r in records) else (
        "partial" if records else "failed"
    )

    return ParseResult(
        records=records,
        charset_detection=charset_result,
        excluded_table_count=excluded_count,
        target_table_found=True,
        overall_status=overall_status,
    )
