# Larix FFmpeg SDK

> **Pre-release:** This repository is being prepared for reproducible Larix
> FFmpeg SDK builds. It does not yet publish SDK binaries or a formal release.

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
