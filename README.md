# Larix FFmpeg SDK

> **Pre-release:** The reproducible build and verification tooling is under
> active development. No formal SDK release has been published yet.

LarixFFmpegSDK pins the official FFmpeg source identity and will own the
reproducible build, verification, packaging, and publication process for the
Larix SDK.

The [MIT license](LICENSE) applies to original files in this repository only.
FFmpeg source and derived binaries remain subject to their applicable LGPL or
GPL terms.

## Source lock

The exact approved FFmpeg archive is recorded in
[`config/ffmpeg.lock.json`](config/ffmpeg.lock.json). Consumers must treat the
lock as fail-closed: its URL, archive name, size, SHA-256, release identity,
and license profiles must all match exactly.

## Windows x64 MSVC build

```powershell
./scripts/build-windows.ps1 -Profile lgpl -Configuration Release -OutputRoot build/windows-lgpl
./scripts/build-windows.ps1 -Profile gpl -Configuration Release -OutputRoot build/windows-gpl
```

Use `-Profile gpl` for the explicit GPL profile. The driver requires Visual
Studio 2022 C++ tools, NASM, MSYS2 Bash, GNU make and diffutils, CMake 3.25 or newer, and
Python 3.12 or newer. It never downloads or installs tools. Optional
`LARIX_NASM`, `LARIX_MSYS2_BASH`, `LARIX_MSYS2_MAKE`, `LARIX_CMAKE`, and
`LARIX_PYTHON` variables may point to existing installations.

Each ZIP contains shared `avutil`, `avcodec`, `avformat`, `swresample`, and
`swscale` libraries, `ffprobe.exe`, public headers, MSVC import libraries,
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

The driver requires Xcode Command Line Tools with Apple Clang, `make`, CMake
3.25 or newer, and Python 3.12 or newer. It does not install tools. Each
deterministic `.tar.xz` contains the five versioned shared dylibs, `ffprobe`,
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
