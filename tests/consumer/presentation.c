#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavutil/buffer.h>
#include <libavutil/error.h>
#include <libavutil/frame.h>
#include <libavutil/larix_video_presentation.h>
#include <libavutil/mem.h>
#include <libavutil/pixfmt.h>

#define REQUIRE(value) do { \
    if (!(value)) { fprintf(stderr, "failed: %s\n", #value); abort(); } \
} while (0)

_Static_assert(sizeof(AVLarixFFV1PresentationV1) == 32, "FFV1 V1 size");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, present) == 0, "FFV1 present");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, version) == 4, "FFV1 version");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, micro_version) == 8, "FFV1 micro version");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, supplied_field_order) == 12, "FFV1 supplied order");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, picture_structure) == 16, "FFV1 picture structure");
_Static_assert(offsetof(AVLarixFFV1PresentationV1, reserved) == 20, "FFV1 reserved");

_Static_assert(sizeof(AVLarixMPEG4PresentationV1) == 96, "MPEG4 V1 size");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, present) == 0, "MPEG4 present");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, path) == 4, "MPEG4 path");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vos_profile) == 8, "MPEG4 profile");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vos_level) == 12, "MPEG4 level");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vol_object_type) == 16, "MPEG4 object type");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vol_interlaced) == 20, "MPEG4 interlaced");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_progressive_sequence) == 24, "MPEG4 Studio sequence");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_progressive_frame) == 28, "MPEG4 Studio frame");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, top_field_first) == 32, "MPEG4 top first");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, repeat_first_field) == 36, "MPEG4 repeat first");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_structure) == 40, "MPEG4 VOP structure");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_coded) == 44, "MPEG4 coded");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, time_increment_resolution) == 48, "MPEG4 time resolution");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, fixed_vop_rate) == 52, "MPEG4 fixed rate");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, fixed_vop_time_increment) == 56, "MPEG4 fixed increment");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, modulo_time_base) == 60, "MPEG4 modulo time");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, vop_time_increment) == 64, "MPEG4 VOP time");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_frame_rate_code) == 68, "MPEG4 Studio rate");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_time_code_high) == 72, "MPEG4 time code high");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_time_code_low) == 76, "MPEG4 time code low");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, studio_temporal_reference) == 80, "MPEG4 temporal reference");
_Static_assert(offsetof(AVLarixMPEG4PresentationV1, reserved) == 84, "MPEG4 reserved");

_Static_assert(sizeof(AVLarixVideoPresentationV1) == 112, "presentation V1 size");
_Static_assert(offsetof(AVLarixVideoPresentationV1, version) == 0, "presentation version");
_Static_assert(offsetof(AVLarixVideoPresentationV1, byte_size) == 4, "presentation byte size");
_Static_assert(offsetof(AVLarixVideoPresentationV1, syntax) == 8, "presentation syntax");
_Static_assert(offsetof(AVLarixVideoPresentationV1, qualifiers) == 12, "presentation qualifiers");
_Static_assert(offsetof(AVLarixVideoPresentationV1, facts) == 16, "presentation facts");

static AVLarixVideoPresentationV1 make_record(uint32_t syntax)
{
    AVLarixVideoPresentationV1 record;
    memset(&record, 0, sizeof(record));
    record.version = AV_LARIX_VIDEO_PRESENTATION_VERSION_1;
    record.byte_size = (uint32_t)sizeof(record);
    record.syntax = syntax;
    return record;
}

static AVFrameSideData *attach_record(AVFrame *frame,
                                      const AVLarixVideoPresentationV1 *record,
                                      size_t size)
{
    AVFrameSideData *side_data = av_frame_new_side_data(
        frame, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION, size);
    if (side_data && record)
        memcpy(side_data->data, record, size < sizeof(*record) ? size : sizeof(*record));
    return side_data;
}

static void require_read(const AVFrame *frame,
                         const AVLarixVideoPresentationV1 *expected)
{
    AVLarixVideoPresentationV1 output;
    memset(&output, 0xa5, sizeof(output));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, sizeof(output)) == 0);
    REQUIRE(memcmp(&output, expected, sizeof(output)) == 0);
}

static void require_failure(const AVFrame *frame, int expected_error)
{
    AVLarixVideoPresentationV1 output, before;
    memset(&output, 0xa5, sizeof(output));
    memcpy(&before, &output, sizeof(before));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, sizeof(output)) ==
            expected_error);
    REQUIRE(memcmp(&output, &before, sizeof(output)) == 0);
}

static void require_record_failure(const AVLarixVideoPresentationV1 *record,
                                   size_t size, int expected_error)
{
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, record, size) != NULL);
    require_failure(frame, expected_error);
    av_frame_free(&frame);
}

static void set_ffv1_field(AVLarixVideoPresentationV1 *record,
                           unsigned index, uint32_t value)
{
    uint32_t fields[4];
    REQUIRE(index < 4);
    memcpy(fields, &record->facts.ffv1.version, sizeof(fields));
    fields[index] = value;
    memcpy(&record->facts.ffv1.version, fields, sizeof(fields));
}

static void set_mpeg4_field(AVLarixVideoPresentationV1 *record,
                            unsigned index, uint32_t value)
{
    uint32_t fields[19];
    REQUIRE(index < 19);
    memcpy(fields, &record->facts.mpeg4.vos_profile, sizeof(fields));
    fields[index] = value;
    memcpy(&record->facts.mpeg4.vos_profile, fields, sizeof(fields));
}

static void test_numeric_tags(void)
{
    REQUIRE(AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION == 37);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_VERSION_1 == 1);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1 == 1);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4 == 2);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_QUALIFIER_RECOVERED_SYNTAX == 1);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_QUALIFIER_INCOMPLETE_SYNTAX == 2);
    REQUIRE(AV_LARIX_VIDEO_PRESENTATION_QUALIFIER_REEXPOSED_PICTURE == 4);
    REQUIRE(AV_LARIX_FFV1_PRESENT_VERSION == (1U << 0));
    REQUIRE(AV_LARIX_FFV1_PRESENT_MICRO_VERSION == (1U << 1));
    REQUIRE(AV_LARIX_FFV1_PRESENT_SUPPLIED_FIELD_ORDER == (1U << 2));
    REQUIRE(AV_LARIX_FFV1_PRESENT_PICTURE_STRUCTURE == (1U << 3));
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_UNKNOWN == 0);
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_PROGRESSIVE == 1);
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_TT == 2);
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_BB == 3);
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_TB == 4);
    REQUIRE(AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_BT == 5);
    REQUIRE(AV_LARIX_MPEG4_PATH_ORDINARY == 1);
    REQUIRE(AV_LARIX_MPEG4_PATH_STUDIO == 2);
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOS_PROFILE == (1U << 0));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOS_LEVEL == (1U << 1));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOL_OBJECT_TYPE == (1U << 2));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOL_INTERLACED == (1U << 3));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_PROGRESSIVE_SEQUENCE == (1U << 4));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_PROGRESSIVE_FRAME == (1U << 5));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_TOP_FIELD_FIRST == (1U << 6));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_REPEAT_FIRST_FIELD == (1U << 7));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOP_STRUCTURE == (1U << 8));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOP_CODED == (1U << 9));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_TIME_INCREMENT_RESOLUTION == (1U << 10));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_FIXED_VOP_RATE == (1U << 11));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_FIXED_VOP_TIME_INCREMENT == (1U << 12));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_MODULO_TIME_BASE == (1U << 13));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_VOP_TIME_INCREMENT == (1U << 14));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_FRAME_RATE_CODE == (1U << 15));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_HIGH == (1U << 16));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_LOW == (1U << 17));
    REQUIRE(AV_LARIX_MPEG4_PRESENT_STUDIO_TEMPORAL_REFERENCE == (1U << 18));
}

static void test_arguments_absence_and_shape(void)
{
    AVFrame *frame = av_frame_alloc();
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    AVLarixVideoPresentationV1 output, before;
    REQUIRE(frame != NULL);
    require_failure(frame, AVERROR(ENOENT));

    memset(&output, 0xa5, sizeof(output));
    memcpy(&before, &output, sizeof(before));
    REQUIRE(av_larix_video_presentation_read_v1(NULL, &output, sizeof(output)) ==
            AVERROR(EINVAL));
    REQUIRE(memcmp(&output, &before, sizeof(output)) == 0);
    REQUIRE(av_larix_video_presentation_read_v1(frame, NULL, sizeof(output)) ==
            AVERROR(EINVAL));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, 0) == AVERROR(EINVAL));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, sizeof(output) - 1) ==
            AVERROR(EINVAL));
    REQUIRE(av_larix_video_presentation_read_v1(frame, &output, sizeof(output) + 1) ==
            AVERROR(EINVAL));
    REQUIRE(memcmp(&output, &before, sizeof(output)) == 0);
    av_frame_free(&frame);

    require_record_failure(&record, 32, AVERROR_INVALIDDATA);
    require_record_failure(&record, 96, AVERROR_INVALIDDATA);
    require_record_failure(&record, 111, AVERROR_INVALIDDATA);
    require_record_failure(&record, 113, AVERROR_INVALIDDATA);

    const uint32_t invalid_byte_sizes[] = {0, 111, 113};
    for (unsigned i = 0;
         i < sizeof(invalid_byte_sizes) / sizeof(*invalid_byte_sizes); ++i) {
        AVLarixVideoPresentationV1 invalid = record;
        invalid.byte_size = invalid_byte_sizes[i];
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }

    frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
    AVFrameSideData *duplicate = av_frame_new_side_data(
        frame, AV_FRAME_DATA_SEI_UNREGISTERED, sizeof(record));
    REQUIRE(duplicate != NULL);
    duplicate->type = AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION;
    memset(duplicate->data, 0xff, duplicate->size);
    require_failure(frame, AVERROR_INVALIDDATA);
    av_frame_free(&frame);
}

static void test_common_validation(void)
{
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
    require_read(frame, &record);
    av_frame_free(&frame);

    for (uint32_t qualifier = 1; qualifier <= 4; qualifier <<= 1) {
        AVLarixVideoPresentationV1 qualified = record;
        qualified.qualifiers = qualifier;
        frame = av_frame_alloc();
        REQUIRE(frame != NULL);
        REQUIRE(attach_record(frame, &qualified, sizeof(qualified)) != NULL);
        require_read(frame, &qualified);
        av_frame_free(&frame);
    }
    record.qualifiers = 7;
    frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
    require_read(frame, &record);
    av_frame_free(&frame);
    record.qualifiers = 8;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    record.version = 2;
    require_record_failure(&record, sizeof(record), AVERROR(ENOSYS));
    record = make_record(3);
    require_record_failure(&record, sizeof(record), AVERROR(ENOSYS));
}

static void test_ffv1_validation(void)
{
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    AVFrame *frame;

    record.facts.ffv1.present = AV_LARIX_FFV1_PRESENT_VERSION |
        AV_LARIX_FFV1_PRESENT_MICRO_VERSION |
        AV_LARIX_FFV1_PRESENT_SUPPLIED_FIELD_ORDER |
        AV_LARIX_FFV1_PRESENT_PICTURE_STRUCTURE;
    record.facts.ffv1.version = 3;
    record.facts.ffv1.micro_version = 4;
    record.facts.ffv1.supplied_field_order = AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_BT;
    record.facts.ffv1.picture_structure = UINT32_MAX;
    frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
    require_read(frame, &record);
    av_frame_free(&frame);

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    record.facts.ffv1.present = AV_LARIX_FFV1_PRESENT_SUPPLIED_FIELD_ORDER;
    record.facts.ffv1.supplied_field_order = AV_LARIX_FFV1_SUPPLIED_FIELD_ORDER_UNKNOWN;
    frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
    require_read(frame, &record);
    av_frame_free(&frame);

    for (uint32_t order = 0; order <= 5; ++order) {
        record.facts.ffv1.supplied_field_order = order;
        frame = av_frame_alloc();
        REQUIRE(frame != NULL);
        REQUIRE(attach_record(frame, &record, sizeof(record)) != NULL);
        require_read(frame, &record);
        av_frame_free(&frame);
    }
    record.facts.ffv1.supplied_field_order = 6;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    record.facts.ffv1.present = 1U << 4;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);

    for (unsigned index = 0; index < 4; ++index) {
        AVLarixVideoPresentationV1 absent = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
        set_ffv1_field(&absent, index, 1);
        require_record_failure(&absent, sizeof(absent), AVERROR_INVALIDDATA);
    }
    for (unsigned index = 0; index < 3; ++index) {
        AVLarixVideoPresentationV1 reserved = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
        reserved.facts.ffv1.reserved[index] = 1;
        require_record_failure(&reserved, sizeof(reserved), AVERROR_INVALIDDATA);
    }
    for (unsigned index = 8; index < 24; ++index) {
        AVLarixVideoPresentationV1 unused = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
        unused.facts.storage[index] = 1;
        require_record_failure(&unused, sizeof(unused), AVERROR_INVALIDDATA);
    }

    for (uint32_t bit = AV_LARIX_FFV1_PRESENT_MICRO_VERSION;
         bit <= AV_LARIX_FFV1_PRESENT_PICTURE_STRUCTURE; bit <<= 2) {
        AVLarixVideoPresentationV1 dependent = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
        dependent.facts.ffv1.present = bit;
        require_record_failure(&dependent, sizeof(dependent), AVERROR_INVALIDDATA);
        dependent.facts.ffv1.present |= AV_LARIX_FFV1_PRESENT_VERSION;
        dependent.facts.ffv1.version = 2;
        require_record_failure(&dependent, sizeof(dependent), AVERROR_INVALIDDATA);
        dependent.facts.ffv1.version = 3;
        frame = av_frame_alloc();
        REQUIRE(frame != NULL);
        REQUIRE(attach_record(frame, &dependent, sizeof(dependent)) != NULL);
        require_read(frame, &dependent);
        av_frame_free(&frame);
    }
}

static AVLarixVideoPresentationV1 make_ordinary_record(void)
{
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
    record.facts.mpeg4.present =
        AV_LARIX_MPEG4_PRESENT_VOS_PROFILE |
        AV_LARIX_MPEG4_PRESENT_VOS_LEVEL |
        AV_LARIX_MPEG4_PRESENT_VOL_OBJECT_TYPE |
        AV_LARIX_MPEG4_PRESENT_VOL_INTERLACED |
        AV_LARIX_MPEG4_PRESENT_TOP_FIELD_FIRST |
        AV_LARIX_MPEG4_PRESENT_VOP_CODED |
        AV_LARIX_MPEG4_PRESENT_TIME_INCREMENT_RESOLUTION |
        AV_LARIX_MPEG4_PRESENT_FIXED_VOP_RATE |
        AV_LARIX_MPEG4_PRESENT_FIXED_VOP_TIME_INCREMENT |
        AV_LARIX_MPEG4_PRESENT_MODULO_TIME_BASE |
        AV_LARIX_MPEG4_PRESENT_VOP_TIME_INCREMENT;
    record.facts.mpeg4.vos_profile = 15;
    record.facts.mpeg4.vos_level = 15;
    record.facts.mpeg4.vol_object_type = 255;
    record.facts.mpeg4.vol_interlaced = 1;
    record.facts.mpeg4.top_field_first = 1;
    record.facts.mpeg4.vop_coded = 1;
    record.facts.mpeg4.time_increment_resolution = 65535;
    record.facts.mpeg4.fixed_vop_rate = 1;
    record.facts.mpeg4.fixed_vop_time_increment = UINT32_MAX;
    record.facts.mpeg4.modulo_time_base = UINT32_MAX;
    record.facts.mpeg4.vop_time_increment = UINT32_MAX;
    return record;
}

static AVLarixVideoPresentationV1 make_studio_record(void)
{
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_STUDIO;
    record.facts.mpeg4.present =
        AV_LARIX_MPEG4_PRESENT_VOS_PROFILE |
        AV_LARIX_MPEG4_PRESENT_VOS_LEVEL |
        AV_LARIX_MPEG4_PRESENT_VOL_OBJECT_TYPE |
        AV_LARIX_MPEG4_PRESENT_STUDIO_PROGRESSIVE_SEQUENCE |
        AV_LARIX_MPEG4_PRESENT_STUDIO_PROGRESSIVE_FRAME |
        AV_LARIX_MPEG4_PRESENT_TOP_FIELD_FIRST |
        AV_LARIX_MPEG4_PRESENT_REPEAT_FIRST_FIELD |
        AV_LARIX_MPEG4_PRESENT_VOP_STRUCTURE |
        AV_LARIX_MPEG4_PRESENT_VOP_CODED |
        AV_LARIX_MPEG4_PRESENT_STUDIO_FRAME_RATE_CODE |
        AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_HIGH |
        AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_LOW |
        AV_LARIX_MPEG4_PRESENT_STUDIO_TEMPORAL_REFERENCE;
    record.facts.mpeg4.vos_profile = 15;
    record.facts.mpeg4.vos_level = 15;
    record.facts.mpeg4.vol_object_type = 255;
    record.facts.mpeg4.studio_progressive_sequence = 1;
    record.facts.mpeg4.studio_progressive_frame = 1;
    record.facts.mpeg4.top_field_first = 1;
    record.facts.mpeg4.repeat_first_field = 1;
    record.facts.mpeg4.vop_structure = 3;
    record.facts.mpeg4.vop_coded = 1;
    record.facts.mpeg4.studio_frame_rate_code = 15;
    record.facts.mpeg4.studio_time_code_high = UINT32_MAX;
    record.facts.mpeg4.studio_time_code_low = UINT32_MAX;
    record.facts.mpeg4.studio_temporal_reference = 1023;
    return record;
}

static void require_record_readable(const AVLarixVideoPresentationV1 *record)
{
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    REQUIRE(attach_record(frame, record, sizeof(*record)) != NULL);
    require_read(frame, record);
    av_frame_free(&frame);
}

static void test_mpeg4_validation(void)
{
    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
    require_record_readable(&record);
    require_record_readable(&(AVLarixVideoPresentationV1){
        .version = 1, .byte_size = 112, .syntax = 2,
        .facts.mpeg4 = {.path = AV_LARIX_MPEG4_PATH_STUDIO}});
    require_record_readable(&(AVLarixVideoPresentationV1){
        .version = 1, .byte_size = 112, .syntax = 2,
        .facts.mpeg4 = {
            .present = AV_LARIX_MPEG4_PRESENT_VOS_PROFILE |
                       AV_LARIX_MPEG4_PRESENT_VOS_LEVEL,
            .path = AV_LARIX_MPEG4_PATH_ORDINARY}});
    record = make_ordinary_record();
    require_record_readable(&record);
    record = make_studio_record();
    require_record_readable(&record);

    for (uint32_t path = 0; path <= 3; path += 3) {
        AVLarixVideoPresentationV1 invalid = record;
        invalid.facts.mpeg4.path = path;
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }
    record.facts.mpeg4.present = 1U << 19;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);

    for (unsigned index = 0; index < 19; ++index) {
        AVLarixVideoPresentationV1 absent = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        absent.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
        set_mpeg4_field(&absent, index, 1);
        require_record_failure(&absent, sizeof(absent), AVERROR_INVALIDDATA);
    }
    for (unsigned index = 0; index < 3; ++index) {
        AVLarixVideoPresentationV1 reserved = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        reserved.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
        reserved.facts.mpeg4.reserved[index] = 1;
        require_record_failure(&reserved, sizeof(reserved), AVERROR_INVALIDDATA);
    }

    const unsigned ordinary_forbidden[] = {4, 5, 7, 8, 15, 16, 17, 18};
    for (unsigned i = 0; i < sizeof(ordinary_forbidden) / sizeof(*ordinary_forbidden); ++i) {
        AVLarixVideoPresentationV1 invalid = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        invalid.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
        invalid.facts.mpeg4.present = 1U << ordinary_forbidden[i];
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }
    const unsigned studio_forbidden[] = {3, 10, 11, 12, 13, 14};
    for (unsigned i = 0; i < sizeof(studio_forbidden) / sizeof(*studio_forbidden); ++i) {
        AVLarixVideoPresentationV1 invalid = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        invalid.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_STUDIO;
        invalid.facts.mpeg4.present = 1U << studio_forbidden[i];
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }

    for (unsigned index = 0; index < 2; ++index) {
        AVLarixVideoPresentationV1 invalid = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        invalid.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
        invalid.facts.mpeg4.present = 1U << index;
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_STUDIO;
    record.facts.mpeg4.present = AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_HIGH;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.present = AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_LOW;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.present = AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_HIGH |
                                 AV_LARIX_MPEG4_PRESENT_STUDIO_TIME_CODE_LOW;
    require_record_readable(&record);

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
    record.facts.mpeg4.present = AV_LARIX_MPEG4_PRESENT_FIXED_VOP_TIME_INCREMENT;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.present |= AV_LARIX_MPEG4_PRESENT_FIXED_VOP_RATE;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.fixed_vop_rate = 1;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.present |= AV_LARIX_MPEG4_PRESENT_TIME_INCREMENT_RESOLUTION;
    require_record_readable(&record);

    record = make_record(AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
    record.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_ORDINARY;
    record.facts.mpeg4.present = AV_LARIX_MPEG4_PRESENT_TOP_FIELD_FIRST |
                                 AV_LARIX_MPEG4_PRESENT_VOL_INTERLACED |
                                 AV_LARIX_MPEG4_PRESENT_VOP_CODED;
    record.facts.mpeg4.vol_interlaced = 1;
    record.facts.mpeg4.vop_coded = 1;
    require_record_readable(&record);
    record.facts.mpeg4.vop_coded = 0;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);
    record.facts.mpeg4.vop_coded = 1;
    record.facts.mpeg4.vol_interlaced = 0;
    require_record_failure(&record, sizeof(record), AVERROR_INVALIDDATA);

    const unsigned studio_coded_fields[] = {5, 6, 7};
    for (unsigned i = 0; i < sizeof(studio_coded_fields) / sizeof(*studio_coded_fields); ++i) {
        AVLarixVideoPresentationV1 invalid = make_record(
            AV_LARIX_VIDEO_PRESENTATION_SYNTAX_MPEG4);
        invalid.facts.mpeg4.path = AV_LARIX_MPEG4_PATH_STUDIO;
        invalid.facts.mpeg4.present = (1U << studio_coded_fields[i]) |
                                      AV_LARIX_MPEG4_PRESENT_VOP_CODED;
        set_mpeg4_field(&invalid, studio_coded_fields[i], 1);
        invalid.facts.mpeg4.vop_coded = 0;
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
        invalid.facts.mpeg4.vop_coded = 1;
        require_record_readable(&invalid);
    }

    const unsigned ordinary_booleans[] = {3, 6, 9, 11};
    for (unsigned i = 0; i < sizeof(ordinary_booleans) / sizeof(*ordinary_booleans); ++i) {
        AVLarixVideoPresentationV1 invalid = make_ordinary_record();
        set_mpeg4_field(&invalid, ordinary_booleans[i], 2);
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }
    const unsigned studio_booleans[] = {4, 5, 6, 7, 9};
    for (unsigned i = 0; i < sizeof(studio_booleans) / sizeof(*studio_booleans); ++i) {
        AVLarixVideoPresentationV1 invalid = make_studio_record();
        set_mpeg4_field(&invalid, studio_booleans[i], 2);
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }

    struct RangeCase { unsigned index; uint32_t value; int studio; };
    const struct RangeCase ranges[] = {
        {2, 256, 0}, {8, 4, 1}, {10, 65536, 0}, {15, 16, 1}, {18, 1024, 1}
    };
    for (unsigned i = 0; i < sizeof(ranges) / sizeof(*ranges); ++i) {
        AVLarixVideoPresentationV1 invalid = ranges[i].studio ?
            make_studio_record() : make_ordinary_record();
        set_mpeg4_field(&invalid, ranges[i].index, ranges[i].value);
        require_record_failure(&invalid, sizeof(invalid), AVERROR_INVALIDDATA);
    }
}

static AVFrame *make_video_frame(void)
{
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    frame->format = AV_PIX_FMT_GRAY8;
    frame->width = 2;
    frame->height = 2;
    REQUIRE(av_frame_get_buffer(frame, 0) == 0);
    return frame;
}

static void test_unaligned_and_lifetime(void)
{
    AVLarixVideoPresentationV1 record = make_ordinary_record();
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    AVFrameSideData *side_data = av_frame_new_side_data(
        frame, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION, sizeof(record) + 1);
    REQUIRE(side_data != NULL);
    memcpy(side_data->data + 1, &record, sizeof(record));
    side_data->data += 1;
    side_data->size = sizeof(record);
    require_read(frame, &record);
    av_frame_free(&frame);

    AVFrame *source = make_video_frame();
    REQUIRE(attach_record(source, &record, sizeof(record)) != NULL);
    source->opaque_ref = av_buffer_alloc(4);
    REQUIRE(source->opaque_ref != NULL);
    source->opaque_ref->data[0] = 0x5a;

    AVFrame *reference = av_frame_alloc();
    REQUIRE(reference != NULL);
    REQUIRE(av_frame_ref(reference, source) == 0);
    AVFrame *clone = av_frame_clone(source);
    REQUIRE(clone != NULL);
    AVFrame *properties = av_frame_alloc();
    REQUIRE(properties != NULL);
    REQUIRE(av_frame_copy_props(properties, source) == 0);
    require_read(reference, &record);
    require_read(clone, &record);
    require_read(properties, &record);
    REQUIRE(reference->opaque_ref != NULL && reference->opaque_ref->data[0] == 0x5a);
    REQUIRE(clone->opaque_ref != NULL && clone->opaque_ref->data[0] == 0x5a);
    REQUIRE(properties->opaque_ref != NULL && properties->opaque_ref->data[0] == 0x5a);
    REQUIRE(av_frame_get_side_data(source, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION)->data ==
            av_frame_get_side_data(reference, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION)->data);
    REQUIRE(av_frame_get_side_data(source, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION)->data !=
            av_frame_get_side_data(properties, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION)->data);

    AVLarixVideoPresentationV1 output;
    REQUIRE(av_larix_video_presentation_read_v1(reference, &output, sizeof(output)) == 0);
    output.syntax = 0;
    require_read(source, &record);

    AVFrame *moved = av_frame_alloc();
    REQUIRE(moved != NULL);
    av_frame_move_ref(moved, properties);
    require_failure(properties, AVERROR(ENOENT));
    require_read(moved, &record);
    av_frame_unref(source);
    require_read(reference, &record);
    require_read(clone, &record);
    av_frame_unref(reference);
    require_read(clone, &record);

    AVFrame *image_source = make_video_frame();
    AVFrame *image_destination = make_video_frame();
    REQUIRE(attach_record(image_source, &record, sizeof(record)) != NULL);
    REQUIRE(av_frame_copy(image_destination, image_source) == 0);
    require_failure(image_destination, AVERROR(ENOENT));

    av_frame_free(&image_destination);
    av_frame_free(&image_source);
    av_frame_free(&moved);
    av_frame_free(&properties);
    av_frame_free(&clone);
    av_frame_free(&reference);
    av_frame_free(&source);
}

static void test_descriptor_singleton_and_attachment_failure(void)
{
    const AVSideDataDescriptor *descriptor = av_frame_side_data_desc(
        AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION);
    REQUIRE(descriptor != NULL);
    REQUIRE(descriptor->name != NULL);
    REQUIRE(descriptor->props == AV_SIDE_DATA_PROP_SIZE_DEPENDENT);

    AVLarixVideoPresentationV1 record = make_record(
        AV_LARIX_VIDEO_PRESENTATION_SYNTAX_FFV1);
    AVFrame *frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    AVFrameSideData *first = attach_record(frame, &record, sizeof(record));
    REQUIRE(first != NULL);
    REQUIRE(frame->nb_side_data == 1);
    require_read(frame, &record);
    av_frame_free(&frame);

    frame = av_frame_alloc();
    REQUIRE(frame != NULL);
    frame->opaque_ref = av_buffer_alloc(1);
    REQUIRE(frame->opaque_ref != NULL);
    AVBufferRef *opaque_before = frame->opaque_ref;
    av_max_alloc(sizeof(record) - 1);
    AVFrameSideData *failed = av_frame_new_side_data(
        frame, AV_FRAME_DATA_LARIX_VIDEO_PRESENTATION, sizeof(record));
    av_max_alloc(INT_MAX);
    REQUIRE(failed == NULL);
    REQUIRE(frame->nb_side_data == 0);
    REQUIRE(frame->opaque_ref == opaque_before);
    require_failure(frame, AVERROR(ENOENT));
    av_frame_free(&frame);
}

int main(void)
{
#if defined(LARIX_PRESENTATION_FORCE_FAILURE)
    REQUIRE(0 && "Release REQUIRE witness");
#endif
    test_numeric_tags();
    test_arguments_absence_and_shape();
    test_common_validation();
    test_ffv1_validation();
    test_mpeg4_validation();
    test_unaligned_and_lifetime();
    test_descriptor_singleton_and_attachment_failure();
    puts("presentation transport passed");
    return 0;
}
