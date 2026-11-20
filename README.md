# Larix FFmpeg SDK

LarixFFmpegSDK pins the official FFmpeg source identity and owns the
reproducible build, verification, packaging, and publication process for the
Larix SDK.

The [MIT license](LICENSE) applies to original files in this repository only.
FFmpeg source and derived binaries remain subject to their applicable LGPL or
GPL terms.

## Current release

[`ffmpeg-9.0.1-larix.2`](https://github.com/AlexBianzd/LarixFFmpegSDK/releases/tag/ffmpeg-9.0.1-larix.2)
provides LGPL and GPL shared-library SDKs for Windows x64 MSVC and macOS arm64,
with CPU encoding and HDR processing. The Release includes four hash-locked
upstream source archives, a release catalog, and top-level checksums. See the
[version release report](docs/reports/2026-11-18-ffmpeg-9.0.1-larix.2-release.md)
for the included libraries, capability boundaries, exact assets, hashes,
toolchains, and verification evidence.

## Repository map

| Path | Purpose |
| --- | --- |
| [`config/`](config/) | Source and dependency locks, license profiles, target ABIs, and JSON schemas |
| [`scripts/`](scripts/) | Public build commands, platform drivers, and shared packaging and verification tools |
| [`cmake/`](cmake/) | Installed CMake package template and out-of-tree zimg build adapter |
| [`tests/`](tests/) | Python contract tests, native C consumers, and media fixtures |
| [`.github/workflows/`](.github/workflows/) | Pull request checks, full native verification, and tag-based publication |
| [`licenses/`](licenses/) | Notices for bundled external dependencies |
| [`patches/`](patches/) | Empty FFmpeg patch set, recorded in SDK provenance |
| [`docs/reports/`](docs/reports/) | Version release reports |

Build output defaults to the ignored `build/` directory. Keep local inspection
results and working notes under the ignored `artifacts/` directory.

## Source lock

The exact approved FFmpeg archive is recorded in
[`config/ffmpeg.lock.json`](config/ffmpeg.lock.json). Consumers must treat the
lock as fail-closed: its URL, archive name, size, SHA-256, release identity,
and license profiles must all match exactly.

## CPU encoding and HDR processing (revision 2)

| Capability | LGPL SDK | GPL SDK |
| --- | --- | --- |
| H.264 and HEVC software decoding | Included | Included |
| H.264 software encoding | — | x264, 8/10-bit 4:2:0 |
| HEVC software encoding | — | x265, 8/10/12-bit |
| HEVC HDR10/HLG encoding | — | x265 Main 10 |
| CPU HDR-to-SDR and SDR-to-PQ/HLG conversion | avfilter + zimg | avfilter + zimg |

Both profiles statically link zimg into avfilter. The GPL profile also links
x264 and x265 into avcodec. The pinned 12-bit x265 implementation keeps
capability probes from loading a system encoder. See the
[dependency lock](config/dependencies.lock.json) and the packaged SBOM for exact
sources and licenses.

The native `libx264` encoder covers 8-bit Baseline/Main/High and 10-bit High 10.
The `libx265` encoder covers HEVC Main/Main 10/Main 12. CPU `zscale`, `tonemap` and
`exposure` filters support the documented HDR10/HLG and SDR conversion paths.
All builds exclude GPU processing, hardware codecs and display/player modules.
The C consumers in `tests/consumer` exercise encoding, delayed-frame flush,
MP4/Matroska mux/demux, decoded-frame re-encoding and numeric filter output.

HDR10+ re-encoding requires explicit frame-matched JSON input to x265;
FFmpeg 9.0.1 does not automatically forward dynamic HDR frame side data through
its x265 wrapper. Dolby Vision support is limited to upstream RPU handling and
profile 8.1/8.4 compatibility-base paths. CPU profile 5 reshaping, complete
profile 7/FEL enhancement-layer coding, display rendering and Dolby certification
are not provided. Processing pixels invalidates old grading metadata; the
consumer example removes it explicitly. SDR-to-HDR conversion cannot recreate
highlight detail absent from the SDR input.

## Windows x64 MSVC build

```powershell
./scripts/build-windows.ps1 -Profile lgpl -Configuration Release -OutputRoot build/windows-lgpl
./scripts/build-windows.ps1 -Profile gpl -Configuration Release -OutputRoot build/windows-gpl
```

Use `-Profile gpl` for the explicit GPL profile. The driver requires Visual
Studio 2022 C++ tools, NASM, MSYS2 Bash, GNU make and diffutils, plus
MSYS2 pkgconf, CMake 3.25 through 3.x, and Python 3.12 or newer. Install these tools before
building. Optional
`LARIX_NASM`, `LARIX_MSYS2_BASH`, `LARIX_MSYS2_MAKE`, `LARIX_CMAKE`, and
`LARIX_PYTHON` variables may point to existing installations.

Each ZIP contains shared `avutil`, `avcodec`, `avformat`, `avfilter`,
`swresample`, and `swscale` libraries, `ffprobe.exe`, public headers, MSVC import
libraries, relocatable `LarixFFmpegSDK::*` CMake targets, license/source provenance,
profile-correct FFmpeg license texts, PDB symbols, canonical metadata, an
SPDX 2.3 SBOM, and `SHA256SUMS`. Packaging and verification reject undeclared
files, static archives, forbidden FFmpeg components, unsafe archive paths, and
MSYS2/MinGW runtime dependencies.
Release manifests record stable SOURCE, BUILD, INSTALL, and OUTPUT aliases;
the Windows driver substitutes actual paths only for compilation so local
machine paths do not enter release provenance.

## macOS arm64 build

Run these commands on an Apple Silicon Mac. macOS Intel and universal2 builds
are intentionally unsupported; every dylib and executable targets arm64 with
an exact minimum deployment target of macOS 12.0.

```bash
./scripts/build-macos.sh --profile lgpl --configuration Release --output-root build/macos-lgpl
./scripts/build-macos.sh --profile gpl --configuration Release --output-root build/macos-gpl
```

The driver requires Xcode with Apple Clang, `make`, `pkg-config`, CMake 3.25
through 3.x, and Python 3.12 or newer. Install these tools before building. Each
deterministic `.tar.xz` contains the six versioned shared dylibs, `ffprobe`,
public headers, relocatable `LarixFFmpegSDK::*` CMake targets, exact legal and
source provenance, canonical manifest/SBOM/checksums, and no static archives.
Verification uses `file`, `otool`, and `vtool` to require arm64, macOS 12.0,
SDK-relative `@rpath` install names, and only packaged FFmpeg or allowlisted
system dependencies before building and running the relocated C consumer.

## Use and verify an SDK package

Extract the package for your platform and license profile. Pass the extracted
root to CMake with `-DCMAKE_PREFIX_PATH=/path/to/extracted/sdk` and link to
the installed targets.

```cmake
find_package(LarixFFmpegSDK CONFIG REQUIRED)
target_link_libraries(my_app PRIVATE
  LarixFFmpegSDK::avformat LarixFFmpegSDK::avcodec LarixFFmpegSDK::avutil)
```

For HDR processing, also link `LarixFFmpegSDK::avfilter`. On Windows, deploy
the package DLLs alongside your executable or put the SDK's `bin/` directory
on the runtime search path. On macOS, make the SDK's `lib/` directory
available through your application's runtime search paths for `@rpath`.

Run the contract suite from the repository root:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

On the package's native platform, the SDK verifier checks the archive,
relocates it, and builds and runs the C consumers. On Windows
and macOS, the verifier requires the toolchain versions recorded in the package
manifest.

```bash
python -m scripts.common.verify_sdk --archive path/to/sdk-archive
```

## GitHub verification

Pull requests run only the lightweight Python contract suite on the standard
public `windows-2022` runner. Maintainers can manually dispatch the `Verify
SDK` workflow with `full=true` to build all four LGPL/GPL Windows x64 and
macOS arm64 packages on standard `windows-2022` and `macos-15` runners.

```bash
gh workflow run verify.yml --repo AlexBianzd/LarixFFmpegSDK --ref master -f full=true
gh run watch --repo AlexBianzd/LarixFFmpegSDK --exit-status
```

The workflow has read-only repository permissions, uses SHA-pinned official
GitHub actions, and retains intermediate SDK archives for one day. It does not
use larger, paid, or self-hosted runners and does not publish release assets.

## Immutable releases

The revision 2 tag `ffmpeg-9.0.1-larix.2` builds all four target/profile SDKs again
on standard public runners. Only the final publication job receives
`contents: write`. Build and catalog jobs have read-only repository access.
The public Release contains four SDK archives, four pinned source archives
(FFmpeg, x264, x265 and zimg), a canonical `release-catalog.json`, and
top-level `SHA256SUMS`. A correction requires a new packaging revision.

Anyone can verify the Release without GitHub credentials:

```bash
python -m scripts.common.verify_release \
  --github-release AlexBianzd/LarixFFmpegSDK@ffmpeg-9.0.1-larix.2 \
  --anonymous
```

The verifier uses the checked-out source and dependency locks. To verify a
historical release, use a separate checkout of its matching tag.
