# LarixFFmpegSDK Repository Instructions

This public repository prepares reproducible FFmpeg SDK builds for Larix.

- Do not enable paid GitHub features, paid runners, paid storage, GitHub
  Packages, or third-party paid services.
- Do not register or use self-hosted runners.
- Do not rewrite published history or force-push.
- Keep the FFmpeg source archive and release assets explicitly versioned and
  content-hash checked. The FFmpeg patch set must remain empty. Never use
  `latest` or a hidden source clone fallback.
- Use only the pinned, unmodified upstream FFmpeg source. Do not modify FFmpeg
  sources, including through build-time patches, custom decoder
  instrumentation, or custom exports. Packaging and platform tooling may
  change only when it leaves the FFmpeg source tree unaltered.
- The repository's MIT license covers only original repository material.
  FFmpeg source and derived binaries retain their applicable LGPL or GPL
  obligations.
