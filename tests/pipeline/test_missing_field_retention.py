import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import run_update as ru


class MissingFieldRetentionTests(unittest.TestCase):
    def test_empty_candidate_does_not_erase_previous_optional_values(self):
        previous = {
            "id": "event-1", "entry_url": "https://example.test/apply",
            "reserve_text": "12/6", "reserve_periods": [{"raw": "12/6"}],
            "deadline_text": "10月26日", "notes": ["old note"],
        }
        candidate = {
            "id": "event-1", "entry_url": None, "reserve_text": None,
            "reserve_periods": [], "deadline_text": "11月1日", "notes": [],
        }

        result = ru.preserve_unread_optional_fields(previous, candidate)

        self.assertEqual(result["entry_url"], previous["entry_url"])
        self.assertEqual(result["reserve_text"], previous["reserve_text"])
        self.assertEqual(result["reserve_periods"], previous["reserve_periods"])
        self.assertEqual(result["deadline_text"], "11月1日")
        self.assertTrue(any("前回値を保持" in n for n in result["notes"]))

    def test_first_run_empty_values_remain_empty(self):
        candidate = {"id": "event-2", "entry_url": None, "reserve_text": None}
        self.assertEqual(ru.preserve_unread_optional_fields({}, candidate), candidate)

    def test_nonempty_new_value_replaces_old_value(self):
        result = ru.preserve_unread_optional_fields(
            {"entry_url": "https://example.test/old"},
            {"entry_url": "https://example.test/new"},
        )
        self.assertEqual(result["entry_url"], "https://example.test/new")


if __name__ == "__main__":
    unittest.main()
