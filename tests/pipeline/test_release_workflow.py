import subprocess
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / ".github/workflows/all-sources-update.yml").read_text(), Loader=yaml.BaseLoader)

    def test_weekly_and_confirmed_manual_only(self):
        triggers = self.workflow["on"]
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        self.assertEqual(triggers["schedule"][0]["cron"], "17 18 * * 0")
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_readonly_fetch_and_guard_before_commit(self):
        steps = self.workflow["jobs"]["update"]["steps"]
        names = [s.get("name", "") for s in steps]
        self.assertLess(names.index("Validate schema and release safety"), names.index("Prepare and atomically commit data only"))
        fetch = next(s["run"] for s in steps if s.get("name") == "Fetch all sources without writing public data")
        self.assertIn("--dry-run", fetch)

    def test_cross_repository_atomic_publication_uses_full_history(self):
        checkouts = [s for s in self.workflow["jobs"]["update"]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 2)
        for step in checkouts:
            self.assertEqual(step["with"]["fetch-depth"], "0")

    def test_atomic_nonforced_push_and_explicit_file_staging(self):
        script = next(s["run"] for s in self.workflow["jobs"]["update"]["steps"] if s.get("name") == "Prepare and atomically commit data only")
        self.assertIn("push --atomic", script)
        self.assertNotIn("--force", script)
        self.assertIn("git -C public add -- data/tournaments.json", script)
        self.assertNotIn("git add .", script)
        self.assertNotIn("index.html", script)

    def test_no_raw_or_collector_in_public_artifact(self):
        script = next(s["run"] for s in self.workflow["jobs"]["update"]["steps"] if s.get("name") == "Assemble approved public assets only")
        commands = [s for s in script.splitlines() if s.strip().startswith("cp ")]
        self.assertEqual(len(commands), 3)
        self.assertFalse(any("collector" in s or "raw" in s for s in commands))

    def test_deploy_requires_success_and_limited_permissions(self):
        job = self.workflow["jobs"]["deploy"]
        self.assertEqual(job["needs"], "update")
        self.assertNotIn("if", job)
        self.assertEqual(job["permissions"], {"contents": "read", "pages": "write", "id-token": "write"})

    def test_shell_syntax(self):
        for job in self.workflow["jobs"].values():
            for step in job.get("steps", []):
                if "run" in step:
                    subprocess.run(["bash", "-n"], input=step["run"], text=True, check=True)

    def test_manual_app_updates_keep_a_publication_route(self):
        doc = yaml.load((ROOT / ".github/workflows/pages-public.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(doc["on"]["push"]["branches"], ["main"])
        self.assertIn("index.html", doc["on"]["push"]["paths"])
        self.assertEqual(doc["concurrency"], self.workflow["concurrency"])


if __name__ == "__main__":
    unittest.main()
