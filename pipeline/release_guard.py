"""Fail closed before automatic publication. No network or writes here."""
import json
from pathlib import Path

EXPECTED_SOURCES = {
    "aichi": "aichi_tennis_association",
    "toyohashi": "toyohashi_tennis_association",
    "gamagori": "gamagori_tennis_association",
    "toyokawa": "toyokawa_tennis_association",
    "hamamatsu": "hamamatsu_tennis_association",
    "okazaki": "okazaki_tennis_association",
    "toyota": "toyota_tennis_association",
    "anjo": "anjo_tennis_association",
    "kariya": "kariya_tennis_association",
}
IMPORTANT = ("venue", "deadline_text", "deadline_date", "eligibility_text",
             "entry_url", "guideline_url", "fee_text", "contact_text")


def check_release(previous, proposed, summary):
    errors = []
    results = summary.get("results", {})
    if summary.get("fatal_error") or summary.get("validation_errors"):
        errors.append("取得・検証エラーがあるため公開不可")
    if set(results) != set(EXPECTED_SOURCES):
        errors.append(f"{len(EXPECTED_SOURCES)}情報源すべての結果が必要")
    if not any(r.get("status") == "ok" for r in results.values()):
        errors.append("正常取得した情報源がない")
    old_rows = previous.get("tournaments", [])
    new_rows = proposed.get("tournaments", [])
    old = {r["id"]: r for r in old_rows}
    new = {r["id"]: r for r in new_rows}
    if len(old) != len(old_rows) or len(new) != len(new_rows):
        errors.append("大会IDの重複")
    if not new or set(old) - set(new):
        errors.append("自動公開の第一段階では既存大会を削除しない")
    allowed_sources = set(EXPECTED_SOURCES.values())
    if any(r.get("source_id") not in allowed_sources for r in new_rows):
        errors.append("未承認の情報源")
    for key, sid in EXPECTED_SOURCES.items():
        result = results.get(key, {})
        status = result.get("status")
        before = [r for r in old_rows if r.get("source_id") == sid]
        after = [r for r in new_rows if r.get("source_id") == sid]
        if status not in ("ok", "failed"):
            errors.append(f"{key}: 想定外の取得状態")
        elif status == "failed" and before != after:
            errors.append(f"{key}: 失敗情報源のデータが変化")
        elif status == "ok" and (not after or result.get("records") != len(after)):
            errors.append(f"{key}: 件数が一致しない")
    for rid in set(old) & set(new):
        a, b = old[rid], new[rid]
        if a.get("source_id") != b.get("source_id"):
            errors.append(f"{rid}: 情報源が変化")
        for field in IMPORTANT:
            if a.get(field) not in (None, "", []) and b.get(field) in (None, "", []):
                errors.append(f"{rid}: {field}の確認済み値が消失")
        if b.get("parse_status") == "partial":
            for field in ("event_types", "audience_types"):
                if not set(a.get(field) or []).issubset(set(b.get(field) or [])):
                    errors.append(f"{rid}: 部分解析で{field}が減少")
    return errors


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("previous")
    parser.add_argument("proposed")
    parser.add_argument("summary")
    args = parser.parse_args()
    docs = [json.loads(Path(p).read_text(encoding="utf-8"))
            for p in (args.previous, args.proposed, args.summary)]
    errors = check_release(*docs)
    if errors:
        raise SystemExit("公開を停止しました:\n" + "\n".join(errors))
    print(f"公開前ガード合格: {len(EXPECTED_SOURCES)}情報源・失敗時保持・重複・消失を確認")


if __name__ == "__main__":
    main()

