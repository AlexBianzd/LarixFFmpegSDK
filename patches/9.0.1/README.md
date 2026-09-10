# FFmpeg 9.0.1 Patches

Patch files in this directory are ordered lexicographically, hashed into the
preparation state, checked with `git apply --check`, and then applied.

1. `0001-video-presentation-evidence.patch` adds the versioned immutable
   frame-side-data transport and reader in `libavutil`. It does not add codec
   producers or claim native presentation admission.
2. `0002-ffv1-presentation-evidence.patch` snapshots FFV1 version, micro-version
   and supplied context order; software slice workers collect picture structure
   separately and the decoder checks complete slice coverage and agreement
   before attaching an immutable record. Damaged, missing or conflicting slices
   cannot supply an unqualified picture structure. Attachment failure prevents
   output publication. Hardware paths without slice observations keep applicable
   header/context facts and qualify unobserved picture syntax as incomplete.

The `.2` package identity is an unpublished accumulation. These patches do not
change the five shared-library ABI majors or implement MPEG4 evidence or Larix
native presentation admission. Native Windows and hardware decode validation
remain separate verification boundaries.

The Release `presentation_ffv1` consumer test uses the installed public SDK to
encode/decode real v1/v3 pictures, mutate v3 slice syntax with CRC repair, and
exercise damage, loss, allocation failure, frame/slice threading and retained
frame lifetime. Its header mutation helper is LGPL-2.1-or-later, as identified in
`tests/consumer/presentation_codecs.c`; it is not part of the production parser.
