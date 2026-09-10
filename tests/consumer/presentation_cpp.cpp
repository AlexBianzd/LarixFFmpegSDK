#include <cerrno>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <type_traits>

extern "C" {
#include <libavutil/error.h>
#include <libavutil/frame.h>
}
#include <libavutil/larix_video_presentation.h>

#define REQUIRE(value) do { \
    if (!(value)) { std::fprintf(stderr, "failed: %s\n", #value); std::abort(); } \
} while (0)

static_assert(std::is_standard_layout_v<AVLarixFFV1PresentationV1>);
static_assert(sizeof(AVLarixFFV1PresentationV1) == 32);
static_assert(offsetof(AVLarixFFV1PresentationV1, present) == 0);
static_assert(offsetof(AVLarixFFV1PresentationV1, version) == 4);
static_assert(offsetof(AVLarixFFV1PresentationV1, micro_version) == 8);
static_assert(offsetof(AVLarixFFV1PresentationV1, supplied_field_order) == 12);
static_assert(offsetof(AVLarixFFV1PresentationV1, picture_structure) == 16);
static_assert(offsetof(AVLarixFFV1PresentationV1, reserved) == 20);

static_assert(std::is_standard_layout_v<AVLarixMPEG4PresentationV1>);
static_assert(sizeof(AVLarixMPEG4PresentationV1) == 96);
static_assert(offsetof(AVLarixMPEG4PresentationV1, present) == 0);
static_assert(offsetof(AVLarixMPEG4PresentationV1, path) == 4);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vos_profile) == 8);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vos_level) == 12);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vol_object_type) == 16);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vol_interlaced) == 20);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_progressive_sequence) == 24);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_progressive_frame) == 28);
static_assert(offsetof(AVLarixMPEG4PresentationV1, top_field_first) == 32);
static_assert(offsetof(AVLarixMPEG4PresentationV1, repeat_first_field) == 36);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_structure) == 40);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_coded) == 44);
static_assert(offsetof(AVLarixMPEG4PresentationV1, time_increment_resolution) == 48);
static_assert(offsetof(AVLarixMPEG4PresentationV1, fixed_vop_rate) == 52);
static_assert(offsetof(AVLarixMPEG4PresentationV1, fixed_vop_time_increment) == 56);
static_assert(offsetof(AVLarixMPEG4PresentationV1, modulo_time_base) == 60);
static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_time_increment) == 64);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_frame_rate_code) == 68);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_time_code_high) == 72);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_time_code_low) == 76);
static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_temporal_reference) == 80);
static_assert(offsetof(AVLarixMPEG4PresentationV1, reserved) == 84);

static_assert(std::is_standard_layout_v<AVLarixVideoPresentationV1>);
static_assert(sizeof(AVLarixVideoPresentationV1) == 112);
static_assert(offsetof(AVLarixVideoPresentationV1, version) == 0);
static_assert(offsetof(AVLarixVideoPresentationV1, byte_size) == 4);
static_assert(offsetof(AVLarixVideoPresentationV1, syntax) == 8);
static_assert(offsetof(AVLarixVideoPresentationV1, qualifiers) == 12);
static_assert(offsetof(AVLarixVideoPresentationV1, facts) == 16);

int main(void)
{
#if defined(LARIX_PRESENTATION_FORCE_FAILURE)
    REQUIRE(false && "Release REQUIRE witness");
#endif
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != nullptr);
    AVLarixVideoPresentationV1 output;
    AVLarixVideoPresentationV1 before;
    std::memset(&output, 0xa5, sizeof(output));
    std::memcpy(&before, &output, sizeof(before));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, sizeof(output)) ==
            AVERROR(ENOENT));
    REQUIRE(std::memcmp(&before, &output, sizeof(output)) == 0);
    av_frame_free(&frame);
    return 0;
}
