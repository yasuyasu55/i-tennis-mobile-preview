#!/usr/bin/env python3
"""
共通大会JSON（data/tournaments.json）の検証。自動更新の結果を書き込む前と、CIで必ず実行する。
アプリの画面・既存テストが前提にしている形（必須項目・許可値・URLの安全性・空文字なし・件数の整合）を確認する。
使い方: python3 pipeline/validate.py [path]   （エラーがあれば終了コード1）
"""
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import tournament_classify as tc  # noqa: E402

REQUIRED = ["id", "title", "source_id", "source_name", "source_area", "event_area", "date_text", "periods", "deadline_text", "venue", "event_types",
            "primary_event_types", "component_match_types", "audience_types", "eligibility_status", "eligibility_text", "official_url", "guideline_url", "entry_url", "parse_status",
            "classification_basis", "warnings", "source_snapshot_date"]
EVENT_TYPES = {"シングルス", "ダブルス", "ミックス", "団体戦"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(/\d{4}-\d{2}-\d{2})?$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^((aichi|gamagori|toyokawa|hamamatsu|okazaki|toyota|anjo|kariya)-\d{4}-[0-9a-f]{8}|toyohashi-\d+)$")
EVENT_KINDS = ("failed", "recovered", "data_changed", "removed")
FORBIDDEN = ["誰でも" + "参加可能"]   # 断定表現は使わない（TTA-MOBILE-012-R1）


def _walk_empty(v, path, out):
    if v == "":
        out.append(path)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk_empty(x, "%s[%d]" % (path, i), out)
    elif isinstance(v, dict):
        for k, x in v.items():
            _walk_empty(x, path + "." + k, out)


def validate_records(records, label="records"):
    errs = []
    seen = set()
    for i, t in enumerate(records):
        tag = "%s[%d:%s]" % (label, i, t.get("id") if isinstance(t, dict) else "?")
        if not isinstance(t, dict):
            errs.append(tag + " が辞書ではありません")
            continue
        for f in REQUIRED:
            if f not in t:
                errs.append("%s 必須項目がありません: %s" % (tag, f))
        if t.get("id") in seen:
            errs.append(tag + " idが重複しています")
        if not ID_RE.match(str(t.get("id"))):
            errs.append(tag + " idが安定ID形式（<情報源>-<年度>-<8桁16進> または toyohashi-<投稿ID>）ではありません")
        if not t.get("event_key") or not isinstance(t.get("event_key"), str):
            errs.append(tag + " event_key がありません")
        if t.get("fiscal_year") is not None and not isinstance(t.get("fiscal_year"), int):
            errs.append(tag + " fiscal_year が整数ではありません")
        seen.add(t.get("id"))
        if not isinstance(t.get("title"), str) or not t["title"].strip():
            errs.append(tag + " title が空です")
        aud = t.get("audience_types")
        if not isinstance(aud, list) or not aud or any(a not in tc.AUDIENCE_TYPES for a in aud):
            errs.append(tag + " audience_types が不正です")
        if t.get("eligibility_status") not in tc.ELIGIBILITY_STATUSES:
            errs.append(tag + " eligibility_status が許可値ではありません: %r" % t.get("eligibility_status"))
        et = t.get("event_types")
        if et is not None and (not isinstance(et, list) or not et or any(x not in EVENT_TYPES for x in et)):
            errs.append(tag + " event_types が不正です")
        pe, ce = t.get("primary_event_types"), t.get("component_match_types")
        if pe is not None and (not isinstance(pe, list) or not pe or any(x not in EVENT_TYPES for x in pe)):
            errs.append(tag + " primary_event_types が不正です")
        if not isinstance(ce, list) or any(x not in EVENT_TYPES for x in ce):
            errs.append(tag + " component_match_types が不正です")
        elif isinstance(pe, list) or pe is None:
            ev = t.get("event_types") or []
            if sorted(set((pe or []) + ce)) != sorted(set(ev)):
                errs.append(tag + " 主種目＋構成種目が、検出した種目（event_types）と一致しません")
            if "団体戦" in ev and not (pe == ["団体戦"] and "団体戦" not in ce):
                errs.append(tag + " 団体戦は、主種目を「団体戦」だけにし、構成種目へ分けます")
            if ce and "団体戦" not in (pe or []):
                errs.append(tag + " 構成種目は、団体戦の記録にだけ付けます")
        if t.get("parse_status") not in ("success", "partial"):
            errs.append(tag + " parse_status が不正です")
        for u in ("official_url", "guideline_url", "entry_url"):
            v = t.get(u)
            if v is not None and not (isinstance(v, str) and re.match(r"^https?://", v, re.I)):
                errs.append("%s %s がhttp/httpsではありません" % (tag, u))
        for p in (t.get("periods") or []) + (t.get("reserve_periods") or []):
            n = p.get("normalized")
            if n is not None and not DATE_RE.match(n):
                errs.append("%s 日付の形式が不正です: %r" % (tag, n))
        if t.get("deadline_date") is not None and not ISO_RE.match(str(t["deadline_date"])):
            errs.append(tag + " deadline_date の形式が不正です")
        if not ISO_RE.match(str(t.get("source_snapshot_date") or "")):
            errs.append(tag + " source_snapshot_date が日付ではありません")
        if not isinstance(t.get("classification_basis"), list):
            errs.append(tag + " classification_basis がリストではありません")
    empties = []
    _walk_empty(records, label, empties)
    errs += ["空文字があります（不明値は null）: " + p for p in empties[:5]]
    return errs


def validate(data):
    errs = []
    for k in ("schema_version", "sources", "other_areas", "summary", "tournaments"):
        if k not in data:
            errs.append("トップレベルの項目がありません: " + k)
    if errs:
        return errs
    src = {s.get("id"): s for s in data["sources"]}
    for s in data["sources"]:
        for f in ("id", "name", "event_area", "coverage", "coverage_label", "record_count", "snapshot_date"):
            if f not in s:
                errs.append("sources[%s] に %s がありません" % (s.get("id"), f))
        if not ISO_RE.match(str(s.get("snapshot_date") or "")):
            errs.append("sources[%s] snapshot_date が日付ではありません" % s.get("id"))
    errs += validate_records(data["tournaments"], "tournaments")
    for t in data["tournaments"]:
        s = src.get(t.get("source_id"))
        if not s:
            errs.append("%s: source_id が sources にありません" % t.get("id"))
        elif t.get("event_area") != s.get("event_area"):
            errs.append("%s: event_area が情報源の検索区分と一致しません" % t.get("id"))
    for sid, s in src.items():
        n = sum(1 for t in data["tournaments"] if t.get("source_id") == sid)
        if n != s.get("record_count"):
            errs.append("sources[%s] record_count(%s) が実際の件数(%d)と一致しません" % (sid, s.get("record_count"), n))
    if data["summary"].get("total") != len(data["tournaments"]):
        errs.append("summary.total が実際の件数と一致しません")
    text = json.dumps(data, ensure_ascii=False)
    for ph in FORBIDDEN:
        if ph in text:
            errs.append("使用しない表現が含まれています: " + ph)
    if re.search(r"javascript:|\"data:", text, re.I):
        errs.append("危険なURLスキームが含まれています")
    up = data.get("update")
    if up is not None:
        for sid, u in (up.get("sources") or {}).items():
            if u.get("status") not in ("ok", "stale", "failed"):
                errs.append("update.sources[%s].status が不正です" % sid)
            if u.get("status") in ("ok", "stale") and sid not in src:
                errs.append("update.sources[%s] に対応する情報源がありません" % sid)
    return errs


def validate_state(state_doc, data):
    errs = []
    ids = {t["id"]: t for t in data.get("tournaments", [])}
    for k, v in (state_doc.get("entries") or {}).items():
        if k not in ids:
            errs.append("tournament-state: 現在の一覧にない大会の状態があります: " + k)
        for f in ("last_seen_at", "first_seen_at"):
            if not ISO_RE.match(str(v.get(f) or "")):
                errs.append("tournament-state[%s].%s が日付ではありません" % (k, f))
        if not isinstance(v.get("missed_runs"), int) or not 0 <= v["missed_runs"] < state_doc.get("miss_limit", 3):
            errs.append("tournament-state[%s].missed_runs が不正です（0〜%d）" % (k, state_doc.get("miss_limit", 3) - 1))
    return errs


def validate_history(doc):
    errs = []
    for h in doc.get("entries", []):
        if not ISO_RE.match(str(h.get("removed_at") or "")) or not h.get("id") or not isinstance(h.get("record"), dict):
            errs.append("tournament-history: 不正な項目があります: %s" % h.get("id"))
    return errs


def validate_log(doc):
    errs = []
    ev = doc.get("events")
    if not isinstance(ev, list) or len(ev) > 200:
        return ["update-log: events が不正です"]
    for e in ev:
        if not e.get("at") or not e.get("source") or e.get("event") not in EVENT_KINDS:
            errs.append("update-log: 不正な出来事があります: %s" % e)
    return errs


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "tournaments.json")
    data = json.load(open(path, encoding="utf-8"))
    errs = validate(data)
    base = os.path.dirname(path)
    for name, fn in (("tournament-state.json", lambda d: validate_state(d, data)), ("tournament-history.json", validate_history), ("update-log.json", validate_log)):
        fp = os.path.join(base, name)
        if os.path.exists(fp):
            errs += fn(json.load(open(fp, encoding="utf-8")))
    if errs:
        print("[NG] %s の検証に失敗（%d件）" % (os.path.relpath(path, ROOT), len(errs)))
        for e in errs[:30]:
            print("  -", e)
        sys.exit(1)
    print("[OK] %s は検証に合格" % os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()

