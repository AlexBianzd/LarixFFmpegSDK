from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.common.model import load_lock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPOSITORY_ROOT / "config" / "ffmpeg.lock.json"

EXPECTED = {
    "schemaVersion": 1,
    "upstreamVersion": "9.0.1",
    "packagingRevision": 1,
    "releaseTag": "ffmpeg-9.0.1-larix.1",
    "source": {
        "url": "https://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz",
        "archive": "ffmpeg-9.0.1.tar.xz",
        "size": 12036420,
        "sha256": "cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635",
    },
    "profiles": ["lgpl", "gpl"],
}


class LoadLockTests(unittest.TestCase):
    def test_loads_the_exact_ffmpeg_9_0_1_source_identity(self) -> None:
        self.assertEqual(load_lock(LOCK_PATH), EXPECTED)

    def test_returns_a_freshly_owned_result(self) -> None:
        first = load_lock(LOCK_PATH)
        first["profiles"].append("mutation")
        self.assertEqual(load_lock(LOCK_PATH), EXPECTED)

    def test_rejects_invalid_lock_contracts(self) -> None:
        rejected = [
            ("http source URL", ("source", "url"), "http://ffmpeg.org/releases/ffmpeg-9.0.1.tar.xz"),
            ("uppercase hash", ("source", "sha256"), EXPECTED["source"]["sha256"].upper()),
            ("short hash", ("source", "sha256"), "cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f63"),
            ("latest release tag", ("releaseTag",), "latest"),
            ("nonfree profile", ("profiles",), ["lgpl", "gpl", "nonfree"]),
            ("duplicate profile", ("profiles",), ["lgpl", "gpl", "gpl"]),
        ]

        for name, path, value in rejected:
            with self.subTest(name=name):
                candidate = copy.deepcopy(EXPECTED)
                target = candidate
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assert_rejected(candidate)

        missing_size = copy.deepcopy(EXPECTED)
        del missing_size["source"]["size"]
        self.assert_rejected(missing_size)

    def assert_rejected(self, candidate: dict[str, object]) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            path = Path(temp_directory) / "ffmpeg.lock.json"
            path.write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_lock(path)


if __name__ == "__main__":
    unittest.main()
