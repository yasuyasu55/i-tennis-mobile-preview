#!/usr/bin/env python3
"""Read-only probe for the Okazaki Tennis Association tournament page.

Fetches the public tournament page and up to two linked guideline PDFs via GET.
It never follows application links, submits forms, or writes raw HTML/PDF files.
Only sanitized response metadata and extracted visible fields are written to JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional


PAGE_URL = "https://www.okazaki-tennis.com/taikai-r8"
ALLOWED_HOST = "www.okazaki-tennis.com"
USER_AGENT = "I-Tennis-Mobile-Okazaki-ReadOnlyProbe/0.1"
MAX_HTML = 8 * 1024 * 1024
MAX_PDF = 12 * 1024 * 1024
MAX_PDFS = 2
DELAY_SECONDS = 3.0
TIMEOUT_SECONDS = 25
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data-source"))
from parsers import okazaki_tennis as app_parser  # noqa: E402


@dataclass
class Node:
    tag: str
    attrs: dict
    parent: Optional["Node"] = None
    children: list = None

    def __post_init__(self):
        if self.children is None:
            self.children = []

    def text(self) -> str:
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(parts)

    def descendants(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.descendants(tag)


class SameOfficialHostRedirects(urllib.request.HTTPRedirectHandler):
    """Allow only HTTPS redirects that stay on the association host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            raise urllib.error.HTTPError(newurl, code, "redirect outside official host blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_URL_OPENER = urllib.request.build_opener(SameOfficialHostRedirects())


class TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag.lower(), dict(attrs), self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag.lower(), dict(attrs), self.stack[-1]))

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1].children.append(data)


def _norm(s: str) -> str:
    # Wix may insert invisible formatting characters into visible card text.
    # Remove them before title deduplication and parser-to-probe comparison.
    s = (s.replace("\u3000", " ").replace("\xa0", " ")
           .replace("\u200b", "").replace("\ufeff", ""))
    return re.sub(r"\s+", " ", s).strip()


def _label_value(text: str, label_rx: str, next_rx: str) -> Optional[str]:
    m = re.search(label_rx + r"\s*(.*?)\s*(?=" + next_rx + r")", text, re.S)
    return _norm(m.group(1)) if m else None


def extract_events(html: bytes, page_url: str = PAGE_URL):
    # Decode according to the declared charset when available; Wix normally serves UTF-8.
    text = html.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", text)
    parser = TreeParser()
    parser.feed(text)
    headings = [n for n in parser.root.descendants("h4") if _norm(n.text())]
    events = []
    seen = set()
    for h in headings:
        title = _norm(h.text())
        card = h.parent
        while card and card is not parser.root:
            candidate_text = _norm(card.text())
            if (len(candidate_text) < 10000 and re.search(r"開催\s*日", candidate_text) and
                    re.search(r"資\s*格", candidate_text) and re.search(r"場\s*所", candidate_text)):
                break
            card = card.parent
        if not card or card is parser.root or title in seen:
            continue
        body = _norm(card.text())
        # Labels in the association cards are bracketed; NFKC normalizes full-width spaces.
        body = body.replace("〈種 目〉", "〈種目〉").replace("〈資 格〉", "〈資格〉")
        fields = {
            "title": title,
            "date_text": _label_value(body, r"〈\s*開催日\s*〉", r"〈\s*予備日\s*〉|〈\s*種\s*目\s*〉"),
            "events_text": _label_value(body, r"〈\s*種\s*目\s*〉", r"〈\s*資\s*格\s*〉"),
            "eligibility": _label_value(body, r"〈\s*資\s*格\s*〉", r"〈\s*場\s*所\s*〉"),
            "venue": _label_value(body, r"〈\s*場\s*所\s*〉", r"〈\s*要\s*項\s*〉"),
        }
        dm = re.search(r"申込締切日\s*[:：]?\s*([0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日(?:\s*\([^)]*\)|\s*（[^）]*）)?)", body)
        fields["deadline_text"] = _norm(dm.group(1)) if dm else None
        pdfs = []
        for a in card.descendants("a"):
            href = a.attrs.get("href", "")
            label = _norm(a.text())
            absolute = urllib.parse.urljoin(page_url, href)
            parts = urllib.parse.urlsplit(absolute)
            # Restrict to the association's hosted PDF links. Never include registration forms.
            if (parts.scheme == "https" and parts.hostname == ALLOWED_HOST and
                    parts.path.lower().endswith(".pdf") and "申込書" not in label and
                    not re.search(r"ドロー|結果", label)):
                pdfs.append({"label": label, "url": absolute})
        fields["guideline_url"] = next((p["url"] for p in pdfs if "詳細" in p["label"]), None)
        if not fields["guideline_url"] and len(pdfs) == 1:
            fields["guideline_url"] = pdfs[0]["url"]
        fields["pdf_link_count_in_card"] = len(pdfs)
        if fields["date_text"] and fields["eligibility"] and fields["venue"]:
            events.append(fields)
            seen.add(title)
    return events


def _first_month_day(text: str):
    s = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _deadline_month_day(text: Optional[str]):
    if not text:
        return None
    s = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", s)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _event_year(month: int, today: date):
    # Annual schedule crosses the year: Jan-Mar events belong to the next season;
    # Apr-Sep dates earlier than today are past; Oct-Dec are in the current season.
    if month <= 3:
        return today.year + 1
    return today.year


def _deadline_is_upcoming(event: dict, today: date) -> bool:
    md = _first_month_day(event.get("date_text") or "")
    dl = _deadline_month_day(event.get("deadline_text"))
    if not md or not dl:
        return False
    event_year = _event_year(md[0], today)
    try:
        event_date = date(event_year, md[0], md[1])
        deadline = date(event_year, dl[0], dl[1])
    except ValueError:
        return False
    return event_date >= today and today <= deadline < event_date


def _request(url: str, max_bytes: int):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5"}, method="GET")
    try:
        with _URL_OPENER.open(req, timeout=TIMEOUT_SECONDS) as res:
            body = res.read(max_bytes + 1)
            return {"status": res.status, "final_url": res.geturl(), "content_type": res.headers.get("Content-Type", ""), "body": body}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": e.geturl(), "content_type": e.headers.get("Content-Type", "") if e.headers else "", "body": b""}


def _robots_allows(url: str, log: list):
    robots_url = "https://" + ALLOWED_HOST + "/robots.txt"
    try:
        r = _request(robots_url, 512_000)
    except Exception as exc:
        log.append({"kind": "robots", "url": robots_url, "error": type(exc).__name__})
        return False, "robots.txtを確認できないため停止", None
    log.append({"kind": "robots", "url": robots_url, "status": r["status"], "bytes": len(r["body"]),
                "sha256": hashlib.sha256(r["body"]).hexdigest() if r["body"] else None})
    if r["status"] == 200:
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(r["body"].decode("utf-8", "replace").splitlines())
        if not rp.can_fetch(USER_AGENT, url):
            return False, "robots.txtで対象URLが許可されていないため停止", rp
    elif 400 <= r["status"] < 500:
        pass
    else:
        return False, "robots.txtの応答が不明確なため停止", None
    return True, "robots確認済み", rp if r["status"] == 200 else None


def run(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    log = []
    today = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Tokyo")).date()
    report = {"probe": "okazaki-read-only-v0.1", "run_date_jst": today.isoformat(), "page_url": PAGE_URL,
              "read_only": True, "raw_html_saved": False, "raw_pdf_saved": False, "form_links_requested": False,
              "events": [], "pdf_checks": [], "requests": [], "status": "failed", "notes": []}
    allowed, message, robots = _robots_allows(PAGE_URL, log)
    if not allowed:
        report["notes"].append(message)
        report["requests"] = log
        _write_report(out_dir, report)
        return 2
    time.sleep(DELAY_SECONDS)
    try:
        page = _request(PAGE_URL, MAX_HTML)
    except Exception as exc:
        report["notes"].append("大会ページGET失敗: " + type(exc).__name__)
        report["requests"] = log
        _write_report(out_dir, report)
        return 2
    page_meta = {k: page[k] for k in ("status", "final_url", "content_type")}
    page_meta["bytes"] = len(page["body"])
    page_meta["sha256"] = hashlib.sha256(page["body"]).hexdigest() if page["body"] else None
    log.append({"kind": "page", "url": PAGE_URL, **page_meta})
    final_host = urllib.parse.urlsplit(page["final_url"]).hostname
    if page["status"] != 200 or final_host != ALLOWED_HOST or len(page["body"]) > MAX_HTML or "html" not in page["content_type"].lower():
        report["notes"].append("大会ページ応答が想定外（status/host/size/content-type）")
        report["requests"] = log
        _write_report(out_dir, report)
        return 2
    events = extract_events(page["body"])
    report["events"] = events
    if not events:
        report["notes"].append("大会カードを抽出できません。raw HTMLは保存せず停止しました")
        report["requests"] = log
        _write_report(out_dir, report)
        return 2

    # Validate the production candidate parser against the same live response bytes.
    # The response remains in memory only; only sanitized fields and comparison results are written.
    parsed = app_parser.parse_page(page["body"], page_url=PAGE_URL)
    def norm_value(v):
        return re.sub(r"\s+", " ", v.replace("\u200b", "").replace("\ufeff", "")).strip() if isinstance(v, str) else v
    differences = []
    parsed_by_title = {x["title"]: x for x in parsed.get("events", [])}
    compare_fields = ("date_text", "events_text", "eligibility", "venue", "deadline_text", "guideline_url")
    for event in events:
        other = parsed_by_title.get(event["title"])
        if other is None:
            differences.append({"title": event["title"], "field": "record", "probe": "present", "candidate_parser": "missing"})
            continue
        for field in compare_fields:
            if norm_value(event.get(field)) != norm_value(other.get(field)):
                differences.append({"title": event["title"], "field": field,
                                    "probe": norm_value(event.get(field)), "candidate_parser": norm_value(other.get(field))})
    for title in parsed_by_title:
        if not any(x["title"] == title for x in events):
            differences.append({"title": title, "field": "record", "probe": "missing", "candidate_parser": "present"})
    report["candidate_parser_check"] = {
        "parser": "okazaki_tennis.py v0.1",
        "status": "match" if not parsed.get("fatal_error") and not differences else "mismatch",
        "fatal_error": parsed.get("fatal_error"),
        "event_count": len(parsed.get("events", [])),
        "matched_event_count": len(events) - sum(1 for d in differences if d.get("field") == "record" and d.get("probe") == "present"),
        "differences": differences,
        "normalized_event_dates": [{"title": e["title"], "dates": [p["normalized"] for p in e["periods"]]} for e in parsed.get("events", [])],
    }
    if report["candidate_parser_check"]["status"] != "match":
        report["notes"].append("アプリ候補パーサーとprobeの抽出結果に差があります。候補採用は保留してください")
        report["requests"] = log
        _write_report(out_dir, report)
        return 3

    # Only fetch linked guideline PDFs for entries whose deadline text is visibly present.
    pdf_urls = []
    for event in events:
        if not _deadline_is_upcoming(event, today):
            continue
        url = event.get("guideline_url")
        if url and url not in pdf_urls:
            pdf_urls.append(url)
        if len(pdf_urls) >= MAX_PDFS:
            break
    if robots is not None:
        permitted = []
        for url in pdf_urls:
            if robots.can_fetch(USER_AGENT, url):
                permitted.append(url)
            else:
                report["notes"].append("robots.txtで要項PDFが許可されていないため取得しません")
        pdf_urls = permitted
    if len(pdf_urls) < MAX_PDFS:
        report["notes"].append("要項PDFの取得対象は上限2件。全件取得の確認ではありません")
    for url in pdf_urls:
        time.sleep(DELAY_SECONDS)
        item = {"url": url, "status": None, "bytes": 0, "sha256": None, "content_type": None, "valid_pdf": False}
        try:
            r = _request(url, MAX_PDF)
            item.update({"status": r["status"], "bytes": len(r["body"]), "sha256": hashlib.sha256(r["body"]).hexdigest() if r["body"] else None,
                         "content_type": r["content_type"], "final_url": r["final_url"]})
            item["valid_pdf"] = (r["status"] == 200 and urllib.parse.urlsplit(r["final_url"]).hostname == ALLOWED_HOST and
                                 "pdf" in r["content_type"].lower() and r["body"].startswith(b"%PDF-") and len(r["body"]) <= MAX_PDF)
            if not item["valid_pdf"]:
                report["notes"].append("PDF応答をPDFとして確認できませんでした")
        except Exception as exc:
            item["error"] = type(exc).__name__
            report["notes"].append("PDF GET失敗: " + type(exc).__name__)
        report["pdf_checks"].append(item)
        log.append({"kind": "pdf", **item})
    report["requests"] = log
    report["status"] = "probe_ok" if report.get("candidate_parser_check", {}).get("status") == "match" and report["pdf_checks"] and all(x.get("valid_pdf") for x in report["pdf_checks"]) else "page_only_or_partial"
    _write_report(out_dir, report)
    return 0 if report["status"] == "probe_ok" else 1


def _write_report(out_dir: Path, report: dict):
    # Metadata and visible text only; response bodies are never written.
    path = out_dir / "okazaki_probe_result.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    from zoneinfo import ZoneInfo
    parser = argparse.ArgumentParser(description="岡崎テニス協会ページの読み取り専用probe")
    parser.add_argument("--out-dir", help="結果JSONの出力先（生HTML/PDFは保存しません）")
    args = parser.parse_args()
    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("probe_out") / stamp
    code = run(out_dir)
    print("岡崎公式ページ 読み取りprobe")
    print("実行結果: " + ("確認完了" if code == 0 else "部分確認" if code == 1 else "停止（結果ファイルを確認）"))
    print("出力: " + str(out_dir / "okazaki_probe_result.json"))
    raise SystemExit(code)
