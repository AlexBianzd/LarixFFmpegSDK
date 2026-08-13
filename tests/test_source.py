from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.common.source import prepare_source


def archive_bytes(
    members: tuple[tuple[str, bytes, str | None], ...] = (
        ("ffmpeg-9.0.1/README", b"source\n", None),
    ),
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        for name, content, link_target in members:
            info = tarfile.TarInfo(name)
            if link_target is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link_target
                archive.addfile(info)
            elif name.endswith("/"):
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, content: bytes, final_url: str) -> None:
        super().__init__(content)
        self._final_url = final_url

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class SourcePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.downloads = self.root / "downloads"
        self.sources = self.root / "source"
        self.patches = self.root / "patches"
        self.patches.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def lock_for(self, content: bytes) -> dict[str, object]:
        return {
            "upstreamVersion": "9.0.1",
            "source": {
                "url": "https://example.invalid/ffmpeg-9.0.1.tar.xz",
                "archive": "ffmpeg-9.0.1.tar.xz",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        }

    def prepare(self, content: bytes, final_url: str = "https://cdn.invalid/source") -> Path:
        with mock.patch(
            "urllib.request.urlopen",
            return_value=Response(content, final_url),
        ):
            return prepare_source(
                self.lock_for(content), self.downloads, self.sources, self.patches
            )

    def test_downloads_verifies_extracts_and_records_state(self) -> None:
        content = archive_bytes()
        result = self.prepare(content)
        self.assertEqual(result, self.sources / "ffmpeg-9.0.1")
        self.assertEqual((result / "README").read_bytes(), b"source\n")
        self.assertEqual(
            hashlib.sha256((self.downloads / "ffmpeg-9.0.1.tar.xz").read_bytes()).hexdigest(),
            self.lock_for(content)["source"]["sha256"],
        )
        state = json.loads((self.sources / ".larix-source-state.json").read_text())
        self.assertEqual(state["patches"], [])

    def test_rejects_partial_length_hash_and_non_https_redirect(self) -> None:
        content = archive_bytes()
        cases = (
            ("partial", content[:-1], self.lock_for(content), "https://cdn.invalid/x"),
            (
                "hash",
                content,
                {**self.lock_for(content), "source": {**self.lock_for(content)["source"], "sha256": "0" * 64}},
                "https://cdn.invalid/x",
            ),
            ("redirect", content, self.lock_for(content), "http://cdn.invalid/x"),
        )
        for name, delivered, lock, final_url in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                with mock.patch(
                    "urllib.request.urlopen",
                    return_value=Response(delivered, final_url),
                ):
                    prepare_source(lock, self.downloads, self.sources, self.patches)
            self.assertFalse(self.sources.exists())

    def test_rejects_unsafe_or_ambiguous_archive_members(self) -> None:
        cases = (
            (("/absolute", b"x", None),),
            (("ffmpeg-9.0.1/../escape", b"x", None),),
            (("C:/escape", b"x", None),),
            (("ffmpeg-9.0.1/link", b"", "../../escape"),),
            (
                ("ffmpeg-9.0.1/a", b"a", None),
                ("other-root/b", b"b", None),
            ),
        )
        for index, members in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.prepare(archive_bytes(members))
            self.assertFalse(self.sources.exists())

    def test_rejects_stale_destination_and_changed_patch_set(self) -> None:
        content = archive_bytes()
        self.sources.mkdir()
        with self.assertRaises(ValueError):
            self.prepare(content)
        self.sources.rmdir()

        first = self.prepare(content)
        patch = self.patches / "001-test.patch"
        patch.write_text("changed\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)
        self.assertTrue(first.exists())

    def test_failed_patch_leaves_no_accepted_source_root(self) -> None:
        content = archive_bytes()
        patch = self.patches / "001-invalid.patch"
        patch.write_text("not a patch\n", encoding="utf-8")
        with mock.patch(
            "subprocess.run",
            side_effect=__import__("subprocess").CalledProcessError(1, ["git"]),
        ), self.assertRaises(__import__("subprocess").CalledProcessError):
            self.prepare(content)
        self.assertFalse(self.sources.exists())

    def test_patch_manifest_is_sorted_and_rerun_is_idempotent(self) -> None:
        content = archive_bytes()
        (self.patches / "b.patch").write_text("b\n", encoding="utf-8")
        (self.patches / "a.patch").write_text("a\n", encoding="utf-8")
        with mock.patch("subprocess.run") as run:
            first = self.prepare(content)
        state = json.loads((self.sources / ".larix-source-state.json").read_text())
        self.assertEqual([entry["path"] for entry in state["patches"]], ["a.patch", "b.patch"])
        self.assertEqual(run.call_count, 4)

        with mock.patch("urllib.request.urlopen") as download, mock.patch(
            "subprocess.run"
        ) as rerun_patch:
            second = prepare_source(
                self.lock_for(content), self.downloads, self.sources, self.patches
            )
        self.assertEqual(first, second)
        download.assert_not_called()
        rerun_patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
