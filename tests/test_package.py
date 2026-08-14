from __future__ import annotations

import io
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

from scripts.common.package import _validated_zip_entries, create_zip_package, extract_zip_package
from scripts.common.release_manifest import generate_release_metadata
from tests.test_manifest import LOCK, TARGET, create_sdk


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sdk = self.root / "sdk"
        create_sdk(self.sdk)
        generate_release_metadata(self.sdk, LOCK, "lgpl", TARGET)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_zip_is_deterministic_normalized_and_relocatable(self) -> None:
        first = self.root / "first.zip"
        second = self.root / "second.zip"
        create_zip_package(self.sdk, first)
        create_zip_package(self.sdk, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with zipfile.ZipFile(first) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(all("\\" not in name for name in names))
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
        relocated = self.root / "relocated"
        extracted = extract_zip_package(first, relocated)
        self.assertEqual(extracted, relocated)
        self.assertEqual(
            (extracted / "include" / "libavcodec" / "avcodec.h").read_bytes(),
            b"avcodec\n",
        )

    def test_rejects_archive_traversal_absolute_duplicate_and_symlink_entries(self) -> None:
        warnings.filterwarnings('ignore', category=UserWarning)
        cases = {
            "traversal": [("../escape", b"x", 0)],
            "backslash": [("bin\\escape.dll", b"x", 0)],
            "absolute": [("/escape", b"x", 0)],
            "drive": [("C:/escape", b"x", 0)],
            "duplicate": [("same", b"a", 0), ("same", b"b", 0)],
            "symlink": [("link", b"target", 0o120777 << 16)],
        }
        for name, entries in cases.items():
            with self.subTest(name=name):
                path = self.root / f"{name}.zip"
                with zipfile.ZipFile(path, "w") as archive:
                    for entry_name, data, attributes in entries:
                        info = zipfile.ZipInfo(entry_name)
                        info.create_system = 3
                        info.external_attr = attributes
                        archive.writestr(info, data)
                with self.assertRaises(ValueError):
                    extract_zip_package(path, self.root / f"out-{name}")

    def test_rejects_drive_relative_and_reserved_archive_entries(self) -> None:
        for entry_name in ('C:escape', 'bin/AUX.txt', 'bin/trailing.'):
            with self.subTest(entry_name=entry_name):
                path = self.root / ('portable-' + entry_name.replace('/', '-') + '.zip')
                with zipfile.ZipFile(path, 'w') as archive:
                    info = zipfile.ZipInfo(entry_name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, b'x')
                with zipfile.ZipFile(path, 'r') as archive, self.assertRaises(ValueError):
                    _validated_zip_entries(archive)

    def test_rejects_noncanonical_or_mutated_sdk_before_packaging(self) -> None:
        (self.sdk / "lib" / "avformat.lib").write_bytes(b"mutated")
        with self.assertRaises(ValueError):
            create_zip_package(self.sdk, self.root / "bad.zip")


if __name__ == "__main__":
    unittest.main()
