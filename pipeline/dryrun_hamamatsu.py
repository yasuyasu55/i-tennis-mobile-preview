#!/usr/bin/env python3
"""
浜松の自動更新候補（R5）の dry-run（読み取り専用。Windowsで .bat から実行する）。TTA-CORE-001

  本番の更新処理（pipeline/run_update.py）と同じ経路で、浜松だけを「取得 → 解析 → 検証 → 候補JSON生成」まで実行する。
    ・通常のHTTP取得だけ（ブラウザ取得・Chrome拡張は使わない）。公式ページ → 要項PDF（順番に1件ずつ。並列なし・再試行なし）
    ・他の情報源（既存の60件）へはアクセスしない。前回のまま保持する
    ・公開データ（data/tournaments.json）は書き換えない。候補JSONは、出力フォルダに出す
    ・取得した生のHTML・PDFは、保存しない（証拠に残すのは、URL・HTTP状態・バイト数・SHA-256・件数）

  報告する内容:
    1. 既存60件が、1バイトも変わっていないか（記録ごとの正規化JSONのSHA-256＋既存4情報源のメタ情報）
    2. 浜松5件の内容差（現在の手動取得データとの項目別の差。手動→自動の記録の置き換えによる「予期される差」と、「内容の差」を分ける）
    3. 取得状態（公式ページ・要項PDFごとのHTTP状態・バイト数・SHA-256・文字の取り出し方法・解析の成否・stale）
    4. 候補JSONのSHA-256

使い方: python pipeline/dryrun_hamamatsu.py [--out-dir dryrun_out]
終了コード: 0 = 取得・解析・検証・候補JSON生成のすべて成功、既存60件は不変、浜松に「内容の差」なし / 1 = 失敗、または既存60件の変化・内容の差あり / 2 = 部品の不足
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in ("tools", "data-source", "pipeline"):
    sys.path.insert(0, os.path.join(ROOT, p))

JST = datetime.timezone(datetime.timedelta(hours=9))
H = "hamamatsu_tennis_association"
EXPECTED_DIFF_KEYS = {"acquisition", "notes", "source_snapshot_date"}      # 手動取得 → 自動取得の記録の置き換えによる、予期される差


def canon(o):
    return json.dumps(o, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def strip_notes(notes):
    """手動/自動の取得の記録の注記と、確認日だけの違いを除いた注記。"""
    return [n for n in notes if not n.startswith(("手動取得（自動取得ではありません）", "自動取得（通常のHTTP取得）", "要項PDFの確認日"))]


def diff_record(old, new):
    keys = sorted(set(old) | set(new))
    out = []
    for k in keys:
        a, b = old.get(k), new.get(k)
        if k == "notes":
            if strip_notes(a or []) != strip_notes(b or []):
                out.append({"field": k, "kind": "内容の差", "old": strip_notes(a or []), "new": strip_notes(b or [])})
            else:
                out.append({"field": k, "kind": "予期される差", "old": "（手動取得の注記）" if any(x.startswith("手動取得") for x in (a or [])) else "（自動取得の注記）", "new": "（自動取得の注記・確認日）"})
        elif a != b:
            out.append({"field": k, "kind": "予期される差" if k in EXPECTED_DIFF_KEYS else "内容の差", "old": a, "new": b})
    return out


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="dryrun_out")
    ap.add_argument("--data-dir", default=os.path.join(ROOT, "data"))
    a = ap.parse_args(argv)
    try:
        import run_update as ru
    except ImportError as e:
        print("部品が不足しています: %s\n→ install_dryrun_deps.bat を実行してください" % e)
        return 2
    stamp = datetime.datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out = os.path.join(a.out_dir, stamp)
    os.makedirs(out, exist_ok=True)
    data_path = os.path.join(a.data_dir, "tournaments.json")
    before_bytes = open(data_path, "rb").read()
    prev = json.loads(before_bytes.decode("utf-8"))
    started = datetime.datetime.now(JST)
    code = ru.main(["--dry-run", "--only", "hamamatsu", "--out-dir", out], runner=runner, data_dir=a.data_dir)
    shutil.rmtree(os.path.join(out, "raw"), ignore_errors=True)            # 取得した生のHTML・PDFは、保存しない
    finished = datetime.datetime.now(JST)
    summary = json.load(open(os.path.join(out, "run-summary.json"), encoding="utf-8"))
    res = (summary.get("results") or {}).get("hamamatsu") or {}
    cand_path = os.path.join(out, "proposed", "tournaments.json")
    rep = {"tool": "dryrun_hamamatsu.py", "started_at_jst": started.strftime("%Y-%m-%d %H:%M:%S"), "finished_at_jst": finished.strftime("%Y-%m-%d %H:%M:%S"),
           "runner_exit_code": code, "http_only": True, "browser_used": False, "public_data_changed": False,
           "fetch": {"status": res.get("status"), "stage": res.get("stage"), "message": res.get("message"), "stats": res.get("stats"), "detail": res.get("detail"), "notes": res.get("notes"),
                     "requests": [{k: x.get(k) for k in ("kind", "url", "status", "bytes", "sha256", "content_type", "error") if k in x} for x in summary.get("request_log", [])]},
           "validation_errors": summary.get("validation_errors")}
    ok_all = False
    if os.path.exists(cand_path) and res.get("status") == "ok":          # 取得に失敗したときは、候補を出さない（現在のデータを保持）
        cand_bytes = open(cand_path, "rb").read()
        cand = json.loads(cand_bytes.decode("utf-8"))
        shutil.copy(cand_path, os.path.join(out, "tournaments.candidate.json"))
        rep["candidate_json"] = {"file": "tournaments.candidate.json", "sha256": sha(cand_bytes), "bytes": len(cand_bytes), "records": len(cand["tournaments"])}
        old_rest = [t for t in prev["tournaments"] if t["source_id"] != H]
        new_rest = [t for t in cand["tournaments"] if t["source_id"] != H]
        h_old = sha("\n".join(canon(t) for t in old_rest).encode("utf-8"))
        h_new = sha("\n".join(canon(t) for t in new_rest).encode("utf-8"))
        meta_old = [s for s in prev["sources"] if s["id"] != H]
        meta_new = [s for s in cand["sources"] if s["id"] != H]
        rep["existing_records"] = {"count_before": len(old_rest), "count_after": len(new_rest), "sha256_before": h_old, "sha256_after": h_new, "identical": old_rest == new_rest and h_old == h_new,
                                   "source_meta_identical": canon(meta_old) == canon(meta_new), "order_identical": [t["id"] for t in old_rest] == [t["id"] for t in new_rest]}
        ho = {t["id"]: t for t in prev["tournaments"] if t["source_id"] == H}
        hn = {t["id"]: t for t in cand["tournaments"] if t["source_id"] == H}
        recs = []
        for i in sorted(set(ho) | set(hn)):
            if i not in hn:
                recs.append({"id": i, "title": ho[i]["title"], "change": "候補に無い（削除扱い）"})
            elif i not in ho:
                recs.append({"id": i, "title": hn[i]["title"], "change": "新規"})
            else:
                d = diff_record(ho[i], hn[i])
                recs.append({"id": i, "title": hn[i]["title"], "change": "差あり" if d else "同一", "diffs": d,
                             "acquisition": hn[i].get("acquisition"), "parse_status": hn[i].get("parse_status")})
        rep["hamamatsu"] = {"count_before": len(ho), "count_after": len(hn), "records": recs,
                            "content_diff_count": sum(1 for r in recs for d in r.get("diffs", []) if d["kind"] == "内容の差") + sum(1 for r in recs if r["change"] in ("新規", "候補に無い（削除扱い）")),
                            "stale_records": [r["title"] for r in recs if (r.get("acquisition") or {}).get("stale")],
                            "source_meta": [s for s in cand["sources"] if s["id"] == H][0].get("acquisition"),
                            "source_note": [s for s in cand["sources"] if s["id"] == H][0].get("snapshot_note")}
        ok_all = (code == 0 and res.get("status") == "ok" and rep["existing_records"]["identical"] and rep["existing_records"]["source_meta_identical"]
                  and rep["hamamatsu"]["content_diff_count"] == 0 and not rep["validation_errors"] and not rep["hamamatsu"]["stale_records"]
                  and rep["hamamatsu"]["source_meta"] == "automatic_http")          # 全工程が成功（要項PDFの取得失敗・staleなし）
    after_bytes = open(data_path, "rb").read()
    rep["public_data_file"] = {"path": "data/tournaments.json", "sha256_before": sha(before_bytes), "sha256_after": sha(after_bytes), "unchanged": before_bytes == after_bytes}
    rep["verdict"] = "候補として採用可（取得・解析・検証・候補JSON生成のすべて成功／既存60件は不変／浜松に内容の差なし）" if ok_all else "不採用または要確認（理由は下の各項目）"
    rep["adoptable"] = ok_all
    with open(os.path.join(out, "dryrun_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
        f.write("\n")
    md = render(rep)
    with open(os.path.join(out, "dryrun_report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print("\n" + md + "\n保存先: " + os.path.abspath(out))
    return 0 if ok_all else 1


def render(r):
    f = r["fetch"]
    L = ["# 浜松 自動更新候補（R5）dry-run の結果", "", "- 実行日時（日本時間）: %s 〜 %s" % (r["started_at_jst"], r["finished_at_jst"]), "- 取得方式: 通常のHTTP取得のみ（ブラウザ不使用）。公開データは書き換えていません",
         "- **判定: %s**" % r["verdict"], "", "## 取得状態", "- 浜松の更新: **%s**%s" % (f.get("status"), ("（失敗の段階: %s／%s）" % (f.get("stage"), f.get("message"))) if f.get("status") != "ok" else "")]
    st = f.get("stats") or {}
    if st:
        L.append("- 要項PDF: 試行 %s件／取得成功 %s件／取得失敗 %s件／文字の取り出し失敗 %s件／取り出し方法: %s" % (st.get("pdf_attempted"), st.get("pdf_fetched"), st.get("pdf_fetch_failed"), st.get("pdf_text_failed"), st.get("pdf_extractor")))
    d = f.get("detail") or {}
    if d:
        L.append("- 取得・解析の内訳: 解析成功 %s件／取得成功・解析未取得 %s件／取得失敗 %s件／今回取得せず %s件／前回成功値を保持（stale）%s件" % (d.get("pdf_parsed"), d.get("pdf_unparsed"), d.get("pdf_fetch_failed"), d.get("pdf_not_attempted"), d.get("stale_records")))
    L += ["", "| 種類 | URL | HTTP | バイト数 | SHA-256 |", "|---|---|---|---|---|"]
    for q in f.get("requests", []):
        L.append("| %s | %s | %s | %s | %s |" % ("pdf" if str(q.get("url")).lower().endswith(".pdf") else q.get("kind"), q.get("url"), q.get("status") if q.get("status") is not None else q.get("error"), q.get("bytes"), (q.get("sha256") or "")[:16]))
    for n in f.get("notes") or []:
        L.append("- 備考: %s" % n)
    if "existing_records" in r:
        e = r["existing_records"]
        L += ["", "## 既存60件（浜松以外）", "- 件数: %d → %d／**1バイトも変わらない: %s**（記録の正規化JSONのSHA-256 前 %s／後 %s）／順序も同一: %s／既存4情報源のメタ情報も同一: %s" % (
            e["count_before"], e["count_after"], "はい" if e["identical"] else "**いいえ**", e["sha256_before"][:16], e["sha256_after"][:16], "はい" if e["order_identical"] else "いいえ", "はい" if e["source_meta_identical"] else "いいえ")]
        h = r["hamamatsu"]
        L += ["", "## 浜松5件の内容差（現在の手動取得データ → 候補）", "- 件数: %d → %d／**内容の差: %d件**（手動→自動の記録の置き換えによる「予期される差」は、内容の差に数えない）／前回成功値を保持（stale）: %s" % (
            h["count_before"], h["count_after"], h["content_diff_count"], "、".join(h["stale_records"]) or "なし"), "- 情報源の取得の記録: %s／%s" % (h["source_meta"], h["source_note"])]
        for rec in h["records"]:
            L.append("")
            L.append("### %s（%s）" % (rec["title"], rec["change"]))
            for dd in rec.get("diffs", []):
                L.append("- [%s] %s: %s → %s" % (dd["kind"], dd["field"], str(dd["old"])[:160], str(dd["new"])[:160]))
        c = r["candidate_json"]
        L += ["", "## 候補JSON", "- ファイル: %s／%d バイト／%d 件／**SHA-256 %s**" % (c["file"], c["bytes"], c["records"], c["sha256"])]
    else:
        L += ["", "## 候補JSON", "- 生成していません（取得・解析・検証のいずれかで失敗）。**現在の65件は、そのまま保持されます**"]
    p = r["public_data_file"]
    L += ["", "## 公開データ（data/tournaments.json）", "- 変更なし: %s（SHA-256 前 %s／後 %s）" % ("はい" if p["unchanged"] else "**いいえ**", p["sha256_before"][:16], p["sha256_after"][:16])]
    if r.get("validation_errors"):
        L += ["", "## 検証エラー"] + ["- " + x for x in r["validation_errors"][:10]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
