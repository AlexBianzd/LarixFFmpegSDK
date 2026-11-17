# Larix FFmpeg SDK

LarixFFmpegSDK pins the official FFmpeg source identity and owns the
reproducible build, verification, packaging, and publication process for the
Larix SDK.

The [MIT license](LICENSE) applies to original files in this repository only.
FFmpeg source and derived binaries remain subject to their applicable LGPL or
GPL terms.

## Current release

[`ffmpeg-9.0.1-larix.1`](https://github.com/AlexBianzd/LarixFFmpegSDK/releases/tag/ffmpeg-9.0.1-larix.1)
is the first immutable public release. It contains LGPL and GPL shared-library
SDKs for Windows x64 MSVC and macOS arm64, plus the hash-locked FFmpeg source,
release catalog, and top-level checksums. See the
[durable release report](docs/reports/2026-08-13-ffmpeg-9.0.1-larix.1-release.md)
for exact assets, hashes, toolchains, verification evidence, and unsupported
platform boundaries.

## Source lock

The exact approved FFmpeg archive is recorded in
[`config/ffmpeg.lock.json`](config/ffmpeg.lock.json). Consumers must treat the
lock as fail-closed: its URL, archive name, size, SHA-256, release identity,
and license profiles must all match exactly.

## CPU encoding and HDR processing (revision 2)

Revision 2 retains unmodified FFmpeg 9.0.1 and adds `avfilter` plus hash-locked
zimg to both license profiles. The GPL profile also statically includes locked
x264 (8/10-bit) and x265 4.1 (8/10-bit multilib, dynamicHDR10 enabled for 10-bit).
No encoder static archives are shipped in the SDK. Exact sources and their
licenses are recorded in `config/dependencies.lock.json` and the package SBOM.

Use the native FFmpeg APIs: `libx264` covers 8-bit Baseline/Main/High and 10-bit
High 10; `libx265` covers HEVC Main/Main 10. CPU `zscale`, `tonemap` and
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
Studio 2022 C++ tools, NASM, MSYS2 Bash, GNU make and diffutils, CMake 3.25 or newer, pkgconf (MSYS2), and
Python 3.12 or newer. It never downloads or installs tools. Optional
`LARIX_NASM`, `LARIX_MSYS2_BASH`, `LARIX_MSYS2_MAKE`, `LARIX_CMAKE`, and
`LARIX_PYTHON` variables may point to existing installations.

Each ZIP contains shared `avutil`, `avcodec`, `avformat`, `swresample`, and
`swscale`, and `avfilter` libraries, `ffprobe.exe`, public headers, MSVC import libraries,
relocatable `LarixFFmpegSDK::*` CMake targets, license/source provenance,
profile-correct FFmpeg license texts, PDB symbols, canonical metadata, an
SPDX 2.3 SBOM, and `SHA256SUMS`. Packaging and
verification reject undeclared files, static libraries, forbidden FFmpeg
components, unsafe archive paths, and MSYS2/MinGW runtime dependencies.
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

The driver requires Xcode Command Line Tools with Apple Clang, `make`, `pkg-config`, CMake
3.25 or newer, and Python 3.12 or newer. It does not install tools. Each
deterministic `.tar.xz` contains the six versioned shared dylibs, `ffprobe`,
public headers, relocatable `LarixFFmpegSDK::*` CMake targets, exact legal and
source provenance, canonical manifest/SBOM/checksums, and no static archives.
Verification uses `file`, `otool`, and `vtool` to require arm64, macOS 12.0,
SDK-relative `@rpath` install names, and only packaged FFmpeg or allowlisted
system dependencies before building and running the relocated C consumer.

## GitHub verification

Pull requests run only the lightweight Python contract suite on the standard
public `ubuntu-24.04` runner. Maintainers can manually dispatch the `Verify
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
on standard public runners. Only the final aggregation job receives
`contents: write`; build jobs remain read-only. The public Release contains the
four SDK archives, the hash-locked official FFmpeg and x264/x265/zimg source archives, a canonical
`release-catalog.json`, and top-level `SHA256SUMS`. Existing releases or assets
are never overwritten; a correction requires a new packaging revision.

Anyone can verify the Release without GitHub credentials:

```bash
python -m scripts.common.verify_release \
  --github-release AlexBianzd/LarixFFmpegSDK@ffmpeg-9.0.1-larix.1 \
  --anonymous
```
