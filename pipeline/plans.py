"""情報源ごとの取得計画（どのページを、何件、どの順で取得するか）。取得できたものを {論理ファイル名: bytes} で返す。"""
import hashlib
import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "data-source"))
from parsers import toyohashi_tennis as tt   # noqa: E402
from parsers import toyokawa_tennis as tk    # noqa: E402
from parsers import hamamatsu_tennis as hm   # noqa: E402
from parsers import okazaki_tennis as ok     # noqa: E402

from fetcher import FetchError               # noqa: E402
import pdftext                               # noqa: E402


class SourcePlanResult:
    def __init__(self):
        self.inputs = {}      # 論理名 → bytes（パーサーへ渡す）
        self.raw_files = {}   # 保存用（artifact）の生データ（PDFの原本など）
        self.notes = []       # 取得できなかった任意ページ等の記録（失敗ではない）
        self.stats = {}       # 実行記録用の集計（浜松: 要項PDFの取得・解析の件数など）


def _generic_pages(cfg, fetcher, cfgroot, res):
    for p in cfg["pages"]:
        try:
            r = fetcher.get(p["url"], cfgroot["max_html_bytes"])
        except FetchError as e:
            if p.get("required", True):
                raise
            res.notes.append("任意ページ %s を取得できませんでした（%s: %s）" % (p["name"], e.stage, e.message))
            continue
        res.inputs[p["name"]] = r.body


def plan_generic(cfg, cfgroot, fetcher, today, pdf_to_text):
    res = SourcePlanResult()
    _generic_pages(cfg, fetcher, cfgroot, res)
    return res


def plan_toyohashi(cfg, cfgroot, fetcher, today, pdf_to_text):
    res = SourcePlanResult()
    _generic_pages(cfg, fetcher, cfgroot, res)
    d = cfg.get("details")
    if d and d.get("max", 0) > 0:
        ids = []
        for name, url in (("toyohashi_guidelines_list.html", "https://www.toyohashi-tennis.net/c/taikai/guidelines/"),
                          ("toyohashi_guidelines_list_page2.html", "https://www.toyohashi-tennis.net/c/taikai/guidelines/page/2/")):
            if name in res.inputs:
                listing = tt.parse_listing(res.inputs[name], url)
                ids += [e.post_id for e in (listing.entries or []) if e.post_id]
        for pid in ids[:d["max"]]:     # 投稿の新しい順に、最大N件（掲載順の先頭）
            try:
                r = fetcher.get(d["url_template"].format(id=pid), cfgroot["max_html_bytes"])
            except FetchError as e:
                res.notes.append("詳細ページ %s を取得できませんでした（%s: %s）" % (pid, e.stage, e.message))
                continue
            res.inputs[d["name_template"].format(id=pid)] = r.body
    return res


def _host_allowed(url, suffixes):
    host = urllib.parse.urlsplit(url).hostname or ""
    return any(host == s or host.endswith("." + s) for s in suffixes)


def plan_toyokawa(cfg, cfgroot, fetcher, today, pdf_to_text):
    res = SourcePlanResult()
    _generic_pages(cfg, fetcher, cfgroot, res)
    pc = cfg.get("pdf")
    if pc and pc.get("max", 0) > 0 and "toyokawa_top.html" in res.inputs:
        top = tk.parse_top_page(res.inputs["toyokawa_top.html"])
        n = 0
        for e in top.get("events", []):
            # 要項PDFは、まだ終わっていない大会（最終開催日が今日以降）だけ取得する（アクセス数を抑える）
            ends = [p["normalized"] for p in e["periods"] if p.get("normalized")]
            if not ends or max(ends) < today or not e.get("guideline_url"):
                continue
            url = e["guideline_url"]
            if not _host_allowed(url, pc.get("allowed_host_suffixes", [])):
                res.notes.append("要項PDFのホストが許可リストに無いため取得しません: %s" % urllib.parse.urlsplit(url).hostname)
                continue
            if n >= pc["max"]:
                res.notes.append("要項PDFの取得数が上限（%d）に達したため、残りは取得しません" % pc["max"])
                break
            n += 1
            try:
                r = fetcher.get(url, cfgroot["max_pdf_bytes"])
                text = pdf_to_text(r.body)
            except FetchError as ex:
                res.notes.append("要項PDFを取得できませんでした（%s: %s）: %s" % (ex.stage, ex.message, e["title"]))
                continue
            except pdftext.PdfError as ex:
                res.notes.append("要項PDFのテキストを取り出せませんでした（%s）: %s" % (ex, e["title"]))
                continue
            key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            res.inputs["toyokawa_%s_youkou.txt" % key] = text.encode("utf-8")
            res.raw_files["toyokawa_%s_youkou.pdf" % key] = r.body
    return res


def hamamatsu_pdf_key(url):
    """要項PDFの入力名に使うキー（tools/build_tournaments_json.py の url_key と同じ）。"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def plan_hamamatsu(cfg, cfgroot, fetcher, today, pdf_to_text):
    """
    「大会情報」ページを取得し、まだ終わっていない大会（最後の開催予定日が今日以降、または日付なし）の要項PDFを、上限まで、順番に1件ずつ取得する（並列なし・再試行なし）。
    PDFごとに、取得の状態（取得成功／取得失敗／文字の取り出し失敗）を、hamamatsu_<キー>_fetch.json に残す。1つのPDFの失敗は、他のPDFの処理を止めない。
    """
    res = SourcePlanResult()
    _generic_pages(cfg, fetcher, cfgroot, res)
    pc = cfg.get("pdf")
    page = res.inputs.get("hamamatsu_tournament.html")
    st = {"pdf_attempted": 0, "pdf_fetched": 0, "pdf_fetch_failed": 0, "pdf_text_failed": 0, "pdf_extractor": None}
    if pc and pc.get("max", 0) > 0 and page:
        listing = hm.parse_tournament_page(page)
        n, seen = 0, set()
        for e in listing.get("events", []):
            url = e.get("guideline_url")
            if e["origin"] != "guidelines" or not url or url in seen:
                continue
            seen.add(url)
            ends = [p["normalized"].split("/")[-1] for p in e["periods"] if p.get("normalized")]
            if ends and max(ends) < today:
                continue
            if not _host_allowed(url, pc.get("allowed_host_suffixes", [])):
                res.notes.append("要項PDFのホストが許可リストに無いため取得しません: %s" % urllib.parse.urlsplit(url).hostname)
                continue
            if n >= pc["max"]:
                res.notes.append("要項PDFの取得数が上限（%d）に達したため、残りは取得しません" % pc["max"])
                break
            n += 1
            st["pdf_attempted"] += 1
            key = hamamatsu_pdf_key(url)
            status = {"url": url, "state": None, "stage": None, "message": None, "http_status": None, "bytes": None, "sha256": None, "extractor": None}
            try:
                r = fetcher.get(url, cfgroot["max_pdf_bytes"])
            except FetchError as ex:
                status.update({"state": "fetch_failed", "stage": ex.stage, "message": ex.message, "http_status": getattr(ex, "status", None)})
                st["pdf_fetch_failed"] += 1
                res.notes.append("要項PDFを取得できませんでした（%s: %s）: %s" % (ex.stage, ex.message, e["title"]))
                res.inputs["hamamatsu_%s_fetch.json" % key] = json.dumps(status, ensure_ascii=False).encode("utf-8")
                continue
            status.update({"http_status": r.status, "bytes": len(r.body), "sha256": hashlib.sha256(r.body).hexdigest()})
            res.raw_files["hamamatsu_%s_youkou.pdf" % key] = r.body
            st["pdf_fetched"] += 1
            try:
                text = pdf_to_text(r.body)
                status.update({"state": "fetched", "extractor": getattr(pdf_to_text, "last_extractor", None)})
                st["pdf_extractor"] = status["extractor"] or st["pdf_extractor"]
                res.inputs["hamamatsu_%s_youkou.txt" % key] = text.encode("utf-8")
                if not hm.parse_youkou_text(text)["ok"]:
                    try:
                        layout = pdftext.pdf_to_layout(r.body)
                        res.inputs["hamamatsu_%s_layout.txt" % key] = layout.encode("utf-8")
                        status["layout_extractor"] = "pdftotext -layout"
                    except pdftext.PdfError:
                        pass  # 任意の補足。失敗しても既存の解析結果を維持する。
            except pdftext.PdfError as ex:
                status.update({"state": "text_failed", "stage": "pdftext", "message": str(ex)})
                st["pdf_text_failed"] += 1
                res.notes.append("要項PDFを取得できましたが、テキストを取り出せませんでした（%s）: %s" % (ex, e["title"]))
            res.inputs["hamamatsu_%s_fetch.json" % key] = json.dumps(status, ensure_ascii=False).encode("utf-8")
    res.stats = st
    return res


def plan_okazaki(cfg, cfgroot, fetcher, today, pdf_to_text):
    res = SourcePlanResult()
    _generic_pages(cfg, fetcher, cfgroot, res)
    pc = cfg.get("pdf") or {}
    page = res.inputs.get("okazaki_tournament.html")
    res.stats = {"pdf_attempted": 0, "pdf_fetched": 0, "pdf_fetch_failed": 0, "pdf_text_failed": 0}
    if not page or not pc.get("max"):
        return res
    seen = set()
    for event in ok.parse_page(page)["events"]:
        url = event.get("guideline_url")
        ends = [p["normalized"] for p in event["periods"] if p.get("normalized")]
        if not url or url in seen or (ends and max(ends) < today):
            continue
        seen.add(url)
        if not _host_allowed(url, pc.get("allowed_host_suffixes", [])):
            continue
        if res.stats["pdf_attempted"] >= pc["max"]:
            res.notes.append("岡崎: 要項PDFの取得上限に達したため残りは未確認です")
            break
        res.stats["pdf_attempted"] += 1
        key = hamamatsu_pdf_key(url)
        status = {"url": url, "state": "fetch_failed"}
        try:
            pdf = fetcher.get(url, cfgroot["max_pdf_bytes"])
            res.stats["pdf_fetched"] += 1
            status.update(http_status=pdf.status, sha256=hashlib.sha256(pdf.body).hexdigest(), bytes=len(pdf.body))
            try:
                text = pdf_to_text(pdf.body)
                res.inputs["okazaki_%s_youkou.txt" % key] = text.encode("utf-8")
                status.update(state="fetched", extractor=getattr(pdf_to_text, "last_extractor", None))
            except pdftext.PdfError as ex:
                res.stats["pdf_text_failed"] += 1
                status.update(state="text_failed", message=str(ex))
                res.notes.append("岡崎: 要項の文字を読み取れません: " + event["title"])
        except FetchError as ex:
            res.stats["pdf_fetch_failed"] += 1
            status.update(stage=ex.stage, message=ex.message)
            res.notes.append("岡崎: 要項PDFを取得できません: " + event["title"])
        res.inputs["okazaki_%s_fetch.json" % key] = json.dumps(status, ensure_ascii=False).encode("utf-8")
    return res


PLANS = {"aichi": plan_generic, "toyohashi": plan_toyohashi, "gamagori": plan_generic, "toyokawa": plan_toyokawa, "hamamatsu": plan_hamamatsu, "okazaki": plan_okazaki, "toyota": plan_generic, "kariya": plan_generic, "anjo": plan_generic}

