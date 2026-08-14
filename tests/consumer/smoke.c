#include <stdint.h>
#include <stdio.h>
#include <libavutil/avutil.h>
#include <libavutil/channel_layout.h>
#include <libavutil/imgutils.h>
#include <libavutil/mem.h>
#include <libavutil/samplefmt.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswresample/swresample.h>
#include <libswscale/swscale.h>

static int decode_one_frame(const char* path) {
  AVFormatContext* format = NULL;
  AVCodecContext* codec = NULL;
  AVPacket* packet = NULL;
  AVFrame* frame = NULL;
  int decoded = 0;
  if (avformat_open_input(&format, path, NULL, NULL) < 0 ||
      avformat_find_stream_info(format, NULL) < 0) goto done;
  int stream_index = av_find_best_stream(format, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
  if (stream_index < 0) goto done;
  const AVCodecParameters* parameters = format->streams[stream_index]->codecpar;
  const AVCodec* decoder = avcodec_find_decoder(parameters->codec_id);
  if (!decoder) goto done;
  codec = avcodec_alloc_context3(decoder);
  packet = av_packet_alloc();
  frame = av_frame_alloc();
  if (!codec || !packet || !frame || avcodec_parameters_to_context(codec, parameters) < 0 ||
      avcodec_open2(codec, decoder, NULL) < 0) goto done;
  while (av_read_frame(format, packet) >= 0) {
    if (packet->stream_index == stream_index && avcodec_send_packet(codec, packet) >= 0 &&
        avcodec_receive_frame(codec, frame) >= 0) {
      decoded = 1;
      av_packet_unref(packet);
      break;
    }
    av_packet_unref(packet);
  }
done:
  av_frame_free(&frame);
  av_packet_free(&packet);
  avcodec_free_context(&codec);
  avformat_close_input(&format);
  return decoded;
}

static int convert_pixels(void) {
  const uint8_t y[4] = {16, 81, 145, 235};
  const uint8_t u[1] = {90};
  const uint8_t v[1] = {240};
  const uint8_t* source[4] = {y, u, v, NULL};
  const int strides[4] = {2, 1, 1, 0};
  uint8_t first_channels[2] = {0};
  uint8_t third_channels[2] = {0};
  const enum AVPixelFormat formats[2] = {AV_PIX_FMT_RGB24, AV_PIX_FMT_BGR24};
  for (int index = 0; index < 2; ++index) {
    struct SwsContext* context = sws_getContext(
        2, 2, AV_PIX_FMT_YUV420P, 2, 2, formats[index], SWS_POINT, NULL, NULL, NULL);
    if (!context) return 0;
    uint8_t* output[4] = {NULL, NULL, NULL, NULL};
    int output_strides[4] = {0, 0, 0, 0};
    if (av_image_alloc(output, output_strides, 2, 2, formats[index], 32) < 0) {
      sws_freeContext(context);
      return 0;
    }
    int rows = sws_scale(context, source, strides, 0, 2, output, output_strides);
    if (rows == 2) {
      first_channels[index] = output[0][0];
      third_channels[index] = output[0][2];
    }
    av_freep(&output[0]);
    sws_freeContext(context);
    if (rows != 2) return 0;
  }
  return first_channels[0] == third_channels[1] &&
         third_channels[0] == first_channels[1];
}

static int convert_audio(void) {
  SwrContext* context = NULL;
  AVChannelLayout mono = AV_CHANNEL_LAYOUT_MONO;
  uint8_t* input[1] = {NULL};
  uint8_t* output[1] = {NULL};
  int input_linesize = 0;
  int output_linesize = 0;
  int converted = -1;
  if (av_samples_alloc(
          input, &input_linesize, 1, 4, AV_SAMPLE_FMT_S16, 0) < 0 ||
      av_samples_alloc(
          output, &output_linesize, 1, 4, AV_SAMPLE_FMT_FLT, 0) < 0) {
    goto done;
  }
  int16_t* input_samples = (int16_t*)input[0];
  input_samples[0] = -32768;
  input_samples[1] = -1;
  input_samples[2] = 1;
  input_samples[3] = 32767;
  if (swr_alloc_set_opts2(&context, &mono, AV_SAMPLE_FMT_FLT, 48000,
                          &mono, AV_SAMPLE_FMT_S16, 48000, 0, NULL) < 0 ||
      !context || swr_init(context) < 0) goto done;
  converted = swr_convert(
      context, output, 4, (const uint8_t* const*)input, 4);
done:
  swr_free(&context);
  float* output_samples = (float*)output[0];
  int valid = converted == 4 && output_samples &&
              output_samples[0] < -0.99f && output_samples[3] > 0.99f;
  av_freep(&input[0]);
  av_freep(&output[0]);
  return valid;
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const char* media = argv[1];
  const unsigned versions[5] = {
      avutil_version(), avcodec_version(), avformat_version(),
      swresample_version(), swscale_version()};
  for (int index = 0; index < 5; ++index) if (versions[index] == 0) return 10 + index;
  if (!decode_one_frame(media)) return 20;
  if (!convert_pixels()) return 21;
  if (!convert_audio()) return 22;
  puts("Larix FFmpeg SDK smoke passed");
  return 0;
}
