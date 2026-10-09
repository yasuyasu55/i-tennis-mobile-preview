"""Execute the actual workflow shell against disposable local Git repositories."""
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_FILES = ("tournaments.json", "tournament-state.json", "tournament-history.json", "update-log.json")


class AtomicPublicationTests(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.check_output(["git", "-C", str(directory), *args], text=True, stderr=subprocess.PIPE).strip()

    def setup_repositories(self, root):
        bare, seed = root / "remote.git", root / "seed"
        self.git(root, "init", "--bare", str(bare))
        self.git(root, "init", "-b", "main", str(seed))
        self.git(seed, "config", "user.name", "Test")
        self.git(seed, "config", "user.email", "test@example.invalid")
        (seed / "data").mkdir()
        for name in DATA_FILES:
            (seed / "data" / name).write_text('"old"\n')
        (seed / "index.html").write_text("unchanged UI")
        self.git(seed, "add", ".")
        self.git(seed, "commit", "-m", "base")
        self.git(seed, "remote", "add", "origin", str(bare))
        self.git(seed, "push", "origin", "main", "HEAD:hamamatsu-r5-candidate")
        self.git(root, "clone", "-b", "main", str(bare), "public")
        self.git(root, "clone", "-b", "hamamatsu-r5-candidate", str(bare), "collector")
        output = root / "collector/pipeline/out/proposed"
        output.mkdir(parents=True)
        for name in DATA_FILES:
            (output / name).write_text('"new"\n')
        return bare, seed

    def script(self):
        doc = yaml.load((ROOT / ".github/workflows/all-sources-update.yml").read_text(), Loader=yaml.BaseLoader)
        return next(s["run"] for s in doc["jobs"]["update"]["steps"]
                    if s.get("name") == "Prepare and atomically commit data only")

    def test_atomic_success_changes_only_expected_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bare, _ = self.setup_repositories(root)
            old = self.git(bare, "rev-parse", "main")
            subprocess.run(["bash", "-e", "-c", self.script()], cwd=root, check=True, capture_output=True)
            self.assertEqual(self.git(bare, "diff", "--name-only", old, "main"), "data/tournaments.json")
            self.assertEqual(set(self.git(bare, "diff", "--name-only", old, "hamamatsu-r5-candidate").splitlines()),
                             {"data/" + name for name in DATA_FILES})
            self.assertEqual(self.git(bare, "show", "main:index.html"), "unchanged UI")

    def test_concurrent_main_change_rejects_both_branches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bare, seed = self.setup_repositories(root)
            old_candidate = self.git(bare, "rev-parse", "hamamatsu-r5-candidate")
            (seed / "index.html").write_text("user's new UI")
            self.git(seed, "add", "index.html")
            self.git(seed, "commit", "-m", "concurrent user change")
            self.git(seed, "push", "origin", "main")
            current_main = self.git(bare, "rev-parse", "main")
            result = subprocess.run(["bash", "-e", "-c", self.script()], cwd=root, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.git(bare, "rev-parse", "main"), current_main)
            self.assertEqual(self.git(bare, "rev-parse", "hamamatsu-r5-candidate"), old_candidate)


if __name__ == "__main__":
    unittest.main()
