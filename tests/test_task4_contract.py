from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.common.release_manifest import (
    PLATFORM_CONFIGURE_ARGS,
    _scan_forbidden_paths,
    generate_release_metadata,
    validate_release_manifest_schema,
    verify_release_metadata,
)
from scripts.common.verify_sdk import verify_ffprobe_inputs
from tests.test_manifest import LOCK, TARGET, create_sdk


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.sdk = Path(self.temporary.name) / "sdk"
        create_sdk(self.sdk)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_windows_paths_are_normalized_and_scanned_case_insensitively(self) -> None:
        root = Path(self.temporary.name) / "path-scan"
        root.mkdir()
        payload = root / "file.bin"
        forbidden = r"C:\Build\Secret"
        mixed_case = r"c:\bUILD\sECRET\source.c"
        payload.write_bytes(mixed_case.encode("utf-8"))
        with self.assertRaises(ValueError):
            _scan_forbidden_paths(root, (forbidden,))
        payload.write_bytes(mixed_case.encode("utf-16le"))
        with self.assertRaises(ValueError):
            _scan_forbidden_paths(root, (forbidden,))
        unicode_forbidden = r"C:\BÜILD\Secret"
        for encoding in ("utf-8", "utf-16le"):
            for observed in (
                r"C:\BÜILD\Secret\source.c",
                r"c:\büild\secret\source.c",
                r"c:\bÜild\sECRET\source.c",
            ):
                with self.subTest(encoding=encoding, observed=observed):
                    prefix = b"X" if encoding == "utf-16le" else b"prefix:"
                    payload.write_bytes(prefix + observed.encode(encoding))
                    with self.assertRaises(ValueError):
                        _scan_forbidden_paths(root, (unicode_forbidden,))

        configure = " ".join(PLATFORM_CONFIGURE_ARGS)
        for option in (
            "--cc=larix-msvc-cl.cmd",
            "/experimental:deterministic",
            "/Brepro",
            "/pathmap:./src=larix-source",
            "/pathmap:.=larix-build",
            "/pathmap:../install=larix-install",
            "/pathmap:..=larix-output",
            "/PDBALTPATH:%_PDB%",
            "--prefix=../install",
        ):
            self.assertIn(option, configure)
        self.assertNotIn(str(self.sdk.parent), configure)
        driver = (
            REPOSITORY_ROOT / "scripts" / "platforms" / "windows" / "build.ps1"
        ).read_text(encoding="utf-8")
        for token in (
            "/experimental:deterministic",
            "/Brepro",
            "/pathmap:",
            "/PDBALTPATH:",
            "larix-source",
            "larix-build",
            "larix-install",
            "larix-output",
            "New-Item -ItemType Junction",
            "'src/configure'",
            "'--prefix=../install'",
        ):
            self.assertIn(token, driver)
        configure_scope = driver[
            driver.index("$deterministicCFlags = @("):
            driver.index("$sourceJunction =", driver.index("$deterministicCFlags = @("))
        ]
        self.assertNotIn("('/pathmap:' + $source", configure_scope)

    def test_profiles_freeze_actual_ffmpeg_license_semantics_and_payloads(self) -> None:
        cases = {
            "lgpl": (
                "LGPL-2.1-or-later",
                {"FFmpeg-LICENSE.md", "COPYING.LGPLv2.1", "LarixFFmpegSDK-MIT.txt"},
            ),
            "gpl": (
                "GPL-2.0-or-later",
                {"FFmpeg-LICENSE.md", "COPYING.GPLv2", "LarixFFmpegSDK-MIT.txt"},
            ),
        }
        for profile, (expression, required) in cases.items():
            with self.subTest(profile=profile):
                root = Path(self.temporary.name) / profile
                create_sdk(root, profile=profile)
                manifest = generate_release_metadata(
                    root,
                    REPOSITORY_ROOT,
                    profile,
                    TARGET,
                    toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
                    runtime_dependencies={path: ["KERNEL32.dll"] for path in manifest_runtime()},
                    forbidden_paths=(str(root.parent),),
                )
                self.assertEqual(manifest["effectiveLicense"], expression)
                license_names = {
                    path.name for path in (root / "LICENSES").iterdir() if path.is_file()
                }
                self.assertEqual(required, license_names)
                (root / "LICENSES" / "undeclared.txt").write_text(
                    "undeclared\n", encoding="utf-8"
                )
                with self.assertRaises(ValueError):
                    generate_release_metadata(
                        root,
                        REPOSITORY_ROOT,
                        profile,
                        TARGET,
                        toolchain={
                            "compiler": "MSVC 19.44",
                            "windowsSdk": "10.0.26100.0",
                        },
                        runtime_dependencies={
                            path: ["KERNEL32.dll"] for path in manifest_runtime()
                        },
                        forbidden_paths=(str(root.parent),),
                    )
                (root / "LICENSES" / "undeclared.txt").unlink()
                if profile == "gpl":
                    self.assertNotIn("--enable-version3", manifest["configureArgs"])
                    self.assertIn("--enable-gpl", manifest["configureArgs"])

    def test_source_package_identity_and_license_are_profile_independent(self) -> None:
        source_packages = []
        sdk_packages = []
        for profile in ("lgpl", "gpl"):
            root = Path(self.temporary.name) / f"source-package-{profile}"
            create_sdk(root, profile=profile)
            generate_release_metadata(
                root,
                REPOSITORY_ROOT,
                profile,
                TARGET,
                toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
                runtime_dependencies={
                    path: ["KERNEL32.dll"] for path in manifest_runtime()
                },
                forbidden_paths=(str(root.parent),),
            )
            sbom = json.loads(
                (root / "share" / "larix-ffmpeg-sdk" / "sbom.spdx.json").read_text(
                    encoding="utf-8"
                )
            )
            packages = {entry["SPDXID"]: entry for entry in sbom["packages"]}
            source_packages.append(packages["SPDXRef-Package-FFmpeg"])
            sdk_packages.append(packages["SPDXRef-Package-LarixFFmpegSDK"])

        self.assertEqual(source_packages[0], source_packages[1])
        for source_package in source_packages:
            self.assertEqual(source_package["licenseDeclared"], "NOASSERTION")
            self.assertEqual(source_package["licenseConcluded"], "NOASSERTION")
        self.assertEqual(
            sdk_packages[0]["licenseDeclared"], "MIT AND LGPL-2.1-or-later"
        )
        self.assertEqual(
            sdk_packages[1]["licenseDeclared"], "MIT AND GPL-2.0-or-later"
        )

    def test_manifest_binds_every_release_input_and_rejects_tampering(self) -> None:
        manifest = generate_release_metadata(
            self.sdk,
            REPOSITORY_ROOT,
            "lgpl",
            TARGET,
            toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
            runtime_dependencies={path: ["KERNEL32.dll"] for path in manifest_runtime()},
            forbidden_paths=(str(self.sdk.parent),),
        )
        self.assertEqual(manifest["packagingRevision"], 1)
        self.assertEqual(manifest["packageFormat"], "zip")
        self.assertEqual(manifest["assetName"], "larix-ffmpeg-sdk-9.0.1-larix.1-lgpl-windows-x64-msvc.zip")
        self.assertEqual(set(manifest["libraryVersions"]), {"avutil", "avcodec", "avformat", "swresample", "swscale"})
        self.assertEqual(manifest["toolchain"], {"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"})
        self.assertEqual(manifest["patches"], [])
        self.assertIn("--toolchain=msvc", manifest["configureArgs"])
        self.assertEqual(set(manifest["runtimeDependencies"]), set(manifest_runtime()))
        self.assertEqual(verify_release_metadata(self.sdk, REPOSITORY_ROOT), manifest)

        manifest_path = self.sdk / "share" / "larix-ffmpeg-sdk" / "manifest.json"
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutations = {
            "source": {**original["source"], "sha256": "0" * 64},
            "packagingRevision": 2,
            "releaseTag": "ffmpeg-9.0.1-larix.2",
            "licenseProfile": "gpl",
            "assetName": "wrong.zip",
            "packageFormat": "tar.xz",
            "configureArgs": [],
            "patches": [{"path": "undeclared.patch", "sha256": "0" * 64}],
            "target": {**original["target"], "abi": "mingw"},
            "toolchain": {"compiler": "MinGW", "windowsSdk": "unknown"},
            "runtimeDependencies": {},
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(original))
                candidate[field] = value
                manifest_path.write_text(
                    json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                with self.assertRaises(ValueError):
                    verify_release_metadata(self.sdk, REPOSITORY_ROOT)
        manifest_path.write_text(
            json.dumps(original, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def test_sbom_corresponds_exactly_to_manifest_and_requires_license_fields(self) -> None:
        manifest = generate_release_metadata(
            self.sdk,
            REPOSITORY_ROOT,
            "lgpl",
            TARGET,
            toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
            runtime_dependencies={path: ["KERNEL32.dll"] for path in manifest_runtime()},
            forbidden_paths=(str(self.sdk.parent),),
        )
        sbom_path = self.sdk / "share" / "larix-ffmpeg-sdk" / "sbom.spdx.json"
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        self.assertTrue(all("licenseConcluded" in entry and "copyrightText" in entry for entry in sbom["files"]))
        self.assertTrue(all(
            entry["licenseInfoInFiles"] == ["NOASSERTION"]
            for entry in sbom["files"]
        ))
        self.assertEqual(len(sbom["packages"]), 2)
        packages = {entry["SPDXID"]: entry for entry in sbom["packages"]}
        sdk_package = packages["SPDXRef-Package-LarixFFmpegSDK"]
        source_package = packages["SPDXRef-Package-FFmpeg"]
        aggregate_license = "MIT AND " + manifest["effectiveLicense"]
        self.assertEqual(sdk_package["name"], "Larix FFmpeg SDK")
        self.assertEqual(sdk_package["versionInfo"], manifest["releaseTag"])
        self.assertEqual(sdk_package["downloadLocation"], "NOASSERTION")
        self.assertEqual(sdk_package["licenseDeclared"], aggregate_license)
        self.assertEqual(sdk_package["licenseConcluded"], aggregate_license)
        self.assertIs(sdk_package["filesAnalyzed"], False)
        self.assertNotIn("packageVerificationCode", sdk_package)
        self.assertNotIn("licenseInfoFromFiles", sdk_package)
        self.assertEqual(source_package["name"], "FFmpeg")
        self.assertEqual(source_package["versionInfo"], manifest["upstreamVersion"])
        self.assertEqual(
            source_package["downloadLocation"], manifest["source"]["url"])
        self.assertEqual(source_package["licenseDeclared"], "NOASSERTION")
        self.assertEqual(source_package["licenseConcluded"], "NOASSERTION")
        self.assertIs(source_package["filesAnalyzed"], False)
        self.assertEqual(
            source_package["checksums"],
            [{"algorithm": "SHA256",
              "checksumValue": manifest["source"]["sha256"]}],
        )
        relationships = sbom["relationships"]
        self.assertIn(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": sdk_package["SPDXID"],
            },
            relationships,
        )
        self.assertIn(
            {
                "spdxElementId": sdk_package["SPDXID"],
                "relationshipType": "GENERATED_FROM",
                "relatedSpdxElement": source_package["SPDXID"],
            },
            relationships,
        )
        file_ids = {entry["SPDXID"] for entry in sbom["files"]}
        contained_ids = {
            relation["relatedSpdxElement"]
            for relation in relationships
            if relation["spdxElementId"] == sdk_package["SPDXID"]
            and relation["relationshipType"] == "CONTAINS"
        }
        self.assertEqual(contained_ids, file_ids)
        self.assertEqual(len(relationships), len(file_ids) + 2)
        original = json.loads(json.dumps(sbom))
        for field, mutation in (
            ("sdk-license", lambda value: value["packages"][0].__setitem__(
                "licenseDeclared", "NOASSERTION")),
            ("source-license", lambda value: value["packages"][1].__setitem__(
                "licenseConcluded", manifest["effectiveLicense"])),
            ("source-url", lambda value: value["packages"][1].__setitem__(
                "downloadLocation", "https://example.invalid/ffmpeg.tar.xz")),
            ("source-checksum", lambda value: value["packages"][1][
                "checksums"][0].__setitem__("checksumValue", "0" * 64)),
            ("relationship", lambda value: value["relationships"].pop(1)),
            ("contains", lambda value: value["relationships"].pop()),
        ):
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(original))
                mutation(candidate)
                sbom_path.write_text(
                    json.dumps(candidate, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                with self.assertRaises(ValueError):
                    verify_release_metadata(self.sdk, REPOSITORY_ROOT)
        sbom_path.write_text(
            json.dumps(original, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        sbom = json.loads(json.dumps(original))
        sbom["files"][0]["checksums"][0]["checksumValue"] = "0" * 64
        sbom_path.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        with self.assertRaises(ValueError):
            verify_release_metadata(self.sdk, REPOSITORY_ROOT)

    def test_staged_config_and_patch_provenance_are_exact_and_schema_is_strict(self) -> None:
        manifest = generate_release_metadata(
            self.sdk, REPOSITORY_ROOT, 'lgpl', TARGET,
            toolchain={'compiler': 'MSVC 19.44', 'windowsSdk': '10.0.26100.0'},
            runtime_dependencies={path: ['KERNEL32.dll'] for path in manifest_runtime()},
            forbidden_paths=(str(self.sdk.parent),))
        schema = json.loads(
            (REPOSITORY_ROOT / 'config' / 'schema' / 'release-manifest.schema.json').read_text(encoding='utf-8'))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), set(manifest))
        for field in ('source', 'target', 'toolchain', 'libraryVersions', 'runtimeDependencies'):
            self.assertFalse(schema['properties'][field]['additionalProperties'])
        for field in ('files', 'patches'):
            self.assertFalse(schema['properties'][field]['items']['additionalProperties'])
        validate_release_manifest_schema(manifest, REPOSITORY_ROOT)
        for field in ('avcodec', 'avformat', 'avutil', 'swresample', 'swscale'):
            self.assertEqual(
                schema['properties']['libraryVersions']['properties'][field]['type'],
                'integer')
        invalid_version = json.loads(json.dumps(manifest))
        invalid_version['libraryVersions']['avcodec'] = '63'
        with self.assertRaises(ValueError):
            validate_release_manifest_schema(invalid_version, REPOSITORY_ROOT)
        unknown_toolchain_field = json.loads(json.dumps(manifest))
        unknown_toolchain_field['toolchain']['extra'] = 'x'
        with self.assertRaises(ValueError):
            validate_release_manifest_schema(unknown_toolchain_field, REPOSITORY_ROOT)
        with mock.patch(
            'scripts.common.release_manifest.validate_release_manifest_schema',
            side_effect=ValueError('schema gate')):
            with self.assertRaisesRegex(ValueError, 'schema gate'):
                generate_release_metadata(
                    self.sdk, REPOSITORY_ROOT, 'lgpl', TARGET,
                    toolchain={
                        'compiler': 'MSVC 19.44',
                        'windowsSdk': '10.0.26100.0'},
                    runtime_dependencies={
                        path: ['KERNEL32.dll'] for path in manifest_runtime()},
                    forbidden_paths=(str(self.sdk.parent),))
        provenance = self.sdk / 'share' / 'larix-ffmpeg-sdk' / 'provenance'
        (provenance / 'config' / 'common.conf').write_bytes(b'changed\n')
        with self.assertRaises(ValueError):
            verify_release_metadata(self.sdk, REPOSITORY_ROOT)

    def test_stages_the_exact_profile_license_and_repository_provenance(self) -> None:
        from scripts.common.stage_sdk import stage_legal_provenance

        source = Path(self.temporary.name) / 'source'
        for name in ('LICENSE.md', 'COPYING.LGPLv2.1', 'COPYING.LGPLv3',
                     'COPYING.GPLv2', 'COPYING.GPLv3'):
            source.mkdir(parents=True, exist_ok=True)
            (source / name).write_text(name + '\n', encoding='utf-8')
        for profile, required, forbidden in (
            ('lgpl', 'COPYING.LGPLv2.1', 'COPYING.GPLv2'),
            ('gpl', 'COPYING.GPLv2', 'COPYING.LGPLv2.1')):
            with self.subTest(profile=profile):
                stage = Path(self.temporary.name) / ('stage-' + profile)
                stage_legal_provenance(source, REPOSITORY_ROOT, stage, profile)
                names = {path.name for path in (stage / 'LICENSES').iterdir()}
                self.assertIn('FFmpeg-LICENSE.md', names)
                self.assertIn(required, names)
                self.assertIn('LarixFFmpegSDK-MIT.txt', names)
                self.assertNotIn(forbidden, names)
                self.assertTrue((stage / 'share/larix-ffmpeg-sdk/BUILD.txt').is_file())
                self.assertEqual(
                    (stage / 'share/larix-ffmpeg-sdk/provenance/config/gpl.conf').read_bytes(),
                    (REPOSITORY_ROOT / 'config/profiles/gpl.conf').read_bytes())
                self.assertEqual(
                    (stage / 'share/larix-ffmpeg-sdk/provenance/patches/README.md').read_bytes(),
                    (REPOSITORY_ROOT / 'patches/9.0.1/README.md').read_bytes())

    def test_rejects_zero_pdb_and_embedded_build_paths(self) -> None:
        for path in (self.sdk / "symbols").glob("*.pdb"):
            path.unlink()
        with self.assertRaises(ValueError):
            generate_release_metadata(
                self.sdk,
                REPOSITORY_ROOT,
                "lgpl",
                TARGET,
                toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
                runtime_dependencies={path: ["KERNEL32.dll"] for path in manifest_runtime()},
                forbidden_paths=(str(self.sdk.parent),),
            )
        create_sdk(self.sdk)
        (self.sdk / "symbols" / "avcodec-63.pdb").write_bytes(
            (str(self.sdk.parent) + "\\source.c").encode("utf-8")
        )
        with self.assertRaises(ValueError):
            generate_release_metadata(
                self.sdk,
                REPOSITORY_ROOT,
                "lgpl",
                TARGET,
                toolchain={"compiler": "MSVC 19.44", "windowsSdk": "10.0.26100.0"},
                runtime_dependencies={path: ["KERNEL32.dll"] for path in manifest_runtime()},
                forbidden_paths=(str(self.sdk.parent),),
            )


class FFprobeReviewTests(unittest.TestCase):
    def test_runs_packaged_ffprobe_and_requires_video_audio_timing_metadata(self) -> None:
        video = {
            "format": {"format_name": "avi", "duration": "0.040000"},
            "streams": [{"codec_type": "video", "codec_name": "rawvideo", "width": 2, "height": 2, "pix_fmt": "bgr24", "r_frame_rate": "25/1", "avg_frame_rate": "25/1", "time_base": "1/25", "nb_frames": "1", "nb_read_frames": "1", "duration": "0.040000"}],
        }
        audio = {
            "format": {"format_name": "wav", "duration": "0.010000"},
            "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 1, "duration": "0.010000"}],
        }
        completed = [
            mock.Mock(returncode=0, stdout=json.dumps(video), stderr=""),
            mock.Mock(returncode=0, stdout=json.dumps(audio), stderr=""),
        ]
        with mock.patch("subprocess.run", side_effect=completed) as run:
            verify_ffprobe_inputs(Path("sdk/bin/ffprobe.exe"), Path("video.avi"), Path("audio.wav"), {})
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all("-show_streams" in call.args[0] and "-show_format" in call.args[0] for call in run.call_args_list))
        self.assertTrue(all("-count_frames" in call.args[0] for call in run.call_args_list))
        self.assertTrue(all(call.args[0][0] == "sdk/bin/ffprobe.exe" for call in run.call_args_list))
        mutations = (
            ("r_frame_rate", "24/1"),
            ("avg_frame_rate", "0/0"),
            ("time_base", "1/1000"),
            ("nb_frames", "2"),
            ("nb_read_frames", "2"),
            ("duration", "0.080000"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                invalid = json.loads(json.dumps(video))
                invalid["streams"][0][field] = value
                results = [
                    mock.Mock(returncode=0, stdout=json.dumps(invalid), stderr=""),
                    mock.Mock(returncode=0, stdout=json.dumps(audio), stderr=""),
                ]
                with mock.patch("subprocess.run", side_effect=results):
                    with self.assertRaises(ValueError):
                        verify_ffprobe_inputs(
                            Path("sdk/bin/ffprobe.exe"),
                            Path("video.avi"),
                            Path("audio.wav"),
                            {},
                        )
        missing_count = json.loads(json.dumps(video))
        del missing_count["streams"][0]["nb_read_frames"]
        results = [
            mock.Mock(returncode=0, stdout=json.dumps(missing_count), stderr=""),
            mock.Mock(returncode=0, stdout=json.dumps(audio), stderr=""),
        ]
        with mock.patch("subprocess.run", side_effect=results):
            with self.assertRaises(ValueError):
                verify_ffprobe_inputs(
                    Path("sdk/bin/ffprobe.exe"),
                    Path("video.avi"),
                    Path("audio.wav"),
                    {},
                )
        invalid_format = json.loads(json.dumps(video))
        invalid_format["format"]["duration"] = "0.080000"
        results = [
            mock.Mock(returncode=0, stdout=json.dumps(invalid_format), stderr=""),
            mock.Mock(returncode=0, stdout=json.dumps(audio), stderr=""),
        ]
        with mock.patch("subprocess.run", side_effect=results):
            with self.assertRaises(ValueError):
                verify_ffprobe_inputs(
                    Path("sdk/bin/ffprobe.exe"),
                    Path("video.avi"),
                    Path("audio.wav"),
                    {},
                )


def manifest_runtime() -> tuple[str, ...]:
    return (
        "bin/avcodec-63.dll", "bin/avformat-63.dll", "bin/avutil-61.dll",
        "bin/ffprobe.exe", "bin/swresample-7.dll", "bin/swscale-10.dll",
    )


if __name__ == "__main__":
    unittest.main()
