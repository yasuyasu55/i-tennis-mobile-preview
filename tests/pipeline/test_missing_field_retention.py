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


if __name__ == "__main__":
    unittest.main()
