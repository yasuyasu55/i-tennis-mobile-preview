"""
TTA-MOBILE-006 共通ユーティリティ

このモジュールは既存の app_auto_test.py には一切接続しません。
独立した試作パーサー専用の補助関数のみを提供します。

含まれるもの：
  - 文字コード判定（優先順位：HTTP Content-Type -> meta charset -> BOM -> 統計判定）
  - 安全なURL絶対化・スキーム検証
  - rowspanを考慮したHTMLテーブルの読み取り（2次元の実効セル配列に展開する）
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

try:
    import chardet  # 統計判定のフォールバックに使用
except Exception:  # pragma: no cover - 環境にchardetが無い場合の保険
    chardet = None


ALLOWED_URL_SCHEMES = {"http", "https"}


# ---------------------------------------------------------------------------
# 文字コード判定
# ---------------------------------------------------------------------------

@dataclass
class CharsetDetectionResult:
    charset: Optional[str]
    source: str  # "http_content_type" / "meta_charset" / "bom" / "statistical" / "undetermined"
    confident: bool
    detail: str = ""


def detect_charset(
    raw_bytes: bytes,
    http_content_type: Optional[str] = None,
) -> CharsetDetectionResult:
    """
    指示書どおりの優先順位で文字コードを判定する。
    1. HTTP Content-Type の charset
    2. HTML の meta charset
    3. BOM
    4. 統計判定（利用可能なら chardet）
    確信を持てない場合は confident=False とし、呼び出し側で failed 扱いにする。

    重要：いずれかの段階で情報が「無い」場合（例：Content-Typeにcharset指定が無い）は、
    その時点でconfident=Falseとして打ち切らず、必ず次の段階へ読み進める。
    confident=Falseを返すのは、4段階すべてを試しても判定できなかった場合だけである。
    """
    trail: List[str] = []  # どの段階を試し、どう判断したかの記録（detailに残す）

    # 1. HTTP Content-Type
    if http_content_type:
        m = re.search(r"charset=([\w\-]+)", http_content_type, re.IGNORECASE)
        if m:
            return CharsetDetectionResult(
                charset=m.group(1), source="http_content_type", confident=True,
                detail=f"Content-Type headerのcharsetを採用: {http_content_type}",
            )
        trail.append(f"Content-Type headerはあるがcharset指定なし（{http_content_type}）。meta charsetへ進む。")
    else:
        trail.append("Content-Type header情報なし（未取得または保存HTML等）。meta charsetへ進む。")

    # 2. meta charset（HTMLの先頭部分だけを見れば十分。ASCII範囲のタグ検索なのでデコード前でも安全）
    head = raw_bytes[:4096]
    # <meta charset="...">
    m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', head, re.IGNORECASE)
    if m:
        charset = m.group(1).decode("ascii", errors="ignore")
        return CharsetDetectionResult(
            charset=charset, source="meta_charset", confident=True,
            detail="; ".join(trail + [f"<meta charset> declaration found: {charset}"]),
        )
    # <meta http-equiv="Content-Type" content="text/html; charset=...">
    m = re.search(rb'<meta[^>]+http-equiv=["\']?Content-Type["\']?[^>]*charset=([\w\-]+)', head, re.IGNORECASE)
    if m:
        charset = m.group(1).decode("ascii", errors="ignore")
        return CharsetDetectionResult(
            charset=charset, source="meta_charset", confident=True,
            detail="; ".join(trail + [f"<meta http-equiv=Content-Type> declaration found: {charset}"]),
        )
    trail.append("meta charset宣言なし。BOMへ進む。")

    # 3. BOM
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        return CharsetDetectionResult(charset="utf-8-sig", source="bom", confident=True, detail="; ".join(trail + ["UTF-8 BOM detected"]))
    if raw_bytes[:2] == b"\xff\xfe":
        return CharsetDetectionResult(charset="utf-16-le", source="bom", confident=True, detail="; ".join(trail + ["UTF-16LE BOM detected"]))
    if raw_bytes[:2] == b"\xfe\xff":
        return CharsetDetectionResult(charset="utf-16-be", source="bom", confident=True, detail="; ".join(trail + ["UTF-16BE BOM detected"]))
    trail.append("BOMなし。統計判定へ進む。")

    # 4. 統計判定
    if chardet is not None:
        guess = chardet.detect(raw_bytes)
        enc = guess.get("encoding")
        conf = guess.get("confidence") or 0.0
        if enc and conf >= 0.8:
            return CharsetDetectionResult(
                charset=enc, source="statistical", confident=True,
                detail="; ".join(trail + [f"chardet guess: {enc} (confidence={conf:.2f})"]),
            )
        trail.append(f"chardet guess confidence too low: {enc} (confidence={conf:.2f})")
    else:
        trail.append("chardet未インストールのため統計判定を実行できず。")

    return CharsetDetectionResult(charset=None, source="undetermined", confident=False, detail="; ".join(trail))


def strict_decode(raw_bytes: bytes, charset: str) -> str:
    """
    指定charsetで厳密デコードする。置換文字や無視を一切使わない。
    失敗した場合は例外を投げる（呼び出し側でparse_status=failedにする）。
    """
    return raw_bytes.decode(charset, errors="strict")


# ---------------------------------------------------------------------------
# 安全なURL処理
# ---------------------------------------------------------------------------

def is_safe_http_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in ALLOWED_URL_SCHEMES and bool(parsed.netloc)


def absolutize_url(base_url: str, href: Optional[str]) -> Optional[str]:
    """
    相対URLをbase_urlを基準に絶対化する。危険なスキーム（javascript: 等）はNoneにする。
    base_url中の '~' はそのまま保持されることをテストで確認している（urljoinは'~'を変換しない）。
    """
    if not href:
        return None
    href = href.strip()
    if href.lower().startswith("javascript:") or href.lower().startswith("data:"):
        return None
    absolute = urljoin(base_url, href)
    if not is_safe_http_url(absolute):
        return None
    return absolute


# ---------------------------------------------------------------------------
# rowspan対応のテーブル読み取り
# ---------------------------------------------------------------------------

@dataclass
class TableCell:
    tag: Optional[Tag]   # 元の<td>/<th> (本物のセルの場合のみ。rowspanによる補完セルはNone)
    is_header: bool
    is_fill: bool        # rowspanによって上のセルから補完されたセルならTrue


@dataclass
class TableGrid:
    rows: List[List[TableCell]] = field(default_factory=list)


def expand_table_with_rowspan(table: Tag) -> TableGrid:
    """
    <table>をrowspanを考慮した2次元配列に展開する。colspanは列方向の見かけ上の
    スパンをそのまま1セルとして扱う（本パーサーでは列統合までは行わない）。
    """
    grid = TableGrid()
    pending: dict[int, tuple[TableCell, int]] = {}  # col_index -> (cell, remaining_rowspan)

    trs = table.find_all("tr", recursive=False)
    if not trs:
        # <tbody>経由の場合
        tbody = table.find("tbody", recursive=False)
        if tbody:
            trs = tbody.find_all("tr", recursive=False)

    for tr in trs:
        row: List[TableCell] = []
        col = 0
        cells = tr.find_all(["td", "th"], recursive=False)
        cell_iter = iter(cells)
        current_cell = next(cell_iter, None)

        while current_cell is not None or col in pending:
            if col in pending:
                fill_cell, remaining = pending[col]
                row.append(TableCell(tag=fill_cell.tag, is_header=fill_cell.is_header, is_fill=True))
                if remaining - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = (fill_cell, remaining - 1)
                col += 1
                continue

            if current_cell is None:
                break

            is_header = current_cell.name == "th"
            cell = TableCell(tag=current_cell, is_header=is_header, is_fill=False)
            row.append(cell)

            rowspan = int(current_cell.get("rowspan", 1) or 1)
            if rowspan > 1:
                pending[col] = (cell, rowspan - 1)

            col += 1
            current_cell = next(cell_iter, None)

        grid.rows.append(row)

    return grid


def cell_text(cell: TableCell) -> str:
    if cell.tag is None:
        return ""
    return cell.tag.get_text(strip=True)


def cell_lines(cell: TableCell) -> List[str]:
    """
    セル内を<br>で分割したテキスト行のリストを返す（前後空白除去、空行除去）。
    """
    if cell.tag is None:
        return []
    html = str(cell.tag)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text()
    lines = [ln.strip() for ln in text.split("\n")]
    return [ln for ln in lines if ln]


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
