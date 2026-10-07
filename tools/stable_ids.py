"""
安定した大会ID（TTA-MOBILE-014-R1）

並び順に依存せず、「情報源・年度・大会固有キー」から決める。
  - 愛知県・蒲郡・豊川: id = <情報源>-<年度>-<キーのSHA-1先頭8桁>、キー = 年度 + 正規化した大会名（NFKC・空白除去・小文字）
      同じ年度に同名の大会が複数ある場合だけ、先頭の開催日（無ければ出現順）をキーに足して区別する
  - 豊橋: 公式の投稿ID（投稿URL）がそのまま固有キー → id = toyohashi-<投稿ID>（従来から変更なし）
年度は、取得したページの見出し（愛知県「YYYY年度」・蒲郡「令和N年度」）または大会の開催日（豊川。4月始まりの年度）から取得する。固定値は持たない。
大会が掲載から消えても、同じ大会が再び載れば同じIDになる（名前が変わった場合は別の大会として扱う）。
"""
import hashlib
import re
import unicodedata

SOURCE_KEY_BY_ID = {"aichi_tennis_association": "aichi", "toyohashi_tennis_association": "toyohashi",
                    "gamagori_tennis_association": "gamagori", "toyokawa_tennis_association": "toyokawa"}
LEGACY_RE = re.compile(r"^(aichi|gamagori|toyokawa)-(\d{4})-(\d{2})$")


def norm_title(t):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", t or "")).lower()


def fiscal_year_of(iso_date):
    """4月始まりの年度。'2027-03-14' → 2026"""
    y, m = int(iso_date[:4]), int(iso_date[5:7])
    return y if m >= 4 else y - 1


def _digest(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def assign_ids(src_key, recs, fiscal_years):
    """recs の各レコードに id / fiscal_year / event_key を設定する（順序に依存しない）。"""
    if len(recs) != len(fiscal_years):
        raise ValueError("年度の数が合いません")
    groups = {}
    for r, fy in zip(recs, fiscal_years):
        base = "%d|%s" % (fy, norm_title(r.get("title")))
        r["fiscal_year"] = fy
        groups.setdefault(base, []).append(r)
    for base, members in groups.items():
        if len(members) == 1:
            members[0]["event_key"] = base
            continue
        for n, r in enumerate(members, 1):
            d = (r.get("periods") or [{}])[0].get("normalized") if r.get("periods") else None
            r["event_key"] = base + "|" + (d or "#%d" % n)
        seen = {}
        for n, r in enumerate(members, 1):          # 日付でも区別できない場合は出現順
            if r["event_key"] in seen:
                r["event_key"] += "#%d" % n
            seen[r["event_key"]] = True
    ids = set()
    for r in recs:
        r["id"] = "%s-%d-%s" % (src_key, r["fiscal_year"], _digest(r["event_key"]))
        if r["id"] in ids:
            raise ValueError("idが重複しました: " + r["id"])
        ids.add(r["id"])


def migrate_data(data):
    """
    従来の並び順ID（aichi-2026-07 など）のデータを、安定IDへ移行する。
    戻り値: (新データ, {旧ID: 新ID})。すでに移行済み（event_keyあり）のレコードは変更しない。
    """
    mapping, out = {}, dict(data)
    by_src = {}
    for t in data["tournaments"]:
        by_src.setdefault(t["source_id"], []).append(dict(t))
    migrated = {}
    for sid, group in by_src.items():
        key = SOURCE_KEY_BY_ID.get(sid)
        if key is None or all(g.get("event_key") for g in group):
            migrated[sid] = group
            continue
        if key == "toyohashi":
            for g in group:
                g["fiscal_year"] = None
                g["event_key"] = "post:" + g["id"].split("-", 1)[1]
                mapping[g["id"]] = g["id"]
            migrated[sid] = group
            continue
        legacy = [g["id"] for g in group]
        years = []
        for g in group:
            m = LEGACY_RE.match(g["id"])
            if not m or m.group(1) != key:
                raise ValueError("旧形式のIDではありません: " + g["id"])
            years.append(int(m.group(2)))
        assign_ids(key, group, years)
        for old, g in zip(legacy, group):
            mapping[old] = g["id"]
        migrated[sid] = group
    order = []
    # 元の並びを保つ（情報源ごとの順序は変えない）
    idx = {sid: 0 for sid in migrated}
    for t in data["tournaments"]:
        order.append(migrated[t["source_id"]][idx[t["source_id"]]])
        idx[t["source_id"]] += 1
    out["tournaments"] = [_ordered(t) for t in order]
    out["schema_version"] = 3
    return out, mapping


def _ordered(t):
    """id・fiscal_year・event_key を先頭に置く（読みやすさのため。値は変えない）。"""
    head = ["id", "fiscal_year", "event_key"]
    d = {k: t[k] for k in head if k in t}
    d.update({k: v for k, v in t.items() if k not in head})
    return d
