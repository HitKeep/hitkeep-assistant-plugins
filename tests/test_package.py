import importlib.util
from pathlib import Path
import shutil
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
