"""
TTA-MOBILE-007 豊橋テニス協会 専用パーサー（試作・独立モジュール）

対象：
  - 大会要項一覧（複数ページ、#post_list 配下の li.article のみ）
  - 代表的な大会要項投稿（#post_title / #post_meta_top / .post_content のみ）
  - 年間計画ページ（同上）

対象外（意図的に読まない）：
  - ヘッダー・フッター・サイドバー・カレンダーウィジェット等のページ全体の他の部分
  - ブラウザ拡張機能が挿入した要素（chrome-extension://、mns_*、UMSDataElement等）
  - PDF本文（リンクの保持のみ。中身は解析しない）

既存の app_auto_test.py・PC版UI・CSV・モバイル版・Supabaseには一切接続しません。
実サイトへの通信も行っていません（すべて榎本さん提供の保存HTMLを解析）。
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
    now_iso,
)

SOURCE_ID = "toyohashi_tennis_association"
SOURCE_NAME = "豊橋テニス協会"

# 表示日・datetime属性の意味は確定できないため、常にこの定型文を記録する。
DATE_MEANING_STATUS = (
    "表示日(displayed_date_text)とtime要素のdatetime属性(datetime_attribute)は、"
    "投稿日・更新日・開催日のいずれであるかHTML上では確定できない。開催日としては使用しない。"
)

TITLE_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})_")
FORM_URL_PATTERN = re.compile(r"forms\.gle|docs\.google\.com/forms", re.IGNORECASE)


# ---------------------------------------------------------------------------
# データ形式
# ---------------------------------------------------------------------------

@dataclass
class LinkInfo:
    label: str
    url: Optional[str]
    link_type: str  # guideline_pdf / entry_form / other / unsafe_excluded


@dataclass
class PaginationInfo:
    current_page: Optional[int]
    total_pages: Optional[int]
    next_page_url: Optional[str]
    prev_page_url: Optional[str]
    warnings: List[str] = field(default_factory=list)


@dataclass
class GuidelineListEntry:
    source_id: str
    source_name: str
    source_page_url: str
    post_id: Optional[str]
    post_url: Optional[str]
    title_text: str
    title_year_month_text: Optional[str]
    category_text: str
    displayed_date_text: str
    datetime_attribute: Optional[str]
    date_meaning_status: str
    page_number: Optional[int]
    retrieved_at: str
    parse_status: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GuidelineDetailRecord:
    source_id: str
    source_name: str
    source_page_url: str
    post_id: Optional[str]
    title_text: str
    title_year_month_text: Optional[str]
    category_text: str
    displayed_date_text: str
    datetime_attribute: Optional[str]
    date_meaning_status: str
    event_date_text: str
    event_date_periods: List[Any]
    venue_text: str
    deadline_date_text: str
    guideline_links: List[Dict[str, Any]]
    entry_form_links: List[Dict[str, Any]]
    related_links: List[Dict[str, Any]]
    retrieved_at: str
    parse_status: str
    warnings: List[str]
    year_basis: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScheduleRecord:
    source_id: str
    source_name: str
    source_page_url: str
    title_text: str
    category_text: str
    displayed_date_text: str
    datetime_attribute: Optional[str]
    date_meaning_status: str
    schedule_pdf_url: Optional[str]
    schedule_pdf_title: Optional[str]
    related_links: List[Dict[str, Any]]
    retrieved_at: str
    parse_status: str
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ListingParseResult:
    entries: List[GuidelineListEntry]
    pagination: Optional[PaginationInfo]
    charset_detection: CharsetDetectionResult
    overall_status: str
    fatal_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "fatal_error": self.fatal_error,
            "pagination": asdict(self.pagination) if self.pagination else None,
            "charset_detection": asdict(self.charset_detection),
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass
class DetailParseResult:
    record: Optional[GuidelineDetailRecord]
    charset_detection: CharsetDetectionResult
    overall_status: str
    fatal_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "fatal_error": self.fatal_error,
            "charset_detection": asdict(self.charset_detection),
            "record": self.record.to_dict() if self.record else None,
        }


@dataclass
class ScheduleParseResult:
    record: Optional[ScheduleRecord]
    charset_detection: CharsetDetectionResult
    overall_status: str
    fatal_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "fatal_error": self.fatal_error,
            "charset_detection": asdict(self.charset_detection),
            "record": self.record.to_dict() if self.record else None,
        }


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _decode(raw_bytes: bytes, http_content_type: Optional[str] = None):
    """文字コードを判定し厳密デコードする。失敗時は (None, charset_result, error_message) を返す。"""
    charset_result = detect_charset(raw_bytes, http_content_type=http_content_type)
    if not charset_result.confident or not charset_result.charset:
        return None, charset_result, (
            f"文字コードを確信を持って判定できませんでした（{charset_result.detail}）。"
            "文字化けしたまま処理を続けないため停止します。"
        )
    try:
        text = strict_decode(raw_bytes, charset_result.charset)
    except UnicodeDecodeError as e:
        return None, charset_result, f"宣言された文字コード({charset_result.charset})での厳密デコードに失敗しました: {e}"
    return text, charset_result, None


def _extract_post_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/(\d+)/?(?:$|\?)", url)
    return m.group(1) if m else None


def _extract_title_year_month(title_text: str) -> Optional[str]:
    """
    タイトル先頭の「YYYY-MM_」パターンを原文のまま返す（例："2026-10"）。
    これは開催日への変換に使わない。手掛かりとして保持するだけ。
    """
    m = TITLE_YEAR_MONTH_RE.match(title_text or "")
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def _classify_detail_link(url: str, label: str) -> str:
    if FORM_URL_PATTERN.search(url or ""):
        return "entry_form"
    if (url or "").lower().endswith(".pdf"):
        return "other_pdf"
    return "other"


def _link_label(a: Tag, fallback: str) -> str:
    """
    リンクの表示名を取得する。テキストが無い場合（画像のみのリンク等）は、
    title属性→画像のalt属性→呼び出し元が渡したfallbackの順で補う。
    空文字のまま放置すると、データとして何のリンクか分からなくなるため。
    """
    text = a.get_text(strip=True)
    if text:
        return text
    if a.get("title"):
        return a.get("title")
    img = a.find("img")
    if img is not None and img.get("alt"):
        return img.get("alt")
    return fallback


def _safe_link(page_url: str, href: Optional[str], label: str) -> LinkInfo:
    abs_url = absolutize_url(page_url, href)
    if href and abs_url is None:
        return LinkInfo(label=label, url=None, link_type="unsafe_excluded")
    return LinkInfo(label=label, url=abs_url, link_type=_classify_detail_link(abs_url or "", label))


# ---------------------------------------------------------------------------
# 一覧ページの解析
# ---------------------------------------------------------------------------

def _parse_pagination(soup: BeautifulSoup, page_url: str) -> PaginationInfo:
    warnings: List[str] = []

    next_link = soup.select_one('head link[rel="next"]')
    prev_link = soup.select_one('head link[rel="prev"]')
    next_url = absolutize_url(page_url, next_link.get("href")) if next_link else None
    prev_url = absolutize_url(page_url, prev_link.get("href")) if prev_link else None

    nav = soup.select_one(".page_navi")
    current_page: Optional[int] = None
    total_pages: Optional[int] = None

    if nav is not None:
        current_el = nav.select_one(".page-numbers.current")
        if current_el:
            txt = current_el.get_text(strip=True)
            if txt.isdigit():
                current_page = int(txt)

        # 注意: ul要素自体にも class="page-numbers" が付与されているため、
        # "a.page-numbers" / "span.page-numbers" のようにタグ名を限定して選ぶ。
        # 限定しないと ul の get_text() が子要素の数字をすべて連結してしまい
        # （例: "1234567"）、誤ったページ数を拾ってしまう。
        page_numbers: List[int] = []
        for el in nav.select("a.page-numbers, span.page-numbers"):
            txt = el.get_text(strip=True)
            if txt.isdigit():
                page_numbers.append(int(txt))
        if page_numbers:
            total_pages = max(page_numbers)
        else:
            warnings.append("ページ送り領域にページ番号が見つかりませんでした")
    else:
        warnings.append("ページ送り領域(.page_navi)が見つかりませんでした")

    if current_page is None:
        warnings.append("現在のページ番号を特定できませんでした")

    return PaginationInfo(
        current_page=current_page,
        total_pages=total_pages,
        next_page_url=next_url,
        prev_page_url=prev_url,
        warnings=warnings,
    )


def parse_listing(raw_bytes: bytes, page_url: str, http_content_type: Optional[str] = None,
                   retrieved_at: Optional[str] = None) -> ListingParseResult:
    text, charset_result, error = _decode(raw_bytes, http_content_type)
    if error:
        return ListingParseResult(entries=[], pagination=None, charset_detection=charset_result,
                                   overall_status="failed", fatal_error=error)

    soup = BeautifulSoup(text, "lxml")
    retrieved_at = retrieved_at or now_iso()

    post_list = soup.select_one("#post_list")
    if post_list is None:
        return ListingParseResult(
            entries=[], pagination=None, charset_detection=charset_result,
            overall_status="failed", fatal_error="#post_list が見つかりませんでした。",
        )

    pagination = _parse_pagination(soup, page_url)

    entries: List[GuidelineListEntry] = []
    for li in post_list.select("li.article"):
        warnings: List[str] = []
        a = li.find("a", recursive=False)
        if a is None:
            continue

        post_url = absolutize_url(page_url, a.get("href"))
        if a.get("href") and post_url is None:
            warnings.append("投稿URLが安全でないため除外しました")

        title_el = li.select_one(".info .title")
        title_text = title_el.get_text(strip=True) if title_el else (a.get("title") or "")
        if not title_text:
            warnings.append("大会名を取得できませんでした")

        category_el = li.select_one(".info .category")
        category_text = category_el.get_text(strip=True) if category_el else ""
        if not category_text:
            warnings.append("カテゴリを取得できませんでした")

        time_el = li.select_one(".info .date time")
        displayed_date_text = time_el.get_text(strip=True) if time_el else ""
        datetime_attribute = time_el.get("datetime") if time_el else None
        if not time_el:
            warnings.append("日付要素を取得できませんでした")

        title_year_month_text = _extract_title_year_month(title_text)

        parse_status = "success" if (title_text and post_url and time_el) else "partial"

        entries.append(GuidelineListEntry(
            source_id=SOURCE_ID,
            source_name=SOURCE_NAME,
            source_page_url=page_url,
            post_id=_extract_post_id(post_url),
            post_url=post_url,
            title_text=title_text,
            title_year_month_text=title_year_month_text,
            category_text=category_text,
            displayed_date_text=displayed_date_text,
            datetime_attribute=datetime_attribute,
            date_meaning_status=DATE_MEANING_STATUS,
            page_number=pagination.current_page,
            retrieved_at=retrieved_at,
            parse_status=parse_status,
            warnings=warnings,
        ))

    overall_status = "success" if entries and all(e.parse_status == "success" for e in entries) else (
        "partial" if entries else "failed"
    )

    return ListingParseResult(
        entries=entries, pagination=pagination, charset_detection=charset_result,
        overall_status=overall_status,
    )


# ---------------------------------------------------------------------------
# 代表投稿（大会要項詳細）の解析
# ---------------------------------------------------------------------------

def parse_guideline_detail(raw_bytes: bytes, page_url: str, http_content_type: Optional[str] = None,
                            retrieved_at: Optional[str] = None) -> DetailParseResult:
    text, charset_result, error = _decode(raw_bytes, http_content_type)
    if error:
        return DetailParseResult(record=None, charset_detection=charset_result,
                                  overall_status="failed", fatal_error=error)

    soup = BeautifulSoup(text, "lxml")
    retrieved_at = retrieved_at or now_iso()

    title_el = soup.select_one("#post_title")
    if title_el is None:
        return DetailParseResult(
            record=None, charset_detection=charset_result,
            overall_status="failed", fatal_error="#post_title が見つかりませんでした。",
        )
    title_text = title_el.get_text(strip=True)

    warnings: List[str] = []

    meta = soup.select_one("#post_meta_top")
    category_text = ""
    displayed_date_text = ""
    datetime_attribute = None
    if meta is not None:
        cat_el = meta.select_one(".category")
        category_text = cat_el.get_text(strip=True) if cat_el else ""
        time_el = meta.select_one(".date time")
        if time_el is not None:
            displayed_date_text = time_el.get_text(strip=True)
            datetime_attribute = time_el.get("datetime")
    else:
        warnings.append("#post_meta_top が見つかりませんでした（カテゴリ・日付は未取得）")

    content = soup.select_one(".post_content")
    guideline_links: List[LinkInfo] = []
    entry_form_links: List[LinkInfo] = []
    related_links: List[LinkInfo] = []

    if content is not None:
        for a in content.select("a.link-to-pdf"):
            label = a.get("title") or a.get_text(strip=True) or "要項PDF"
            link = _safe_link(page_url, a.get("href"), label)
            link.link_type = "guideline_pdf" if link.url else "unsafe_excluded"
            guideline_links.append(link)

        seen_hrefs = {l.url for l in guideline_links if l.url}
        for a in content.select("a"):
            if a in content.select("a.link-to-pdf"):
                continue
            href = a.get("href")
            abs_url = absolutize_url(page_url, href)
            if abs_url and abs_url in seen_hrefs:
                continue
            label = _link_label(a, fallback="（画像等のみのリンク）")
            link = _safe_link(page_url, href, label)
            if link.url is None and href:
                related_links.append(link)
                continue
            if link.link_type == "entry_form":
                entry_form_links.append(link)
                seen_hrefs.add(link.url)
            elif link.url:
                related_links.append(link)
                seen_hrefs.add(link.url)
    else:
        warnings.append(".post_content が見つかりませんでした（関連リンクは未取得）")

    if not guideline_links:
        warnings.append("要項PDFリンクが見つかりませんでした")
    if not entry_form_links:
        warnings.append("申込フォームリンクが見つかりませんでした")

    title_year_month_text = _extract_title_year_month(title_text)

    # 開催日・会場・締切日：指示どおり、HTML本文に明記が無い前提で未取得のまま扱う。
    # （PDF本文は解析しないため、タイトルの年月手掛かりも開催日へは変換しない）
    warnings.append("開催日はPDF本文内の可能性があるため未取得")
    warnings.append("会場はHTML本文に明記されていないため未取得")
    warnings.append("締切日はHTML本文に明記されていないため未取得")

    record = GuidelineDetailRecord(
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        source_page_url=page_url,
        post_id=_extract_post_id(page_url),
        title_text=title_text,
        title_year_month_text=title_year_month_text,
        category_text=category_text,
        displayed_date_text=displayed_date_text,
        datetime_attribute=datetime_attribute,
        date_meaning_status=DATE_MEANING_STATUS,
        event_date_text="",
        event_date_periods=[],
        venue_text="",
        deadline_date_text="",
        guideline_links=[asdict(l) for l in guideline_links],
        entry_form_links=[asdict(l) for l in entry_form_links],
        related_links=[asdict(l) for l in related_links],
        retrieved_at=retrieved_at,
        parse_status="partial",
        warnings=warnings,
        year_basis="年の根拠なし（開催日を確定していないため年度変換は行っていない）",
    )

    return DetailParseResult(record=record, charset_detection=charset_result, overall_status="partial")


# ---------------------------------------------------------------------------
# 年間計画ページの解析
# ---------------------------------------------------------------------------

def parse_schedule(raw_bytes: bytes, page_url: str, http_content_type: Optional[str] = None,
                    retrieved_at: Optional[str] = None) -> ScheduleParseResult:
    text, charset_result, error = _decode(raw_bytes, http_content_type)
    if error:
        return ScheduleParseResult(record=None, charset_detection=charset_result,
                                    overall_status="failed", fatal_error=error)

    soup = BeautifulSoup(text, "lxml")
    retrieved_at = retrieved_at or now_iso()

    title_el = soup.select_one("#post_title")
    if title_el is None:
        return ScheduleParseResult(
            record=None, charset_detection=charset_result,
            overall_status="failed", fatal_error="#post_title が見つかりませんでした。",
        )
    title_text = title_el.get_text(strip=True)

    warnings: List[str] = []

    meta = soup.select_one("#post_meta_top")
    category_text = ""
    displayed_date_text = ""
    datetime_attribute = None
    if meta is not None:
        cat_el = meta.select_one(".category")
        category_text = cat_el.get_text(strip=True) if cat_el else ""
        time_el = meta.select_one(".date time")
        if time_el is not None:
            displayed_date_text = time_el.get_text(strip=True)
            datetime_attribute = time_el.get("datetime")
    else:
        warnings.append("#post_meta_top が見つかりませんでした（カテゴリ・日付は未取得）")

    content = soup.select_one(".post_content")
    schedule_pdf_url = None
    schedule_pdf_title = None
    related_links: List[LinkInfo] = []

    if content is not None:
        pdf_a = content.select_one("a.link-to-pdf")
        if pdf_a is not None:
            link = _safe_link(page_url, pdf_a.get("href"), pdf_a.get("title") or "年間計画PDF")
            if link.url:
                schedule_pdf_url = link.url
                schedule_pdf_title = pdf_a.get("title")
            else:
                warnings.append("年間計画PDFのURLが安全でないため除外しました")
        else:
            warnings.append("年間計画PDFリンクが見つかりませんでした")

        for a in content.select("a"):
            if a is pdf_a:
                continue
            href = a.get("href")
            label = _link_label(a, fallback="（画像等のみのリンク）")
            link = _safe_link(page_url, href, label)
            if link.url:
                related_links.append(link)
    else:
        warnings.append(".post_content が見つかりませんでした")

    record = ScheduleRecord(
        source_id=SOURCE_ID,
        source_name=SOURCE_NAME,
        source_page_url=page_url,
        title_text=title_text,
        category_text=category_text,
        displayed_date_text=displayed_date_text,
        datetime_attribute=datetime_attribute,
        date_meaning_status=DATE_MEANING_STATUS,
        schedule_pdf_url=schedule_pdf_url,
        schedule_pdf_title=schedule_pdf_title,
        related_links=[asdict(l) for l in related_links],
        retrieved_at=retrieved_at,
        parse_status="partial" if schedule_pdf_url else "failed",
        warnings=warnings,
    )

    return ScheduleParseResult(record=record, charset_detection=charset_result, overall_status=record.parse_status)
