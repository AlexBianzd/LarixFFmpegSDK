from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.common.source as source_module
from scripts.common.source import prepare_source


def archive_bytes(
    members: tuple[tuple[str, bytes, str | None, int], ...] = (
        ("ffmpeg-9.0.1/README", b"source\n", None, 0o644),
    ),
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        for name, content, link_target, mode in members:
            info = tarfile.TarInfo(name)
            info.mode = mode
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


def special_archive(member_type: bytes, *, link_target: str = "") -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        root = tarfile.TarInfo("ffmpeg-9.0.1/")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        archive.addfile(root)
        special = tarfile.TarInfo("ffmpeg-9.0.1/special")
        special.type = member_type
        special.linkname = link_target
        archive.addfile(special)
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
            ("oversize", content + b"x", self.lock_for(content), "https://cdn.invalid/x"),
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
            (("/absolute", b"x", None, 0o644),),
            (("ffmpeg-9.0.1/../escape", b"x", None, 0o644),),
            (("ffmpeg-9.0.1\\escape", b"x", None, 0o644),),
            (("C:/escape", b"x", None, 0o644),),
            (("ffmpeg-9.0.1/link", b"", "../../escape", 0o777),),
            (
                ("ffmpeg-9.0.1/a", b"a", None, 0o644),
                ("other-root/b", b"b", None, 0o644),
            ),
            (
                ("ffmpeg-9.0.1/duplicate", b"a", None, 0o644),
                ("ffmpeg-9.0.1/duplicate", b"b", None, 0o644),
            ),
        )
        for index, members in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.prepare(archive_bytes(members))
            self.assertFalse(self.sources.exists())

    def test_rejects_nonportable_components_and_equivalent_paths_before_writes(self) -> None:
        unsafe_names = (
            "ffmpeg-9.0.1/file:stream",
            "ffmpeg-9.0.1/CON",
            "ffmpeg-9.0.1/nul.txt",
            "ffmpeg-9.0.1/trailing.",
            "ffmpeg-9.0.1/trailing ",
            "ffmpeg-9.0.1/nested/C:/escape",
            "ffmpeg-9.0.1/question?",
        )
        for name in unsafe_names:
            with self.subTest(name=name):
                content = archive_bytes(((name, b"x", None, 0o644),))
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:xz") as archive:
                    with self.assertRaises(ValueError):
                        source_module._validated_members(archive, "ffmpeg-9.0.1")

        collisions = (
            (
                ("ffmpeg-9.0.1/Case", b"a", None, 0o644),
                ("ffmpeg-9.0.1/case", b"b", None, 0o644),
            ),
            (
                ("ffmpeg-9.0.1/name", b"a", None, 0o644),
                ("ffmpeg-9.0.1/name.", b"b", None, 0o644),
            ),
        )
        for members in collisions:
            with self.subTest(members=members):
                content = archive_bytes(members)
                with tarfile.open(fileobj=io.BytesIO(content), mode="r:xz") as archive:
                    with self.assertRaises(ValueError):
                        source_module._validated_members(archive, "ffmpeg-9.0.1")

    def test_rejects_hardlinks_devices_fifos_and_other_special_members(self) -> None:
        cases = (
            ("hardlink", special_archive(tarfile.LNKTYPE, link_target="ffmpeg-9.0.1/README")),
            ("character-device", special_archive(tarfile.CHRTYPE)),
            ("block-device", special_archive(tarfile.BLKTYPE)),
            ("fifo", special_archive(tarfile.FIFOTYPE)),
        )
        for name, content in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(content)
            self.assertFalse(self.sources.exists())

    def test_rejects_member_count_and_path_length_resource_bombs(self) -> None:
        member_bomb = archive_bytes(
            (
                ("ffmpeg-9.0.1/a", b"a", None, 0o644),
                ("ffmpeg-9.0.1/b", b"b", None, 0o644),
            )
        )
        with mock.patch.object(
            source_module, "_MAX_ARCHIVE_MEMBERS", 1, create=True
        ), self.assertRaises(ValueError):
            self.prepare(member_bomb)
        (self.downloads / "ffmpeg-9.0.1.tar.xz").unlink(missing_ok=True)

        path_bomb = archive_bytes(
            (("ffmpeg-9.0.1/" + "x" * 32, b"x", None, 0o644),)
        )
        with mock.patch.object(
            source_module, "_MAX_ARCHIVE_PATH_LENGTH", 16, create=True
        ), self.assertRaises(ValueError):
            self.prepare(path_bomb)

    def test_preserves_sanitized_archive_permissions(self) -> None:
        content = archive_bytes(
            (("ffmpeg-9.0.1/configure", b"#!/bin/sh\n", None, 0o4755),)
        )
        with mock.patch("os.chmod", wraps=os.chmod) as chmod:
            result = self.prepare(content)
        self.assertIn(
            0o755,
            [
                call.args[1]
                for call in chmod.call_args_list
                if Path(call.args[0]).name == "configure"
            ],
        )
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((result / "configure").stat().st_mode), 0o755)

    def test_rejects_stale_destination_and_changed_existing_patch(self) -> None:
        content = archive_bytes()
        self.sources.mkdir()
        with self.assertRaises(ValueError):
            self.prepare(content)
        self.sources.rmdir()

        patch = self.patches / "001-test.patch"
        patch.write_bytes(b"first\n")
        with mock.patch("subprocess.run"):
            first = self.prepare(content)
        patch.write_bytes(b"changed\n")
        with self.assertRaises(ValueError):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)
        self.assertTrue(first.exists())

    def test_rejects_modified_or_deleted_verified_tree(self) -> None:
        for mutation in ("modify", "delete"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    downloads = root / "downloads"
                    sources = root / "source"
                    patches = root / "patches"
                    patches.mkdir()
                    content = archive_bytes()
                    with mock.patch(
                        "urllib.request.urlopen",
                        return_value=Response(content, "https://cdn.invalid/source"),
                    ):
                        prepared = prepare_source(
                            self.lock_for(content), downloads, sources, patches
                        )
                    readme = prepared / "README"
                    if mutation == "modify":
                        readme.write_bytes(b"tampered\n")
                    else:
                        readme.unlink()
                    with self.assertRaises(ValueError):
                        prepare_source(self.lock_for(content), downloads, sources, patches)

    def test_rejects_extra_top_level_cache_content(self) -> None:
        content = archive_bytes()
        self.prepare(content)
        extra_directory = self.sources / "unexpected-root"
        extra_directory.mkdir()
        with self.assertRaises(ValueError):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)
        extra_directory.rmdir()
        extra_file = self.sources / "unexpected.txt"
        extra_file.write_text("unexpected\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)

    def test_cache_manifest_propagates_directory_enumeration_errors(self) -> None:
        content = archive_bytes()
        self.prepare(content)

        def walk_with_error(*args: object, **kwargs: object) -> object:
            onerror = kwargs.get("onerror")
            if callable(onerror):
                onerror(PermissionError("denied subtree"))
            return iter(())

        with mock.patch("os.walk", side_effect=walk_with_error), self.assertRaises(
            PermissionError
        ):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)

    def test_rejects_symlinked_verified_tree(self) -> None:
        content = archive_bytes()
        prepared = self.prepare(content)
        real_root = self.root / "moved-root"
        prepared.rename(real_root)
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(prepared), str(real_root)],
                check=True,
                capture_output=True,
            )
        else:
            prepared.symlink_to(real_root, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare_source(self.lock_for(content), self.downloads, self.sources, self.patches)

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

    def test_applies_a_real_patch_from_the_frozen_bytes(self) -> None:
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "core.autocrlf", "true"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "config",
                "apply.ignoreWhitespace",
                "change",
            ],
            check=True,
        )
        content = archive_bytes(
            (("ffmpeg-9.0.1/hello.txt", b"old\n", None, 0o644),)
        )
        patch = self.patches / "001-real.patch"
        patch.write_bytes(
            b"diff --git a/hello.txt b/hello.txt\n"
            b"--- a/hello.txt\n"
            b"+++ b/hello.txt\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+new\n"
        )
        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(self.root / ".git"),
                "GIT_WORK_TREE": str(self.root),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "apply.ignoreWhitespace",
                "GIT_CONFIG_VALUE_0": "change",
            },
        ):
            result = self.prepare(content)
        self.assertEqual((result / "hello.txt").read_bytes(), b"new\n")

    def test_archive_extraction_uses_the_verified_snapshot(self) -> None:
        content = archive_bytes()
        original_extract = source_module._extract

        def mutate_original_then_extract(
            archive_path: Path, container: Path, expected_root: str
        ) -> Path:
            self.assertNotEqual(archive_path, self.downloads / "ffmpeg-9.0.1.tar.xz")
            (self.downloads / "ffmpeg-9.0.1.tar.xz").write_bytes(b"tampered")
            return original_extract(archive_path, container, expected_root)

        with mock.patch(
            "scripts.common.source._extract", side_effect=mutate_original_then_extract
        ):
            result = self.prepare(content)
        self.assertEqual((result / "README").read_bytes(), b"source\n")

    def test_patch_manifest_is_sorted_and_rerun_is_idempotent(self) -> None:
        content = archive_bytes()
        (self.patches / "b.patch").write_bytes(b"b\n")
        (self.patches / "a.patch").write_bytes(b"a\n")
        with mock.patch("subprocess.run") as run:
            first = self.prepare(content)
        state = json.loads((self.sources / ".larix-source-state.json").read_text())
        self.assertEqual([entry["path"] for entry in state["patches"]], ["a.patch", "b.patch"])
        self.assertEqual(run.call_count, 4)
        self.assertEqual(
            [call.kwargs["input"] for call in run.call_args_list],
            [b"a\n", b"a\n", b"b\n", b"b\n"],
        )
        for call in run.call_args_list:
            command = call.args[0]
            self.assertIn("--no-index", command)
            self.assertIn("apply.ignoreWhitespace=no", command)
            self.assertEqual(call.kwargs["env"]["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(call.kwargs["env"]["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(
                Path(call.kwargs["env"]["GIT_CEILING_DIRECTORIES"]).name,
                "source",
            )

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
