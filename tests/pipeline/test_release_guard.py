import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
from release_guard import EXPECTED_SOURCES, check_release


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.old = {"tournaments": [
            {"id": key, "source_id": sid, "venue": "会場", "event_types": ["ダブルス"],
             "audience_types": ["一般"], "parse_status": "success"}
            for key, sid in EXPECTED_SOURCES.items()]}
        self.new = copy.deepcopy(self.old)
        self.summary = {"results": {key: {"status": "ok", "records": 1}
                                    for key in EXPECTED_SOURCES}, "validation_errors": []}

    def check(self):
        return check_release(self.old, self.new, self.summary)

    def test_success(self):
        self.assertEqual(self.check(), [])

    def test_partial_with_retained_details_is_allowed(self):
        self.new["tournaments"][0]["parse_status"] = "partial"
        self.assertEqual(self.check(), [])

    def test_failed_source_keeps_exact_previous_rows(self):
        self.summary["results"]["aichi"] = {"status": "failed"}
        self.assertEqual(self.check(), [])

    def test_failed_source_change_is_rejected(self):
        self.summary["results"]["aichi"] = {"status": "failed"}
        self.new["tournaments"][0]["venue"] = "別会場"
        self.assertTrue(self.check())

    def test_deleted_event_is_rejected(self):
        self.new["tournaments"].pop()
        self.assertTrue(self.check())

    def test_duplicate_is_rejected(self):
        self.new["tournaments"].append(self.new["tournaments"][0])
        self.assertTrue(self.check())

    def test_lost_important_field_is_rejected(self):
        self.new["tournaments"][0]["venue"] = None
        self.assertTrue(self.check())

    def test_partial_classification_loss_is_rejected(self):
        self.new["tournaments"][0].update(parse_status="partial", audience_types=["不明"])
        self.assertTrue(self.check())

    def test_all_failed_is_rejected(self):
        for result in self.summary["results"].values():
            result["status"] = "failed"
        self.assertTrue(self.check())

    def test_missing_source_is_rejected(self):
        del self.summary["results"]["okazaki"]
        self.assertTrue(self.check())

    def test_wrong_count_is_rejected(self):
        self.summary["results"]["aichi"]["records"] = 2
        self.assertTrue(self.check())

    def test_validation_error_is_rejected(self):
        self.summary["validation_errors"] = ["invalid"]
        self.assertTrue(self.check())


if __name__ == "__main__":
    unittest.main()
