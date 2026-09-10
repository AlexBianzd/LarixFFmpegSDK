# FFmpeg 9.0.1 Patches

Patch files in this directory are ordered lexicographically, hashed into the
preparation state, checked with `git apply --check`, and then applied.

1. `0001-video-presentation-evidence.patch` adds the versioned immutable
   frame-side-data transport and reader in `libavutil`. It does not add codec
   producers or claim native presentation admission.
