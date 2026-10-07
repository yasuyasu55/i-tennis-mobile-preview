"""
TTA-MOBILE-012 大会の分類ルール（対象者区分・種目・参加資格）

方針:
  - 分類はすべて「どの文字列が、どこ（タイトル／一覧表の欄）に書かれていたか」を根拠として記録する。
  - 根拠が無いものは推測しない。対象者が分からなければ「不明」、参加資格が分からなければ「参加資格要確認」。
  - 大会名だけでは、参加できると断定しない。参加資格を確定してよいのは、情報源の一覧表の参加資格欄に明記された場合だけ。
    「オープン参加」と明記されていても、年齢・レベルなどの詳細条件が要項に残る可能性があるため、
    「オープン参加（詳細条件は要項確認）」と表示し、参加できると断定しない（要項の本文は解析していない）。
  - タイトルの語から注意を促す「ヒント」は、確定した参加資格ではない（eligibility_note に書き、状態は「参加資格要確認」のまま）。
"""
import re

AUDIENCE_TYPES = ["一般", "ベテラン", "ジュニア", "小学生", "中学生", "高校生", "学生", "実業団・企業", "教職員", "団体限定", "不明"]
ELIGIBILITY_STATUSES = ["オープン参加（詳細条件は要項確認）", "地域条件あり", "協会登録必要", "年齢条件あり", "団体・所属条件あり", "その他の条件あり", "参加資格要確認"]
EVENT_TYPES = ["シングルス", "ダブルス", "ミックス", "団体戦"]

# 検索画面の「対象者」グループ（画面の絞り込みと同じ定義）
AUDIENCE_GROUP = {
    "一般": "一般", "ベテラン": "ベテラン", "不明": "不明",
    "ジュニア": "ジュニア", "小学生": "ジュニア", "中学生": "ジュニア", "高校生": "ジュニア",
    "学生": "学生",
    "実業団・企業": "実業団・団体", "教職員": "実業団・団体", "団体限定": "実業団・団体",
}

# (対象者, 正規表現, 説明)。上から順にすべて評価し、該当したものを全部保持する（複数可）。
_AUDIENCE_RULES = [
    ("ジュニア", re.compile(r"ジュニア|U\s?(?:10|12|14|15|16|18)(?!\d)|SCHOOL"), "ジュニア系の語（ジュニア／U10〜U18／SCHOOL）"),
    ("小学生", re.compile(r"小学生"), "「小学生」"),
    ("中学生", re.compile(r"中学生"), "「中学生」"),
    ("高校生", re.compile(r"高校生|高校"), "「高校生」または「高校」"),
    ("学生", re.compile(r"大学|(?<![小中高])学生"), "「大学」または「学生」（小学生・中学生・高校生を除く）"),
    ("実業団・企業", re.compile(r"実業団|企業"), "「実業団」または「企業」"),
    ("教職員", re.compile(r"教職員"), "「教職員」"),
    ("団体限定", re.compile(r"団体戦|団体|対抗|チーム"), "団体・対抗戦を示す語（団体／対抗／チーム）"),
    ("ベテラン", re.compile(r"ベテラン|壮年|シニア|ミドル|オーバー\d*|\d+歳以上"), "ベテラン区分を示す語（ベテラン／壮年／シニア等）。年齢の下限は要項で確認"),
    ("一般", re.compile(r"一般|(?<!未)成年"), "一般区分を示す語（一般／成年）"),
]

# 参加資格の「ヒント」（確定ではない）。タイトルの語から、要項で確認すべき点を示す。
_HINT_RULES = [
    (re.compile(r"ジュニア|小学生|中学生|高校生|U\s?(?:10|12|14|15|16|18)(?!\d)"), "年齢・学年による条件がある可能性（要項で確認）"),
    (re.compile(r"ベテラン|壮年|シニア|ミドル|歳以上"), "年齢条件がある可能性（要項で確認）"),
    (re.compile(r"年齢別"), "年齢別の区分があり、年齢条件がある可能性（要項で確認）"),
    (re.compile(r"レディース"), "性別の条件がある可能性（要項で確認）"),
    (re.compile(r"ファミリー"), "家族でのペア参加などの条件がある可能性（要項で確認）"),
    (re.compile(r"団体|対抗|チーム"), "団体・所属単位での参加の可能性（要項で確認）"),
    (re.compile(r"教職員"), "職域の条件がある可能性（要項で確認）"),
    (re.compile(r"実業団"), "所属（企業・団体）の条件がある可能性（要項で確認）"),
    (re.compile(r"ねんりんピック"), "県予選のため年齢条件や代表選考がある可能性（要項で確認）"),
    (re.compile(r"予選"), "予選のため、所属・登録などの出場条件がある可能性（要項で確認）"),
    (re.compile(r"東海大会"), "東海大会のため、出場条件がある可能性（要項で確認）"),
    (re.compile(r"オープン"), "「オープン」とあっても、参加資格は要項での確認が必要です"),
]


def classify_audience(title, extra_texts=None):
    """対象者区分を返す: (types, basis[])。複数該当可。該当なしは ["不明"]。"""
    sources = [("タイトル", title or "")] + list(extra_texts or [])
    types, basis = [], []
    for atype, rx, desc in _AUDIENCE_RULES:
        for where, text in sources:
            m = rx.search(text)
            if m:
                if atype not in types:
                    types.append(atype)
                basis.append({"field": "audience", "value": atype, "matched": m.group(0), "source": where, "rule": desc})
                break
    if not types:
        types = ["不明"]
        basis.append({"field": "audience", "value": "不明", "matched": None, "source": "タイトル",
                      "rule": "対象者を示す語がタイトルに無いため確定しない（推測しない）"})
    return types, basis


def classify_event_types(title, extra_texts=None):
    """種目を返す: (types|None, basis[])。明記が無ければ None（種目不明）。"""
    sources = [("タイトル", title or "")] + list(extra_texts or [])
    types, basis = [], []
    joined = [(w, t) for w, t in sources]

    def find(rx):
        for where, text in joined:
            m = rx.search(text)
            if m:
                return where, m.group(0)
        return None

    mixed = find(re.compile(r"ミックス|混合"))
    if mixed:
        types.append("ミックス")
        basis.append({"field": "event_type", "value": "ミックス", "matched": mixed[1], "source": mixed[0],
                      "rule": ("「ミックス」の語（ミックスはダブルス形式のため「ダブルス」は付けない）" if mixed[1] == "ミックス"
                               else "「混合」の語（混合ダブルスは「ミックス」として扱う。ダブルス形式のため「ダブルス」は付けない）")})
    stripped = [(w, re.sub(r"(?:ミックス|混合)(?:ダブルス)?", "", t)) for w, t in joined]
    for etype, rx in (("シングルス", re.compile(r"シングルス")), ("ダブルス", re.compile(r"ダブルス"))):
        if etype == "ダブルス" and mixed:
            continue
        for where, text in stripped:
            m = rx.search(text)
            if m:
                types.append(etype)
                basis.append({"field": "event_type", "value": etype, "matched": m.group(0), "source": where, "rule": "「%s」の語" % etype})
                break
    team = find(re.compile(r"団体戦|団体|対抗"))
    if team:
        types.append("団体戦")
        basis.append({"field": "event_type", "value": "団体戦", "matched": team[1], "source": team[0], "rule": "団体・対抗戦を示す語"})
    order = {t: i for i, t in enumerate(EVENT_TYPES)}
    types.sort(key=lambda t: order[t])
    return (types or None), basis


def eligibility_hint(title):
    hints = []
    for rx, text in _HINT_RULES:
        m = rx.search(title or "")
        if m and text not in hints:
            hints.append(text)
    return hints


def classify_eligibility_from_table(cell_text):
    """
    一覧表の「参加資格」欄（情報源の公式HTML）に明記された内容から参加資格を確定する。
    戻り値: (status, text, note, basis) 。解釈できなければ参加資格要確認。
    """
    t = (cell_text or "").strip()
    if not t:
        return "参加資格要確認", None, None, []
    def b(status, matched):
        return [{"field": "eligibility", "value": status, "matched": matched, "source": "一覧表の参加資格欄", "rule": "公式の一覧表に明記"}]
    if "（団体）" in t or "(団体)" in t:
        return "団体・所属条件あり", t, "協会員の団体（クラブ）単位での参加です。", b("団体・所属条件あり", "（団体）")
    if "市内在住" in t:
        return "地域条件あり", t, "地域条件: 市内在住・在勤・在学（一覧表の原文: %s）。詳細は要項で確認してください。" % t, b("地域条件あり", "市内在住在勤在学者")
    if "協会員" in t:
        return "協会登録必要", t, "協会員（協会登録が必要）。詳細は要項・協会登録のページで確認してください。", b("協会登録必要", "協会員")
    if "オープン参加" in t:
        return "オープン参加（詳細条件は要項確認）", t, "一覧表の参加資格欄に「オープン参加」と明記されています。年齢・レベルなどの詳細条件は要項で確認してください。", b("オープン参加（詳細条件は要項確認）", "オープン参加")
    return "参加資格要確認", t, "一覧表の参加資格欄の内容を分類できないため、原文を表示します。", []


# ---------------------------------------------------------------------------
# TTA-MOBILE-016: 検索に使う分類の仕上げ（主種目と構成種目の分離・対象者「一般」の確定・参加資格の区分名）
#
# finalize_record() は、記録に保存されている公式原文由来の項目（大会名・種目・対象者・参加資格の原文と区分・分類根拠）だけから決まる。
# 生成スクリプトが全情報源の記録の最後に呼ぶので、自動更新後も同じ分類になる。
# 既存データの再分類（tools/reclassify.py）も、同じ関数を使う（冪等: 何度適用しても同じ結果）。
# ---------------------------------------------------------------------------

# 参加資格が「公式の一覧・要項・サイトの見出し」に明記されて確定した区分（年齢・学年・所属の限定ではない条件）
_CONFIRMED_STATUSES_FOR_GENERAL = ("地域条件あり", "協会登録必要", "オープン参加（詳細条件は要項確認）", "その他の条件あり")
_OFFICIAL_ELIGIBILITY_SOURCES = ("一覧表の参加資格欄", "要項PDFの資格欄", "保存HTMLの見出し")
_LEGACY_STATUS = {"市内在住・在勤・在学": "地域条件あり"}      # v0.7.1 までの区分名


def split_event_types(event_types):
    """
    検出した種目（event_types。表示・原文の保持用）を、検索に使う主種目と、団体戦の構成種目に分ける。
    団体戦は、主種目を「団体戦」だけにする。団体戦を構成する試合形式（シングルス・ダブルス・ミックス等）は構成種目として別に保持し、
    検索の「種目」には使わない（「ミックス」だけの検索に団体戦が出ないようにする）。
    戻り値: (primary_event_types|None, component_match_types[])
    """
    if not event_types:
        return None, []
    ev = [e for e in event_types if e in EVENT_TYPES]
    if "団体戦" in ev:
        return ["団体戦"], [e for e in ev if e != "団体戦"]
    return ev, []


def _official_eligibility_basis(rec):
    for b in rec.get("classification_basis") or []:
        if b.get("field") == "eligibility" and b.get("value") == rec.get("eligibility_status") and b.get("source") in _OFFICIAL_ELIGIBILITY_SOURCES:
            return b
    return None


def finalize_record(rec):
    """記録を、検索に使う分類へ仕上げる（その場で更新して返す）。"""
    # 1) 参加資格の区分名（v0.7.1までの「市内在住・在勤・在学」→「地域条件あり」。原文は変えない）
    old = rec.get("eligibility_status")
    if old in _LEGACY_STATUS:
        rec["eligibility_status"] = _LEGACY_STATUS[old]
        for b in rec.get("classification_basis") or []:
            if b.get("field") == "eligibility" and b.get("value") == old:
                b["value"] = _LEGACY_STATUS[old]
        if not rec.get("eligibility_note"):
            rec["eligibility_note"] = "地域条件: 市内在住・在勤・在学（一覧表の原文: %s）。詳細は要項で確認してください。" % (rec.get("eligibility_text") or "")
    # 2) 主種目と構成種目
    rec["primary_event_types"], rec["component_match_types"] = split_event_types(rec.get("event_types"))
    # 3) 対象者「不明」の使用条件: 公式の一覧・要項に参加資格が明記されている（年齢・学年・所属の限定ではない）のに、
    #    大会名に対象者を示す語が無いだけの大会は、「一般」とする（根拠を記録）。参加資格が確定していない大会は「不明」のまま。
    if rec.get("audience_types") == ["不明"] and rec.get("eligibility_status") in _CONFIRMED_STATUSES_FOR_GENERAL:
        src = _official_eligibility_basis(rec)
        if src is not None:
            rec["audience_types"] = ["一般"]
            basis = [b for b in (rec.get("classification_basis") or []) if not (b.get("field") == "audience" and b.get("value") == "不明")]
            basis.insert(0, {"field": "audience", "value": "一般", "matched": src.get("matched"), "source": src.get("source"),
                             "rule": "参加資格が明記されており（%s）、年齢・学年・所属による限定の表示が無いため「一般」。大会名に対象者を示す語が無いだけでは「不明」にしない（要項に詳細条件が残る可能性は、参加資格の欄で案内）" % rec.get("eligibility_status")})
            rec["classification_basis"] = basis
    return rec
