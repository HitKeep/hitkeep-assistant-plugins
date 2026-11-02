import importlib.util
from pathlib import Path
import shutil
import subprocess
import json
import hashlib
import tempfile
import unittest
import io
import zipfile
from unittest.mock import patch

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
        from test_release_delivery import fixture_public
        mock = patch.object(package, "produce_public", side_effect=fixture_public)
        mock.start()
        self.addCleanup(mock.stop)

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

    def test_public_archives_are_exact_deterministic_canonical_files(self):
        source, commit, _ = self.source()
        package.sync(source, commit, "2.16.0", self.root)
        public = self.plugin / "public"
        index = json.loads((public / "index.json").read_text())
        self.assertEqual(index["$schema"], "https://schemas.agentskills.io/discovery/0.2.0/schema.json")
        self.assertEqual({entry["name"] for entry in index["skills"]}, package.SKILLS)
        self.assertEqual({p.name for p in public.iterdir()}, {"index.json", "source.json"} | {name + ".zip" for name in package.SKILLS})
        self.assertEqual(json.loads((public / "source.json").read_text())["commit"], commit)
        for entry in index["skills"]:
            raw = (public / (entry["name"] + ".zip")).read_bytes()
            self.assertEqual(entry["digest"], "sha256:" + hashlib.sha256(raw).hexdigest())
            self.assertEqual(entry["url"], "https://hitkeep.com/skills/v2.16.0/" + entry["name"] + ".zip")
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                self.assertEqual(archive.namelist(), ["SKILL.md", "references/procedure.md"])
                for item in archive.infolist():
                    self.assertEqual(archive.read(item), (self.plugin / "skills" / entry["name"] / item.filename).read_bytes())
                    self.assertEqual(item.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(item.external_attr >> 16, 0o100644)
                    self.assertEqual(item.flag_bits, 8)
                    self.assertEqual(item.extra, b"")

        (public / (index["skills"][0]["name"] + ".zip")).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "public artifact"):
            package.verify(self.root)

    def test_public_timestamp_metadata_accepts_only_legacy_or_fixed_go_epoch(self):
        from test_release_delivery import fixture_public
        source, commit, _ = self.source()
        fixed_epoch = bytes.fromhex("555405000100a6ce12")
        for extra, accepted in [(b"", True), (fixed_epoch, True), (fixed_epoch[:-1] + b"\x13", False), (b"\x00\x00\x00\x00", False)]:
            with self.subTest(extra=extra):
                def produce(*args):
                    return fixture_public(*args, timestamp_extra=extra)
                with patch.object(package, "produce_public", side_effect=produce):
                    if accepted:
                        package.sync(source, commit, "2.16.0", self.root)
                        package.verify(self.root)
                    else:
                        with self.assertRaisesRegex(ValueError, "public artifact"):
                            package.sync(source, commit, "2.16.0", self.root)

    def test_rehashed_tamper_fails_independent_source_verification(self):
        source, commit, _ = self.source()
        package.sync(source, commit, "2.15.1", self.root)
        path = self.plugin / "skills/hitkeep-analytics/SKILL.md"
        path.write_text("tampered")
        inventory = self.plugin / "source.json"
        data = json.loads(inventory.read_text())
        data["sha256"]["skills/hitkeep-analytics/SKILL.md"] = hashlib.sha256(path.read_bytes()).hexdigest()
        inventory.write_text(json.dumps(data))
        # Rehashing the authored file cannot authorize a different archive or canonical source.
        with self.assertRaisesRegex(ValueError, "public artifact|upstream content"):
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

    def test_manifest_hooks_are_rejected(self):
        for relative in ("plugin.json", ".claude-plugin/plugin.json"):
            path = self.plugin / relative
            original = path.read_text()
            data = json.loads(original)
            data["hooks"] = {"PostToolUse": [{"command": "unexpected"}]}
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "hooks"):
                package.verify(self.root)
            path.write_text(original)

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
        data = json.loads(path.read_text())
        data["version"] = "0.0.0"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "manifest mismatch"):
            package.verify(self.root)

    def test_valid_package_is_reproducible(self):
        first, second = self.root / "first.zip", self.root / "second.zip"
        package.package(first, self.root)
        package.package(second, self.root)
        self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
