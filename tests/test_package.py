import importlib.util
from pathlib import Path
import shutil
import subprocess
import json
import hashlib
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package", ROOT / "scripts/package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("plugins", ".claude-plugin", ".agents"):
            shutil.copytree(ROOT / name, self.root / name)
        self.plugin = self.root / "plugins/hitkeep-analytics"

    def source(self):
        source = self.root / "upstream"
        source.mkdir()
        for relative in package.CANONICAL:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((self.plugin / relative).read_bytes())
        def git(*args):
            return subprocess.check_output(["git", "-C", str(source), *args]).decode().strip()
        git("init", "-q")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        return source, git("rev-parse", "HEAD"), git

    def test_rehashed_tamper_fails_independent_source_verification(self):
        source, commit, _ = self.source()
        package.sync(source, commit, "2.15.1", self.root)
        path = self.plugin / "skills/hitkeep-analytics/SKILL.md"
        path.write_text("tampered")
        inventory = self.plugin / "source.json"
        data = json.loads(inventory.read_text())
        data["sha256"]["skills/hitkeep-analytics/SKILL.md"] = hashlib.sha256(path.read_bytes()).hexdigest()
        inventory.write_text(json.dumps(data))
        package.verify(self.root)
        with self.assertRaisesRegex(ValueError, "upstream content"):
            package.verify_source(source, commit, self.root)

    def test_sync_uses_immutable_source_and_updates_versions(self):
        source, commit, _ = self.source()
        (source / "skills/hitkeep-analytics/SKILL.md").write_text("uncommitted")
        (source / ".agents").mkdir()
        (source / ".agents/private").write_text("not product content")
        package.sync(source, commit, "2.15.1", self.root)
        package.verify_source(source, commit, self.root)
        self.assertEqual(package.verify(self.root)[0], "2.15.1")
        inventory = json.loads((self.plugin / "source.json").read_text())
        self.assertEqual(inventory["commit"], commit)
        self.assertRegex(inventory["packager_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(set(inventory["sha256"]), package.CANONICAL)
        self.assertEqual((self.root / "LICENSE").read_bytes(), (source / "LICENSE").read_bytes())
        self.assertEqual((self.root / ".agents/plugins/marketplace.json").read_bytes(),
                         (ROOT / ".agents/plugins/marketplace.json").read_bytes())

    def test_source_symlink_rejected_before_writes(self):
        source, _, git = self.source()
        path = source / "skills/hitkeep-analytics/SKILL.md"
        path.unlink()
        path.symlink_to("../../LICENSE")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "symlink")
        before = (self.plugin / "source.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "regular"):
            package.sync(source, git("rev-parse", "HEAD"), "2.15.1", self.root)
        self.assertEqual(before, (self.plugin / "source.json").read_bytes())

    def test_bad_source_identity_and_version_rejected_before_writes(self):
        source, commit, _ = self.source()
        before = (self.plugin / "source.json").read_bytes()
        for sha, version in (("HEAD", "2.15.1"), (commit, "../escape"), ("0" * 40, "2.15.1")):
            with self.assertRaises(ValueError):
                package.sync(source, sha, version, self.root)
        self.assertEqual(before, (self.plugin / "source.json").read_bytes())

    def test_changed_canonical_skill_is_rejected(self):
        (self.plugin / "skills/hitkeep-analytics/SKILL.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "canonical content changed"):
            package.verify(self.root)

    def test_extra_content_is_rejected(self):
        (self.plugin / "hook.sh").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "allowlist mismatch"):
            package.verify(self.root)

    def test_symlink_is_rejected(self):
        (self.plugin / "outside").symlink_to(ROOT)
        with self.assertRaisesRegex(ValueError, "symlink"):
            package.verify(self.root)

    def test_mismatched_version_is_rejected(self):
        path = self.plugin / ".claude-plugin/plugin.json"
        path.write_text(path.read_text().replace('"2.15.0"', '"0.0.0"'))
        with self.assertRaisesRegex(ValueError, "manifest mismatch"):
            package.verify(self.root)

    def test_valid_package_is_reproducible(self):
        first, second = self.root / "first.zip", self.root / "second.zip"
        package.package(first, self.root)
        package.package(second, self.root)
        self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
