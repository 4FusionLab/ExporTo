"""Check the installable ZIP layout, version stamp, and checksum."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from scripts.build import build
from scripts.check_project import ICONS, validate

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_reproducible_package(self):
        name = validate(ROOT)
        manifest_path = ROOT / name / f"{name}.manifest"
        original = manifest_path.read_bytes()
        with TemporaryDirectory() as output:
            archive, checksum = build(ROOT, "1.0.0", output)
            first = archive.read_bytes()
            self.assertEqual(
                checksum.read_text(encoding="ascii").split(),
                [hashlib.sha256(first).hexdigest(), archive.name],
            )
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                expected = {
                    f"{name}/{name}.py",
                    f"{name}/{name}.manifest",
                    f"{name}/platform_apps.py",
                }
                expected.update(
                    f"{name}/resources/{directory}/{filename}"
                    for directory in ("application", "clear", "export", "stl")
                    for filename in ICONS
                )
                if (ROOT / "LICENSE").is_file():
                    expected.add(f"{name}/LICENSE")
                self.assertEqual(names, expected)
                manifest = json.loads(bundle.read(f"{name}/{name}.manifest"))
                self.assertEqual(manifest["version"], "1.0.0")
                self.assertEqual(manifest["id"], json.loads(original)["id"])
                self.assertTrue(manifest["runOnStartup"])
                self.assertIsNone(bundle.testzip())
            build(ROOT, "1.0.0", output)
            self.assertEqual(first, archive.read_bytes())
        self.assertEqual(original, manifest_path.read_bytes())
