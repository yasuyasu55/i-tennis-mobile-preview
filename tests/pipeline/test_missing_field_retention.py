import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import run_update as ru


class MissingFieldRetentionTests(unittest.TestCase):
    def test_missing_deadline_and_eligibility_keep_grouped_previous_values(self):
        previous = {
            "id": "event-1",
            "deadline_text": "10月26日", "deadline_date": "2026-10-26", "deadline_time": "18:00",
            "eligibility_text": "市内在住・在勤者", "eligibility_status": "地域条件あり",
            "eligibility_note": "市内在住・在勤者に限る",
            "classification_basis": [
                {"field": "audience", "value": "一般", "matched": "一般"},
                {"field": "eligibility", "value": "地域条件あり", "matched": "市内在住・在勤者"},
            ],
            "entry_url": "https://example.test/apply", "reserve_text": "12/6",
            "reserve_periods": [{"raw": "12/6"}], "notes": ["old note"],
        }
        candidate = {
            "id": "event-1",
            "deadline_text": None, "deadline_date": None, "deadline_time": None,
            "eligibility_text": None, "eligibility_status": "参加資格要確認",
            "eligibility_note": "参加資格を確認できません",
            "classification_basis": [
                {"field": "event_type", "value": "シングルス", "matched": "シングルス"},
                {"field": "eligibility", "value": "参加資格要確認", "matched": None},
            ],
            "entry_url": None, "reserve_text": None, "reserve_periods": [],
            "notes": [],
        }
        result = ru.preserve_unread_optional_fields(previous, candidate)
        for field in ("deadline_text", "deadline_date", "deadline_time", "eligibility_text",
                      "eligibility_status", "eligibility_note", "entry_url", "reserve_text", "reserve_periods"):
            self.assertEqual(result[field], previous[field], field)
        self.assertIn({"field": "event_type", "value": "シングルス", "matched": "シングルス"},
                      result["classification_basis"])
        self.assertIn(previous["classification_basis"][1], result["classification_basis"])
        self.assertNotIn({"field": "eligibility", "value": "参加資格要確認", "matched": None},
                         result["classification_basis"])
        self.assertTrue(any("前回値を保持" in note for note in result["notes"]))

    def test_first_run_empty_values_remain_empty(self):
        candidate = {"id": "event-2", "deadline_text": None, "eligibility_text": None, "entry_url": None}
        self.assertEqual(ru.preserve_unread_optional_fields({}, candidate), candidate)

    def test_nonempty_new_values_replace_old_values(self):
        result = ru.preserve_unread_optional_fields(
            {"entry_url": "https://example.test/old", "deadline_text": "10月26日",
             "deadline_date": "2026-10-26", "eligibility_text": "旧条件",
             "eligibility_status": "地域条件あり"},
            {"entry_url": "https://example.test/new", "deadline_text": "11月1日",
             "deadline_date": "2026-11-01", "deadline_time": "17:00",
             "eligibility_text": "新条件", "eligibility_status": "協会登録必要"},
        )
        self.assertEqual(result["entry_url"], "https://example.test/new")
        self.assertEqual(result["deadline_date"], "2026-11-01")
        self.assertEqual(result["eligibility_text"], "新条件")
        self.assertEqual(result["eligibility_status"], "協会登録必要")

    def test_new_unparsed_deadline_does_not_reuse_old_normalized_date(self):
        result = ru.preserve_unread_optional_fields(
            {"deadline_text": "10月26日", "deadline_date": "2026-10-26", "deadline_time": "18:00"},
            {"deadline_text": "日程調整中", "deadline_date": None, "deadline_time": None},
        )
        self.assertEqual(result["deadline_text"], "日程調整中")
        self.assertIsNone(result["deadline_date"])
        self.assertIsNone(result["deadline_time"])

    def test_toyokawa_partial_pdf_run_keeps_previous_pdf_verified_fields(self):
        previous = {
            "id": "toyokawa-1", "source_id": "toyokawa_tennis_association",
            "parse_status": "success", "venue": "豊川公園庭球場",
            "event_types": ["ダブルス", "ミックス"],
            "primary_event_types": ["ダブルス"], "component_match_types": ["ダブルス", "ミックス"],
            "audience_types": ["一般"], "eligibility_text": "オープン参加（レベル条件あり）",
            "eligibility_status": "その他の条件あり", "eligibility_note": "PDF確認済みの条件",
            "deadline_text": "10月25日", "deadline_date": "2026-10-25", "deadline_time": "17:00",
            "classification_basis": [
                {"field": "venue", "value": "豊川公園庭球場", "source": "要項PDFの会場欄"},
                {"field": "event_type", "value": "ミックス", "source": "要項PDFの種目欄"},
                {"field": "audience", "value": "一般", "source": "要項PDFの資格欄"},
                {"field": "eligibility", "value": "その他の条件あり", "source": "要項PDFの資格欄"},
            ],
            "year_basis": "見出しから開催年を取得／申込期間は2026年のPDFを使用",
            "notes": ["要項PDFの確認日: 2026-10-06", "申込期間: 2026-10-04〜2026-10-25"],
        }
        candidate = {
            "id": "toyokawa-1", "source_id": "toyokawa_tennis_association",
            "parse_status": "partial", "venue": None,
            "event_types": ["ダブルス"], "primary_event_types": ["ダブルス"],
            "component_match_types": ["ダブルス"], "audience_types": ["不明"],
            "eligibility_text": None, "eligibility_status": "参加資格要確認",
            "eligibility_note": "未取得", "deadline_text": None, "deadline_date": None,
            "deadline_time": None,
            "classification_basis": [
                {"field": "event_type", "value": "ダブルス", "source": "タイトル"},
                {"field": "audience", "value": "不明", "source": "タイトル"},
            ], "year_basis": "見出しから開催年を取得", "notes": [],
        }

        result = ru.preserve_unread_optional_fields(previous, candidate)

        for field in ("venue", "event_types", "primary_event_types", "component_match_types",
                      "audience_types", "eligibility_text", "eligibility_status", "eligibility_note",
                      "deadline_text", "deadline_date", "deadline_time", "year_basis"):
            self.assertEqual(result[field], previous[field], field)
        self.assertEqual(result["parse_status"], "partial")
        self.assertTrue(any("前回確認値（今回未確認）" in note for note in result["notes"]))
        self.assertTrue(any("classification_basis.event_type" in note for note in result["notes"]))

    def test_okazaki_keeps_prior_deadline_note_with_unconfirmed_label(self):
        previous = {"source_id": "okazaki_tennis_association", "notes": [
            "公式要項の郵送締切は10月30日（金）。上記はインターネット申込の締切です。"]}
        candidate = {"source_id": "okazaki_tennis_association", "parse_status": "partial", "notes": []}
        result = ru.preserve_unread_optional_fields(previous, candidate)
        self.assertIn("前回確認事項（今回未確認）: 公式要項の郵送締切は10月30日（金）。上記はインターネット申込の締切です。",
                      result["notes"])

    def test_repeated_unread_deadline_note_does_not_stack_prefixes(self):
        candidate = {"source_id": "okazaki_tennis_association", "parse_status": "partial", "notes": []}
        previous = {"notes": ["公式要項の郵送締切は10月30日。"]}
        once = ru.preserve_unread_optional_fields(previous, candidate)
        twice = ru.preserve_unread_optional_fields(once, candidate)
        self.assertEqual(once["notes"], twice["notes"])

    def test_successful_pdf_parse_replaces_previous_classification(self):
        previous = {"source_id": "toyokawa_tennis_association", "event_types": ["ダブルス", "ミックス"],
                    "venue": "旧会場", "classification_basis": [{"field": "event_type", "source": "要項PDFの種目欄"}]}
        candidate = {"source_id": "toyokawa_tennis_association", "parse_status": "success", "event_types": ["ダブルス"],
                     "venue": "新会場", "notes": []}
        self.assertEqual(ru.preserve_unread_optional_fields(previous, candidate), candidate)


if __name__ == "__main__":
    unittest.main()
