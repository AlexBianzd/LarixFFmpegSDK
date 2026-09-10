/* SPDX-License-Identifier: LGPL-2.1-or-later
 * Range-coder helper portions copyright (c) 2004 Michael Niedermayer.
 * This file is distributed under the GNU Lesser General Public License,
 * version 2.1 or (at your option) any later version, without any warranty.
 * See FFmpeg's COPYING.LGPLv2.1 in the locked source archive for the license.
 */
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavutil/larix_video_presentation.h>
#include <libavutil/mem.h>
#include <libavutil/opt.h>
#include <libavutil/crc.h>
#include <libavutil/intreadwrite.h>

#define REQUIRE(v) do { if (!(v)) { fprintf(stderr, "%s:%d: failed: %s\n", __FILE__, __LINE__, #v); abort(); } } while (0)

typedef struct Witness {
    AVCodecContext *encoder;
    AVFrame *input;
    AVPacket *packet;
} Witness;

static Witness witness_with_coder(int version, int coder)
{
    Witness w = {0};
    w.encoder = avcodec_alloc_context3(avcodec_find_encoder(AV_CODEC_ID_FFV1));
    w.input = av_frame_alloc();
    w.packet = av_packet_alloc();
    REQUIRE(w.encoder && w.input && w.packet);
    w.encoder->width = w.encoder->height = 64;
    w.encoder->pix_fmt = AV_PIX_FMT_YUV420P;
    w.encoder->time_base = (AVRational){1, 25};
    w.encoder->level = version;
    w.encoder->gop_size = 1;
    w.encoder->thread_count = 1;
    w.encoder->slices = version == 3 ? 4 : 1;
    REQUIRE(av_opt_set_int(w.encoder->priv_data, "coder", coder, 0) == 0);
    REQUIRE(av_opt_set_int(w.encoder->priv_data, "context", 0, 0) == 0);
    REQUIRE(av_opt_set_int(w.encoder->priv_data, "slicecrc", version == 3, 0) == 0);
    REQUIRE(avcodec_open2(w.encoder, NULL, NULL) == 0);
    w.input->format = w.encoder->pix_fmt;
    w.input->width = w.input->height = 64;
    w.input->sample_aspect_ratio = (AVRational){1, 1};
    REQUIRE(av_frame_get_buffer(w.input, 32) == 0);
    return w;
}

static Witness witness(int version)
{
    return witness_with_coder(version, 0);
}

static void encode(Witness *w, unsigned structure, int index)
{
    REQUIRE(av_frame_make_writable(w->input) == 0);
    for (int p = 0; p < 3; p++) {
        int size = p ? 32 : 64;
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
                w->input->data[p][y*w->input->linesize[p]+x] = (x*3+y*5+p*41+index*7)&255;
    }
    w->input->flags = structure == 3 ? 0 : AV_FRAME_FLAG_INTERLACED |
        (structure == 1 ? AV_FRAME_FLAG_TOP_FIELD_FIRST : 0);
    w->input->pts = index * 3;
    w->input->duration = 3;
    av_packet_unref(w->packet);
    REQUIRE(avcodec_send_frame(w->encoder, w->input) == 0);
    REQUIRE(avcodec_receive_packet(w->encoder, w->packet) == 0);
    w->packet->pts = index * 3;
    w->packet->duration = 3;
}

static AVCodecContext *decoder(const Witness *w, enum AVFieldOrder order, int threads)
{
    AVCodecContext *d = avcodec_alloc_context3(avcodec_find_decoder(AV_CODEC_ID_FFV1));
    AVCodecParameters *par = avcodec_parameters_alloc();
    REQUIRE(d && par);
    REQUIRE(avcodec_parameters_from_context(par, w->encoder) == 0);
    REQUIRE(avcodec_parameters_to_context(d, par) == 0);
    avcodec_parameters_free(&par);
    d->field_order = order;
    d->thread_count = threads < 0 ? -threads : threads;
    d->thread_type = threads < 0 ? FF_THREAD_FRAME : FF_THREAD_SLICE;
    d->pkt_timebase = (AVRational){1, 25};
    REQUIRE(avcodec_open2(d, NULL, NULL) == 0);
    return d;
}

static void pixels(const AVFrame *f, int index)
{
    REQUIRE(f->format == AV_PIX_FMT_YUV420P && f->width == 64 && f->height == 64);
    REQUIRE(f->pts == index * 3 && f->duration == 3);
    for (int p = 0; p < 3; p++) {
        int size = p ? 32 : 64;
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++)
                REQUIRE(f->data[p][y*f->linesize[p]+x] == ((x*3+y*5+p*41+index*7)&255));
    }
}

static AVLarixVideoPresentationV1 facts(const AVFrame *f, int version, unsigned order,
                                       unsigned structure, unsigned qualifiers)
{
    AVLarixVideoPresentationV1 result;
    int ret = av_larix_video_presentation_read_v1(f, &result, sizeof(result));
    if (ret) fprintf(stderr, "decoded FFV1 v%d evidence reader returned %d\n", version, ret);
    REQUIRE(ret == 0);
    if (result.qualifiers != qualifiers)
        fprintf(stderr, "v%d order%u structure%u: qualifiers=%u expected=%u\n",
                version, order, structure, result.qualifiers, qualifiers);
    REQUIRE(result.syntax == 1 && result.qualifiers == qualifiers);
    REQUIRE(result.facts.ffv1.version == (unsigned)version);
    REQUIRE(result.facts.ffv1.present == (version == 3 ? (qualifiers ? 7u : 15u) : 5u));
    REQUIRE(result.facts.ffv1.micro_version == (version == 3 ? 4u : 0u));
    REQUIRE(result.facts.ffv1.supplied_field_order == order);
    REQUIRE(result.facts.ffv1.picture_structure == (version == 3 && !qualifiers ? structure : 0));
    return result;
}

static void release_witness(Witness *w)
{
    avcodec_free_context(&w->encoder);
    av_frame_free(&w->input);
    av_packet_free(&w->packet);
}

static void ordinary_matrix(void)
{
    const enum AVFieldOrder orders[] = {AV_FIELD_UNKNOWN, AV_FIELD_PROGRESSIVE,
        AV_FIELD_TT, AV_FIELD_BB, AV_FIELD_TB, AV_FIELD_BT};
    for (int version = 1; version <= 3; version += 2) {
        Witness w = witness(version);
        for (unsigned order = 0; order < 6; order++) {
            AVCodecContext *d = decoder(&w, orders[order], 4);
            AVFrame *f = av_frame_alloc(), *old = NULL;
            AVLarixVideoPresentationV1 saved;
            REQUIRE(f);
            for (unsigned structure = 1; structure <= 3; structure++) {
                encode(&w, structure, structure);
                REQUIRE(avcodec_send_packet(d, w.packet) == 0);
                REQUIRE(avcodec_receive_frame(d, f) == 0);
                pixels(f, structure);
                AVLarixVideoPresentationV1 current = facts(f, version, order, structure, 0);
                if (!old) { old = av_frame_clone(f); saved = current; REQUIRE(old); }
                av_frame_unref(f);
            }
            REQUIRE(avcodec_send_packet(d, NULL) == 0);
            REQUIRE(avcodec_receive_frame(d, f) == AVERROR_EOF);
            avcodec_flush_buffers(d);
            encode(&w, 3, 4);
            REQUIRE(avcodec_send_packet(d, w.packet) == 0);
            REQUIRE(avcodec_receive_frame(d, f) == 0);
            pixels(f, 4);
            facts(f, version, order, 3, 0);
            avcodec_free_context(&d);
            AVLarixVideoPresentationV1 retained = facts(old, version, order, 1, 0);
            REQUIRE(memcmp(&saved, &retained, sizeof(saved)) == 0);
            pixels(old, 1);
            av_frame_free(&old);
            av_frame_free(&f);
        }
        release_witness(&w);
    }
}

/* Test-only FFV1 v3 Golomb-Rice header writer. The range coding operations
 * below are adapted from FFmpeg libavcodec/rangecoder.{c,h}, copyright
 * Michael Niedermayer, under LGPL-2.1-or-later. Only the header is rewritten;
 * every encoded pixel payload is retained verbatim and its CRC repaired. */
typedef struct HeaderCoder {
    int low, range, pending, byte, count;
    uint8_t one[256], zero[256], data[128];
} HeaderCoder;

static void header_init(HeaderCoder *c)
{
    const int64_t one = INT64_C(1) << 32;
    const int factor = 214748364;
    int last = 0;
    int64_t p = one / 2;
    memset(c, 0, sizeof(*c));
    c->range = 0xff00;
    c->byte = -1;
    for (int i = 0; i < 128; i++) {
        int next = (256*p + one/2) >> 32;
        if (next <= last) next = last + 1;
        if (last && last < 256 && next <= 248) c->one[last] = next;
        p += ((one-p)*factor + one/2) >> 32;
        last = next;
    }
    for (int i = 8; i <= 248; i++) {
        if (c->one[i]) continue;
        p = (i*one + 128) >> 8;
        p += ((one-p)*factor + one/2) >> 32;
        int next = (256*p + one/2) >> 32;
        if (next <= i) next = i + 1;
        c->one[i] = next > 248 ? 248 : next;
    }
    for (int i = 1; i < 255; i++) c->zero[i] = 256-c->one[256-i];
}

static void header_byte(HeaderCoder *c, int value)
{
    REQUIRE(c->count < (int)sizeof(c->data));
    c->data[c->count++] = value;
}

static void header_renorm(HeaderCoder *c)
{
    if ((unsigned)(c->low-0xff01) >= 0xffu) {
        int mask = (c->low-0xff01) >> 31;
        if (c->byte >= 0) header_byte(c, c->byte+1+mask);
        while (c->pending) { header_byte(c, mask); c->pending--; }
        c->byte = c->low >> 8;
    } else c->pending++;
    c->low = (c->low & 255) << 8;
    c->range <<= 8;
}

static void header_bit(HeaderCoder *c, uint8_t *state, int bit)
{
    int range = (c->range * *state) >> 8;
    if (bit) { c->low += c->range-range; c->range = range; *state = c->one[*state]; }
    else { c->range -= range; *state = c->zero[*state]; }
    if (c->range < 256) header_renorm(c);
}

static void header_symbol(HeaderCoder *c, uint8_t *s, unsigned value)
{
    header_bit(c, s, value == 0);
    if (!value) return;
    unsigned exponent = 0;
    for (unsigned v = value; v >>= 1;) exponent++;
    for (unsigned i = 0; i < exponent; i++) header_bit(c, s+1+(i < 9 ? i : 9), 1);
    header_bit(c, s+1+(exponent < 9 ? exponent : 9), 0);
    for (int i = (int)exponent-1; i >= 0; i--)
        header_bit(c, s+22+(i < 9 ? i : 9), (value >> i)&1);
}

static HeaderCoder slice_header(unsigned slice, unsigned structure)
{
    HeaderCoder c;
    uint8_t s[32], key = 128, end = 129;
    header_init(&c);
    memset(s, 128, sizeof(s));
    if (!slice) header_bit(&c, &key, 1);
    header_symbol(&c, s, slice%2);
    header_symbol(&c, s, slice/2);
    for (int i = 0; i < 4; i++) header_symbol(&c, s, 0); /* extents and quant tables */
    header_symbol(&c, s, structure);
    header_symbol(&c, s, 1);
    header_symbol(&c, s, 1);
    header_bit(&c, &end, 0);
    c.range = 255; c.low += 255; header_renorm(&c);
    c.range = 255; header_renorm(&c);
    return c;
}

static AVPacket *rewrite_structures(const AVPacket *original, const unsigned structures[4])
{
    int starts[4], sizes[4], end = original->size;
    for (int i = 3; i >= 0; i--) {
        REQUIRE(end >= 8);
        sizes[i] = AV_RB24(original->data+end-8);
        REQUIRE(sizes[i]+8 <= end);
        starts[i] = end -= sizes[i]+8;
    }
    REQUIRE(end == 0);
    AVPacket *result = av_packet_alloc();
    REQUIRE(result && av_new_packet(result, original->size+512) == 0);
    REQUIRE(av_packet_copy_props(result, original) == 0);
    int offset = 0;
    for (unsigned i = 0; i < 4; i++) {
        HeaderCoder before = slice_header(i, 3), after = slice_header(i, structures[i]);
        const uint8_t *src = original->data+starts[i];
        REQUIRE(sizes[i] >= before.count);
        REQUIRE(memcmp(src, before.data, before.count) == 0);
        REQUIRE(av_crc(av_crc_get_table(AV_CRC_32_IEEE), 0, src, sizes[i]+8) == 0);
        uint8_t *dst = result->data+offset;
        memcpy(dst, after.data, after.count);
        memcpy(dst+after.count, src+before.count, sizes[i]-before.count);
        int size = after.count+sizes[i]-before.count;
        AV_WB24(dst+size, size);
        dst[size+3] = 0;
        AV_WL32(dst+size+4, av_crc(av_crc_get_table(AV_CRC_32_IEEE), 0, dst, size+4));
        REQUIRE(av_crc(av_crc_get_table(AV_CRC_32_IEEE), 0, dst, size+8) == 0);
        offset += size+8;
    }
    av_shrink_packet(result, offset);
    return result;
}

static void syntax_mutations(void)
{
    const unsigned structures[][4] = {{0,0,0,0}, {4,4,4,4}, {1,2,1,1}, {2,2,2,1}};
    Witness w = witness(3);
    encode(&w, 3, 1);
    for (unsigned i = 0; i < 4; i++) {
        AVPacket *packet = rewrite_structures(w.packet, structures[i]);
        for (int threads = 1; threads <= 4; threads += 3) {
            AVCodecContext *d = decoder(&w, AV_FIELD_BT, threads);
            AVFrame *f = av_frame_alloc();
            REQUIRE(f);
            REQUIRE(avcodec_send_packet(d, packet) == 0);
            REQUIRE(avcodec_receive_frame(d, f) == 0);
            pixels(f, 1);
            facts(f, 3, 5, structures[i][0], i < 2 ? 0 : 2);
            av_frame_unref(f);
            REQUIRE(avcodec_send_packet(d, w.packet) == 0);
            REQUIRE(avcodec_receive_frame(d, f) == 0);
            pixels(f, 1);
            facts(f, 3, 5, 3, 0);
            av_frame_free(&f);
            avcodec_free_context(&d);
        }
        av_packet_free(&packet);
    }
    release_witness(&w);
}

static int fail_attachment;
static int allocation_gate_reached;
static int execute_then_limit(AVCodecContext *d, int (*worker)(AVCodecContext *, void *),
                              void *args, int *results, int count, int size)
{
    for (int i = 0; i < count; i++) {
        int ret = worker(d, (uint8_t *)args+i*size);
        REQUIRE(ret == 0);
        if (results) results[i] = ret;
    }
    if (fail_attachment) {
        allocation_gate_reached++;
        av_max_alloc(1);
    }
    return 0;
}

static void attachment_failure(void)
{
    Witness w = witness(3);
    AVCodecContext *d = decoder(&w, AV_FIELD_TB, 1);
    AVFrame *f = av_frame_alloc(), *old;
    REQUIRE(f);
    d->execute = execute_then_limit;
    encode(&w, 1, 1);
    REQUIRE(avcodec_send_packet(d, w.packet) == 0);
    REQUIRE(avcodec_receive_frame(d, f) == 0);
    old = av_frame_clone(f);
    REQUIRE(old);
    av_frame_unref(f);
    encode(&w, 2, 2);
    fail_attachment = 1;
    int ret = avcodec_send_packet(d, w.packet);
    if (ret == 0) ret = avcodec_receive_frame(d, f);
    av_max_alloc(INT_MAX);
    fail_attachment = 0;
    REQUIRE(allocation_gate_reached == 1);
    REQUIRE(ret == AVERROR(ENOMEM));
    REQUIRE(!f->buf[0]);
    facts(old, 3, 4, 1, 0);
    pixels(old, 1);
    avcodec_flush_buffers(d);
    encode(&w, 3, 3);
    REQUIRE(avcodec_send_packet(d, w.packet) == 0);
    REQUIRE(avcodec_receive_frame(d, f) == 0);
    pixels(f, 3);
    facts(f, 3, 4, 3, 0);
    av_frame_free(&f);
    avcodec_free_context(&d);
    facts(old, 3, 4, 1, 0);
    av_frame_free(&old);
    release_witness(&w);
}

static void damaged_and_missing_slices(void)
{
    Witness w = witness(3);
    for (int missing = 0; missing <= 1; missing++) {
        AVCodecContext *d = decoder(&w, AV_FIELD_PROGRESSIVE, 4);
        AVFrame *f = av_frame_alloc();
        REQUIRE(f);
        encode(&w, 1, 1);
        REQUIRE(avcodec_send_packet(d, w.packet) == 0);
        REQUIRE(avcodec_receive_frame(d, f) == 0);
        AVFrame *old = av_frame_clone(f);
        REQUIRE(old);
        av_frame_unref(f);
        encode(&w, 3, 2);
        REQUIRE(av_packet_make_writable(w.packet) == 0);
        if (missing) {
            int last_size = AV_RB24(w.packet->data+w.packet->size-8)+8;
            av_shrink_packet(w.packet, w.packet->size-last_size);
        } else {
            /* Corrupt CRC only: syntax/pixels decode, then actual concealment. */
            w.packet->data[w.packet->size-1] ^= 1;
        }
        REQUIRE(avcodec_send_packet(d, w.packet) == 0);
        REQUIRE(avcodec_receive_frame(d, f) == 0);
        facts(f, 3, 1, 0, missing ? 2 : 3);
        facts(old, 3, 1, 1, 0);
        pixels(old, 1);
        av_frame_unref(f);
        avcodec_flush_buffers(d);
        encode(&w, 2, 3);
        REQUIRE(avcodec_send_packet(d, w.packet) == 0);
        REQUIRE(avcodec_receive_frame(d, f) == 0);
        pixels(f, 3);
        facts(f, 3, 1, 2, 0);
        av_frame_free(&old);
        av_frame_free(&f);
        avcodec_free_context(&d);
    }
    release_witness(&w);
}

static void frame_threaded_drain(void)
{
    const unsigned structures[] = {1, 2, 3, 1, 3, 2, 1, 2};
    for (int version = 1; version <= 3; version += 2) {
        Witness w = witness(version);
        AVCodecContext *d = decoder(&w, AV_FIELD_BB, -4);
        AVFrame *f = av_frame_alloc(), *old = NULL;
        int received = 0, delayed = 0;
        REQUIRE(f && (d->active_thread_type & FF_THREAD_FRAME));
        for (int index = 0; index < 8; index++) {
            encode(&w, structures[index], index);
            REQUIRE(avcodec_send_packet(d, w.packet) == 0);
            int ret;
            while ((ret = avcodec_receive_frame(d, f)) == 0) {
                REQUIRE(received <= index);
                pixels(f, received);
                facts(f, version, 3, structures[received], 0);
                if (!old) { old = av_frame_clone(f); REQUIRE(old); }
                received++;
                av_frame_unref(f);
            }
            REQUIRE(ret == AVERROR(EAGAIN));
        }
        REQUIRE(received < 8);
        REQUIRE(avcodec_send_packet(d, NULL) == 0);
        int ret;
        while ((ret = avcodec_receive_frame(d, f)) == 0) {
            REQUIRE(received < 8);
            pixels(f, received);
            facts(f, version, 3, structures[received], 0);
            received++; delayed++;
            av_frame_unref(f);
        }
        REQUIRE(ret == AVERROR_EOF && received == 8 && delayed > 0);
        avcodec_flush_buffers(d);
        avcodec_free_context(&d);
        facts(old, version, 3, 1, 0);
        pixels(old, 0);
        av_frame_free(&old);
        av_frame_free(&f);
        release_witness(&w);
    }
}

static void range_coders(void)
{
    const int coders[] = {-2, 2}; /* default and custom range transitions */
    for (int version = 1; version <= 3; version += 2) {
        for (unsigned coder = 0; coder < 2; coder++) {
            Witness w = witness_with_coder(version, coders[coder]);
            AVCodecContext *d = decoder(&w, AV_FIELD_TT, 4);
            AVFrame *f = av_frame_alloc();
            REQUIRE(f);
            for (unsigned structure = 1; structure <= 3; structure++) {
                encode(&w, structure, structure);
                REQUIRE(avcodec_send_packet(d, w.packet) == 0);
                REQUIRE(avcodec_receive_frame(d, f) == 0);
                pixels(f, structure);
                facts(f, version, 2, structure, 0);
                av_frame_unref(f);
            }
            av_frame_free(&f);
            avcodec_free_context(&d);
            release_witness(&w);
        }
    }
}

int main(int argc, char **argv)
{
#ifdef LARIX_PRESENTATION_FORCE_FAILURE
    REQUIRE(0);
#endif
    if (argc == 2 && !strcmp(argv[1], "syntax")) { syntax_mutations(); return 0; }
    ordinary_matrix();
    syntax_mutations();
    damaged_and_missing_slices();
    attachment_failure();
    frame_threaded_drain();
    range_coders();
    puts("FFV1 real picture evidence passed");
    return 0;
}
