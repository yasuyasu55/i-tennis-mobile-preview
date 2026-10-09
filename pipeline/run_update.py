#!/usr/bin/env python3
"""
大会情報の自動更新（開発側の定期処理。GitHub Actions 等から実行する）

流れ（情報源ごとに独立。1つが失敗しても、他の情報源の処理は続ける）
  1. 取得計画に従い、公式ページを礼儀正しく取得（robots.txt確認・間隔・上限）
  2. 情報源別パーサーで共通形式へ変換（年度は実ページの見出し・開催日から取得。取得できなければ、その情報源は失敗）
  3. 点検（件数・必須項目）。不合格なら採用しない
  4. 正常に取得できたときだけ、前回の一覧と照合する
       - 見つかった大会: 更新（last_seen_at を更新、未検出回数を0へ）
       - 今回見つからなかった大会: 即削除せず、現在の一覧に残し、連続未検出回数を+1。3回連続で見つからなければ、現在の一覧から外して履歴へ
  5. 失敗した情報源は、前回成功データを保持して「stale」と記録（未検出回数は数えない）
  6. 全体を検証してから書き込む（検証に失敗したら何も書かない）
  7. 出来事（失敗・復旧・データ変更・削除）を data/update-log.json に追記

出力
  data/tournaments.json          現在の一覧（アプリが読む）
  data/tournament-state.json     大会ごとの last_seen_at・first_seen_at・連続未検出回数
  data/tournament-history.json   一覧から外れた大会の履歴（12か月保持）
  data/update-log.json           出来事の記録
  <out-dir>/proposed/            上記の提案データ（--dry-run でも必ず出力。手動確認・コミット用の成果物）

モバイルアプリは公式サイトへアクセスしない。アプリが読むのは data/tournaments.json だけ。

使い方
  python3 pipeline/run_update.py [--dry-run] [--only aichi,toyokawa] [--out-dir pipeline/out]
終了コード: 0=正常（一部の情報源が失敗していても0。記録に残る） / 2=最終検証に失敗（何も書かない） / 4=有効な情報源がすべて失敗 / 5=予期しない例外
0以外はすべて、GitHub Actionsのジョブを失敗にする。
"""
import argparse
import datetime
import json
import os
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in ("tools", "data-source", "pipeline"):
    sys.path.insert(0, os.path.join(ROOT, p))

import build_tournaments_json as bt   # noqa: E402
import pdftext                        # noqa: E402
import plans                          # noqa: E402
import stable_ids                     # noqa: E402
import validate                       # noqa: E402
from fetcher import FetchError, PoliteFetcher, default_http   # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))
DATA = os.path.join(ROOT, "data")
FILES = {"tournaments": "tournaments.json", "log": "update-log.json", "state": "tournament-state.json", "history": "tournament-history.json"}
MAX_EVENTS = 200
MISS_LIMIT = 3          # 正常取得で3回連続して見つからなければ、現在の一覧から外す
HISTORY_MONTHS = 12     # 履歴の保持期間
POLICY = "公式サイトの取得は開発側の定期処理だけで行う。アプリは公式サイトへ直接アクセスせず、同梱のJSONだけを読む。"


def _norm(r):
    r2 = dict(r)
    r2.pop("source_snapshot_date", None)
    r2["notes"] = [n for n in r.get("notes", []) if not n.startswith(("要項PDFの確認日", "自動取得（通常のHTTP取得）"))]        # 取得日だけの違いでは、更新としない
    return r2


def records_equal(a, b):
    return [_norm(x) for x in a] == [_norm(x) for x in b]


def months_before(date_str, months):
    d = datetime.date.fromisoformat(date_str)
    y, m = d.year, d.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    day = d.day
    while True:
        try:
            return datetime.date(y, m, day).isoformat()
        except ValueError:
            day -= 1


def sanity(cfg, new, prev):
    problems = []
    if len(new) < cfg.get("min_records", 1):
        problems.append("件数が少なすぎます（%d件。最小 %d件）" % (len(new), cfg.get("min_records", 1)))
    if prev and len(new) < len(prev) * cfg.get("min_ratio", 0.5):
        problems.append("前回（%d件）から大きく減りました（%d件）。ページの構造変化・取得の不完全の可能性" % (len(prev), len(new)))
    problems += validate.validate_records(new, cfg["key"])[:3]
    return problems


_OPTIONAL_DETAIL_FIELDS = (
    "fee_text", "contact_text", "entry_text", "entry_url", "guideline_url",
    "reserve_text", "reserve_periods",
)


def _is_empty_detail(value):
    return value is None or value == "" or value == []


def preserve_unread_optional_fields(previous, candidate):
    """Keep known optional details when this successful parse cannot read them."""
    merged = dict(candidate)
    retained = []

    # Keep the display text and normalized deadline values as one group.
    if _is_empty_detail(merged.get("deadline_text")) and not _is_empty_detail(previous.get("deadline_text")):
        for field in ("deadline_text", "deadline_date", "deadline_time"):
            if field in previous:
                merged[field] = previous[field]
                if not _is_empty_detail(previous[field]):
                    retained.append(field)

    # Eligibility status and note are derived from eligibility_text.
    if _is_empty_detail(merged.get("eligibility_text")) and not _is_empty_detail(previous.get("eligibility_text")):
        for field in ("eligibility_text", "eligibility_status", "eligibility_note"):
            if field in previous:
                merged[field] = previous[field]
                if not _is_empty_detail(previous[field]):
                    retained.append(field)
        old_basis = list(previous.get("classification_basis") or [])
        new_basis = list(merged.get("classification_basis") or [])
        old_eligibility = [item for item in old_basis if item.get("field") == "eligibility"]
        if old_eligibility:
            merged["classification_basis"] = [
                item for item in new_basis if item.get("field") != "eligibility"
            ] + old_eligibility
            retained.append("classification_basis.eligibility")

    # Preserve other optional details individually.
    for field in _OPTIONAL_DETAIL_FIELDS:
        old_value = previous.get(field)
        new_value = merged.get(field)
        if _is_empty_detail(new_value) and not _is_empty_detail(old_value):
            merged[field] = old_value
            retained.append(field)

    # 豊川の一覧ページには会場・詳細種目・参加資格がなく、要項PDFが今runで
    # 一意に対応しない場合は parse_status=partial になる。タイトルだけから
    # 作った分類で、前回PDFから確認済みの値を上書きしない。
    if (candidate.get("source_id") == "toyokawa_tennis_association"
            and candidate.get("parse_status") == "partial"):
        old_basis = list(previous.get("classification_basis") or [])
        pdf_fields = {item.get("field") for item in old_basis
                      if "要項PDF" in str(item.get("source") or "")}
        field_groups = {
            "venue": ("venue",),
            "event_type": ("event_types", "primary_event_types", "component_match_types"),
            "audience": ("audience_types",),
            "eligibility": ("eligibility_text", "eligibility_status", "eligibility_note"),
        }
        for basis_field, record_fields in field_groups.items():
            has_pdf_evidence = basis_field in pdf_fields
            if basis_field == "venue" and not _is_empty_detail(previous.get("venue")) and _is_empty_detail(candidate.get("venue")):
                # 会場は classification_basis に別フィールドとして記録されない。
                has_pdf_evidence = True
            if not has_pdf_evidence:
                continue
            for field in record_fields:
                old_value = previous.get(field)
                if not _is_empty_detail(old_value):
                    merged[field] = old_value
                    retained.append(field)
            if basis_field in ("event_type", "audience", "eligibility"):
                merged["classification_basis"] = [
                    item for item in merged.get("classification_basis", [])
                    if item.get("field") != basis_field
                ] + [item for item in old_basis
                     if item.get("field") == basis_field
                     and "要項PDF" in str(item.get("source") or "")]
                retained.append("classification_basis." + basis_field)

        # PDF未対応時は、前回確認した申込期間の年根拠も消さない。
        if ("申込期間" in str(previous.get("year_basis") or "")
                and "申込期間" not in str(merged.get("year_basis") or "")):
            merged["year_basis"] = previous["year_basis"]
            retained.append("year_basis.申込期間")

        # PDF由来の確認注記は消さず、今回再確認していないことを明記する。
        old_pdf_notes = [n for n in previous.get("notes", [])
                         if n.startswith(("要項PDFの確認日:", "申込期間:", "前回確認値（今回未確認）:"))]
        if old_pdf_notes:
            notes = list(merged.get("notes") or [])
            for note in old_pdf_notes:
                stale_note = note if note.startswith("前回確認値（今回未確認）:") else "前回確認値（今回未確認）: " + note
                if stale_note not in notes:
                    notes.append(stale_note)
            merged["notes"] = notes

    # 岡崎の一覧は要項・申込ページをまだ解析していない。前回の郵送締切等の
    # 手確認メモを消さず、今回再確認していない注記として残す。
    if (candidate.get("source_id") == "okazaki_tennis_association"
            and candidate.get("parse_status") == "partial"):
        old_notes = [n for n in previous.get("notes", [])
                     if "郵送締切" in n or "申込締切" in n]
        if old_notes:
            notes = list(merged.get("notes") or [])
            for note in old_notes:
                retained_note = note if note.startswith("前回確認事項（今回未確認）:") else "前回確認事項（今回未確認）: " + note
                if retained_note not in notes:
                    notes.append(retained_note)
                    retained.append("notes.previous_deadline_note")
            merged["notes"] = notes
    if retained:
        notes = list(merged.get("notes") or [])
        marker = "今回未取得のため前回値を保持: " + ", ".join(retained)
        if marker not in notes:
            notes.append(marker)
        merged["notes"] = notes
    return merged

def _fail(stage, message):
    return {"ok": False, "stage": stage, "message": message}


def fetch_and_build(cfg, registry, fetcher, today, now_iso, snap_date, pdf_confirmed, pdf_to_text, prev_recs):
    key = cfg["key"]
    plan = None
    try:
        plan = plans.PLANS[key](cfg, registry, fetcher, today, pdf_to_text)
    except FetchError as e:
        return _fail("fetch:" + e.stage, "%s（%s）" % (e.message, e.url)), None
    except Exception as e:       # 取得計画の予期しない例外も、その情報源だけの失敗
        return _fail("plan", "予期しないエラー: %s: %s" % (type(e).__name__, e)), None
    bt.PROVIDER = dict(plan.inputs)
    bt.PREVIOUS_RECORDS = list(prev_recs or [])
    bt.configure(snapshot_dates={key: snap_date}, pdf_confirmed_on=pdf_confirmed, retrieved_at=now_iso)
    try:
        out = bt.build_source(key)
    except bt.BuildError as e:
        return _fail("parse", str(e)), plan
    except Exception as e:       # パーサーの予期しない例外も、その情報源だけの失敗
        return _fail("parse", "予期しないエラー: %s: %s" % (type(e).__name__, e)), plan
    finally:
        bt.PROVIDER = None
        bt.PREVIOUS_RECORDS = []
    problems = sanity(cfg, out["records"], prev_recs)
    if problems:
        return _fail("sanity", "；".join(problems)), plan
    return {"ok": True, "out": out}, plan


def merge_source(prev_recs, new_recs, state, history, sid, today, events):
    """正常取得したときの、前回の一覧との照合。戻り値: 新しい一覧（見つかった大会＋まだ外さない大会）。"""
    previous_by_id = {r["id"]: r for r in prev_recs}
    merged = [preserve_unread_optional_fields(previous_by_id[r["id"]], r)
              if r["id"] in previous_by_id else r for r in new_recs]
    new_ids = {r["id"] for r in merged}
    for r in merged:
        old = state.get(r["id"]) or {}
        state[r["id"]] = {"source_id": sid, "first_seen_at": old.get("first_seen_at") or today, "last_seen_at": today, "missed_runs": 0}
    for p in prev_recs:
        if p["id"] in new_ids:
            continue
        st = state.get(p["id"]) or {"source_id": sid, "first_seen_at": p.get("source_snapshot_date"), "last_seen_at": p.get("source_snapshot_date"), "missed_runs": 0}
        st["missed_runs"] = int(st.get("missed_runs", 0)) + 1
        if st["missed_runs"] >= MISS_LIMIT:
            history.append({"id": p["id"], "source_id": sid, "title": p.get("title"), "removed_at": today, "last_seen_at": st.get("last_seen_at"),
                            "missed_runs": st["missed_runs"], "reason": "正常取得で%d回連続して見つからなかった" % MISS_LIMIT, "record": p})
            state.pop(p["id"], None)
            events.append({"event": "removed", "source": sid, "stage": None, "records_before": len(prev_recs), "records_after": None,
                           "summary": "「%s」が%d回連続で見つからないため、現在の一覧から外しました（履歴へ）" % (p.get("title"), MISS_LIMIT)})
        else:
            merged.append(p)
            state[p["id"]] = st
    return merged


def run(registry, prev_data, prev_log, fetcher, now, only=None, snapshot_overrides=None, pdf_to_text=pdftext.pdf_to_text, prev_state=None, prev_history=None):
    now = now.astimezone(JST)
    today, now_iso = now.date().isoformat(), now.isoformat(timespec="seconds")
    snapshot_overrides = snapshot_overrides or {}
    prev_data = prev_data or {}
    migrated = False
    if prev_data.get("tournaments") and any(not t.get("event_key") for t in prev_data["tournaments"]):
        prev_data, _mapping = stable_ids.migrate_data(prev_data)     # 旧形式（並び順ID）のデータを、安定IDへ移行して使う
        migrated = True
    state = json.loads(json.dumps((prev_state or {}).get("entries") or {}))
    history = json.loads(json.dumps((prev_history or {}).get("entries") or []))
    prev_recs_by = {}
    for t in prev_data.get("tournaments", []):
        prev_recs_by.setdefault(t.get("source_id"), []).append(t)
    prev_meta = {s["id"]: s for s in prev_data.get("sources", [])}
    prev_upd = ((prev_data.get("update") or {}).get("sources")) or {}

    parts, upd, events, results, raw_out = {}, {}, [], {}, {}
    enabled = [c for c in registry["sources"] if c.get("enabled", True) and (not only or c["key"] in only)]
    for cfg in registry["sources"]:
        key = cfg["key"]
        sid = bt.SOURCE_DEFS[key]["id"]
        pr, pm, pu = prev_recs_by.get(sid, []), prev_meta.get(sid), prev_upd.get(sid) or {}
        if cfg not in enabled:   # 今回は対象外: 前回のまま
            if pm:
                parts[key] = {"records": pr, "meta": pm}
            if pu:
                upd[sid] = pu
            continue
        snap = snapshot_overrides.get(key, today)
        res, plan = fetch_and_build(cfg, registry, fetcher, today, now_iso, snap, snapshot_overrides.get("_pdf", today), pdf_to_text, pr)
        if plan:
            raw_out.update(plan.inputs)
            raw_out.update(plan.raw_files)
        prev_status = pu.get("status")
        ev_here = []
        if res["ok"]:
            snap_state, snap_hist = json.loads(json.dumps(state)), json.loads(json.dumps(history))
            try:
                out = res["out"]
                merged = merge_source(pr, out["records"], state, history, sid, today, ev_here)
                if pr and records_equal(merged, pr) and not ev_here:
                    parts[key] = {"records": pr, "meta": pm}          # 内容が同じ: 前回データを保持（取得日だけの更新はしない）
                    as_of, changed = pu.get("data_as_of") or pm["snapshot_date"], False
                else:
                    meta = dict(out["meta"])
                    meta["record_count"] = len(merged)
                    parts[key] = {"records": merged, "meta": meta}
                    as_of, changed = snap, True
                    events.append({"at": now_iso, "source": sid, "event": "data_changed", "stage": None,
                                   "summary": "大会情報が更新されました（%d件 → %d件）" % (len(pr), len(merged)),
                                   "records_before": len(pr), "records_after": len(merged)})
                for e in ev_here:
                    e["at"], e["records_after"] = now_iso, len(parts[key]["records"])
                    events.append(e)
                if prev_status in ("stale", "failed"):
                    events.append({"at": now_iso, "source": sid, "event": "recovered", "stage": None,
                                   "summary": "更新に成功しました（%d回連続の失敗から復旧）" % pu.get("consecutive_failures", 0),
                                   "records_before": len(pr), "records_after": len(parts[key]["records"])})
                upd[sid] = {"status": "ok", "data_as_of": as_of, "consecutive_failures": 0, "last_failure": pu.get("last_failure")}
                results[key] = {"status": "ok", "changed": changed, "records": len(parts[key]["records"]), "removed": len([e for e in ev_here if e["event"] == "removed"]),
                                "notes": (plan.notes if plan else []) + out["warnings"], "stats": (plan.stats if plan else {}), "detail": (out.get("raw") or {}).get("pdf_summary")}
                continue
            except Exception as e:     # 照合処理の予期しない例外も、その情報源だけの失敗として扱う（状態は変えない）
                res = _fail("merge", "予期しないエラー: %s: %s" % (type(e).__name__, e))
                state, history = snap_state, snap_hist          # 照合の途中で失敗したときは、状態・履歴を元に戻す
        fails = (pu.get("consecutive_failures") or 0) + 1
        lf = {"date": today, "stage": res["stage"], "summary": res["message"][:300]}
        if pr and pm:
            parts[key] = {"records": pr, "meta": pm}          # 前回成功データを保持
            upd[sid] = {"status": "stale", "data_as_of": pu.get("data_as_of") or pm["snapshot_date"], "consecutive_failures": fails, "last_failure": lf}
        else:
            upd[sid] = {"status": "failed", "data_as_of": None, "consecutive_failures": fails, "last_failure": lf}
        events.append({"at": now_iso, "source": sid, "event": "failed", "stage": res["stage"], "summary": res["message"][:300],
                       "records_before": len(pr), "records_after": len(pr)})
        results[key] = {"status": "failed", "stage": res["stage"], "message": res["message"], "kept_records": len(pr), "notes": plan.notes if plan else []}

    ordered = [parts[k] for k in bt.SOURCE_ORDER if k in parts]
    data = bt.assemble(ordered)
    data["update"] = {"schema_version": 1, "policy": POLICY, "sources": upd}
    cutoff = months_before(today, HISTORY_MONTHS)
    history = [h for h in history if h.get("removed_at", "") >= cutoff]       # 12か月より古い履歴は削除
    live_ids = {t["id"] for t in data["tournaments"]}
    state = {k: v for k, v in state.items() if k in live_ids}                 # 一覧にない大会の状態は持たない
    state_doc = {"schema_version": 1, "miss_limit": MISS_LIMIT, "entries": state}
    history_doc = {"schema_version": 1, "retention_months": HISTORY_MONTHS, "entries": history}
    errs = validate.validate(data) + validate.validate_state(state_doc, data) + validate.validate_history(history_doc)
    log = {"schema_version": 1, "events": ((prev_log or {}).get("events") or []) + events}
    log["events"] = log["events"][-MAX_EVENTS:]
    summary = {"run_at": now_iso, "today": today, "results": results, "requests": fetcher.count, "request_log": fetcher.log, "events": events,
               "validation_errors": errs, "migrated_legacy_ids": migrated}
    code = 0
    if errs:
        code = 2
    elif enabled and all(results[c["key"]]["status"] == "failed" for c in enabled):
        code = 4
    return {"data": None if errs else data, "log": log, "state": state_doc, "history": history_doc, "events": events, "summary": summary,
            "exit_code": code, "raw": raw_out}


def _dumps(o):
    return json.dumps(o, ensure_ascii=False, indent=1) + "\n"


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _summary_md(s):
    lines = ["## 大会情報の自動更新（%s）" % s["run_at"], "", "| 情報源 | 結果 | 件数 | 備考 |", "|---|---|---|---|"]
    for k, r in s["results"].items():
        if r["status"] == "ok":
            lines.append("| %s | 成功（%s） | %d | %s |" % (k, "更新あり" if r["changed"] else "変更なし", r["records"], " / ".join(r["notes"]) or "-"))
        else:
            lines.append("| %s | **失敗**（%s） | 前回 %d件を保持 | %s |" % (k, r["stage"], r["kept_records"], r["message"]))
    removed = [e for e in s["events"] if e["event"] == "removed"]
    if removed:
        lines += ["", "**一覧から外した大会（3回連続で見つからなかった）**"] + ["- " + e["summary"] for e in removed]
    lines += ["", "リクエスト数: %d（robots.txt を含む）" % s["requests"]]
    if s.get("migrated_legacy_ids"):
        lines += ["", "旧形式（並び順）の大会IDを、安定IDへ移行して処理しました。"]
    if s["validation_errors"]:
        lines += ["", "**最終検証に失敗したため、何も書き込んでいません。**"] + ["- " + e for e in s["validation_errors"][:10]]
    return "\n".join(lines) + "\n"


def main(argv=None, runner=None, data_dir=DATA):
    """戻り値は終了コード。予期しない例外は握りつぶさず、記録して終了コード5にする。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "pipeline", "out"))
    ap.add_argument("--registry", default=os.path.join(ROOT, "pipeline", "sources.json"))
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.out_dir, "raw"), exist_ok=True)
    try:
        registry = _load(a.registry, None)
        paths = {k: os.path.join(data_dir, v) for k, v in FILES.items()}
        prev = _load(paths["tournaments"], {})
        fetcher = PoliteFetcher(registry["user_agent"], registry["request_delay_seconds"], registry["timeout_seconds"], registry["max_requests_per_run"], default_http)
        r = (runner or run)(registry, prev, _load(paths["log"], {}), fetcher, datetime.datetime.now(JST), only=[x for x in a.only.split(",") if x] or None,
                            prev_state=_load(paths["state"], {}), prev_history=_load(paths["history"], {}))
        with open(os.path.join(a.out_dir, "run-summary.json"), "w", encoding="utf-8") as f:
            f.write(_dumps(r["summary"]))
        with open(os.path.join(a.out_dir, "summary.md"), "w", encoding="utf-8") as f:
            f.write(_summary_md(r["summary"]))
        for name, body in r["raw"].items():
            with open(os.path.join(a.out_dir, "raw", name), "wb") as f:
                f.write(body)
        print(_summary_md(r["summary"]))
        if r["data"] is not None:
            docs = {"tournaments": r["data"], "log": r["log"], "state": r["state"], "history": r["history"]}
            os.makedirs(os.path.join(a.out_dir, "proposed"), exist_ok=True)
            for k, doc in docs.items():                         # 提案データ: dry-run でも必ず出力（確認用・コミット用の成果物）
                with open(os.path.join(a.out_dir, "proposed", FILES[k]), "w", encoding="utf-8", newline="\n") as f:
                    f.write(_dumps(doc))
            if not a.dry_run:
                for k, doc in docs.items():
                    text = _dumps(doc)
                    if not os.path.exists(paths[k]) or open(paths[k], encoding="utf-8").read() != text:
                        with open(paths[k], "w", encoding="utf-8", newline="\n") as f:
                            f.write(text)
        return r["exit_code"]
    except Exception as e:
        tb = traceback.format_exc()
        with open(os.path.join(a.out_dir, "run-summary.json"), "w", encoding="utf-8") as f:
            f.write(_dumps({"fatal_error": "%s: %s" % (type(e).__name__, e), "traceback": tb[-3000:]}))
        with open(os.path.join(a.out_dir, "summary.md"), "w", encoding="utf-8") as f:
            f.write("## 大会情報の自動更新: 予期しない例外で停止しました\n\n`%s: %s`\n\nデータは書き込んでいません。traceback は run-summary.json にあります。\n" % (type(e).__name__, e))
        print("予期しない例外で停止しました:", type(e).__name__, e, file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
