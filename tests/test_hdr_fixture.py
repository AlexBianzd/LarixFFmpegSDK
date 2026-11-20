import hashlib, json, tempfile, unittest
from pathlib import Path
import scripts.common.verify_sdk as verify

ROOT = Path(__file__).resolve().parents[1]


class HDRFixtureTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            callable(getattr(verify, "prepare_hdr_fixture", None)),
            "locked HDR fixture preparation missing",
        )

    def test_prepares_the_exact_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            verify.prepare_hdr_fixture(ROOT, dest)
            self.assertEqual(
                (dest / "hdr10plus.json").read_bytes(),
                (ROOT / "tests/fixtures/hdr10plus.json").read_bytes(),
            )

    def test_rejects_changed_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            src = repo / "tests/fixtures"
            src.mkdir(parents=True)
            (src / "hdr-fixtures.lock.json").write_bytes(
                (ROOT / "tests/fixtures/hdr-fixtures.lock.json").read_bytes()
            )
            (src / "hdr10plus.json").write_bytes(b"wrong metadata")
            with self.assertRaises(ValueError):
                verify.prepare_hdr_fixture(repo, repo / "out")
