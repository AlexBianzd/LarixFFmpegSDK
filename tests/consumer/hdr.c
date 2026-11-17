/* CPU processing example and numeric acceptance test. No GPU or display. */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <libavfilter/avfilter.h>
#include <libavfilter/buffersink.h>
#include <libavfilter/buffersrc.h>
#include <libavutil/dovi_meta.h>
#include <libavutil/hdr_dynamic_metadata.h>
#include <libavutil/hwcontext.h>
#include <libavutil/mastering_display_metadata.h>
#include <libavutil/pixdesc.h>
#define CHECK(x) do { if(!(x)){fprintf(stderr,"HDR check failed %s:%d: %s\n",__FILE__,__LINE__,#x);goto done;} } while(0)
/* Compatibility-base processing only: never interpret P5 IPT as ordinary PQ. */
static int cpu_transform_allowed(int dovi_profile) {
  return dovi_profile==0||dovi_profile==8;
}
static void remove_obsolete_metadata(AVFrame *frame) {
  const enum AVFrameSideDataType types[]={AV_FRAME_DATA_MASTERING_DISPLAY_METADATA,AV_FRAME_DATA_CONTENT_LIGHT_LEVEL,AV_FRAME_DATA_DYNAMIC_HDR_PLUS,AV_FRAME_DATA_DOVI_RPU_BUFFER,AV_FRAME_DATA_DOVI_METADATA};
  for(unsigned i=0;i<sizeof(types)/sizeof(types[0]);i++)av_frame_remove_side_data(frame,types[i]);
}
static int transform(int inverse,int hlg) {
  int ok=0;AVFilterGraph *graph=avfilter_graph_alloc();AVFilterContext *src=NULL,*sink=NULL;AVFilterInOut *inputs=avfilter_inout_alloc(),*outputs=avfilter_inout_alloc();AVFrame *input=av_frame_alloc(),*output=av_frame_alloc();
  CHECK(graph&&inputs&&outputs&&input&&output);graph->nb_threads=1;
  enum AVPixelFormat format=inverse?AV_PIX_FMT_YUV420P:AV_PIX_FMT_YUV420P10LE;
  char args[256];snprintf(args,sizeof(args),"video_size=128x96:pix_fmt=%d:time_base=1/25:pixel_aspect=1/1",format);
  CHECK(avfilter_graph_create_filter(&src,avfilter_get_by_name("buffer"),"input",args,NULL,graph)>=0);
  CHECK(avfilter_graph_create_filter(&sink,avfilter_get_by_name("buffersink"),"output",NULL,NULL,graph)>=0);
  outputs->name=av_strdup("in");outputs->filter_ctx=src;outputs->pad_idx=0;
  inputs->name=av_strdup("out");inputs->filter_ctx=sink;inputs->pad_idx=0;
  const char *chain=inverse?(hlg?
    "zscale=t=linear:npl=100,format=gbrpf32le,exposure=exposure=1.02147973,zscale=p=bt2020:t=arib-std-b67:m=bt2020nc:r=limited:npl=100,format=yuv420p10le":
    "zscale=t=linear:npl=100,format=gbrpf32le,exposure=exposure=1.02147973,zscale=p=bt2020:t=smpte2084:m=bt2020nc:r=limited:npl=100,format=yuv420p10le"):
    "zscale=t=linear:npl=100,format=gbrpf32le,tonemap=mobius:desat=0,zscale=p=bt709:t=bt709:m=bt709:r=limited,format=yuv420p";
  CHECK(avfilter_graph_parse_ptr(graph,chain,&inputs,&outputs,NULL)>=0&&avfilter_graph_config(graph,NULL)>=0);
  input->format=format;input->width=128;input->height=96;input->pts=0;input->duration=1;
  input->color_primaries=inverse?AVCOL_PRI_BT709:AVCOL_PRI_BT2020;
  input->colorspace=inverse?AVCOL_SPC_BT709:AVCOL_SPC_BT2020_NCL;
  input->color_trc=inverse?AVCOL_TRC_BT709:hlg?AVCOL_TRC_ARIB_STD_B67:AVCOL_TRC_SMPTE2084;
  input->color_range=AVCOL_RANGE_MPEG;input->chroma_location=AVCHROMA_LOC_LEFT;
  CHECK(av_frame_get_buffer(input,32)>=0);
  for(int p=0;p<3;p++)for(int y=0;y<(p?48:96);y++)for(int x=0;x<(p?64:128);x++) {
    int value=p?128:16+(219*x/127);
    if(inverse)input->data[p][y*input->linesize[p]+x]=(uint8_t)value;
    else ((uint16_t*)(input->data[p]+y*input->linesize[p]))[x]=(uint16_t)(p?512:64+((hlg?876:659)*x/127));
  }
  if(!inverse){AVMasteringDisplayMetadata *m=av_mastering_display_metadata_create_side_data(input);CHECK(m);m->has_luminance=1;m->max_luminance=(AVRational){1000,1};m->min_luminance=(AVRational){1,10000};CHECK(av_content_light_metadata_create_side_data(input));CHECK(av_dynamic_hdr_plus_create_side_data(input));}
  CHECK(av_buffersrc_add_frame_flags(src,input,AV_BUFFERSRC_FLAG_KEEP_REF)>=0&&av_buffersrc_add_frame_flags(src,NULL,0)>=0);
  CHECK(av_buffersink_get_frame(sink,output)>=0);
  /* Pixel transforms invalidate grading metadata: explicit application policy. */
  remove_obsolete_metadata(output);
  CHECK(output->width==128&&output->height==96&&output->pts==0);
  CHECK(output->format==(inverse?AV_PIX_FMT_YUV420P10LE:AV_PIX_FMT_YUV420P));
  CHECK(output->color_primaries==(inverse?AVCOL_PRI_BT2020:AVCOL_PRI_BT709));
  CHECK(output->colorspace==(inverse?AVCOL_SPC_BT2020_NCL:AVCOL_SPC_BT709));
  CHECK(output->color_trc==(inverse?(hlg?AVCOL_TRC_ARIB_STD_B67:AVCOL_TRC_SMPTE2084):AVCOL_TRC_BT709)&&output->color_range==AVCOL_RANGE_MPEG);
  CHECK(!av_frame_get_side_data(output,AV_FRAME_DATA_MASTERING_DISPLAY_METADATA)&&!av_frame_get_side_data(output,AV_FRAME_DATA_DYNAMIC_HDR_PLUS)&&!av_frame_get_side_data(output,AV_FRAME_DATA_CONTENT_LIGHT_LEVEL));
  int low=inverse?((uint16_t*)output->data[0])[0]:output->data[0][0];
  int high=inverse?((uint16_t*)output->data[0])[127]:output->data[0][127];
  fprintf(stderr,"numeric %s low=%d high=%d\n",inverse?"inverse":hlg?"hlg":"pq",low,high);
  CHECK(low<(inverse?80:24)&&high>low+64);
  if(inverse){/* PQ(203 nit) ~= 0.5807; limited 10-bit code ~= 573. */if(hlg){CHECK(high>400&&high<940);}else{CHECK(abs(high-573)<30);}}
  else {CHECK(high<=235&&high>=200);CHECK(abs((int)output->data[0][64]-(16+219*64/127))>4);}
  for(int x=1;x<128;x++){int prev=inverse?((uint16_t*)output->data[0])[x-1]:output->data[0][x-1];int now=inverse?((uint16_t*)output->data[0])[x]:output->data[0][x];CHECK(now+2>=prev);}
  av_frame_unref(output);CHECK(av_buffersink_get_frame(sink,output)==AVERROR_EOF);
  fprintf(stderr,"PASS CPU %s low=%d high=%d\n",inverse?(hlg?"SDR->HLG":"SDR100->PQ203"):hlg?"HLG->SDR":"PQ->SDR",low,high);ok=1;
done:avfilter_inout_free(&inputs);avfilter_inout_free(&outputs);avfilter_graph_free(&graph);av_frame_free(&input);av_frame_free(&output);return ok;
}
int main(void) {
  av_log_set_level(AV_LOG_ERROR);
  if(av_hwdevice_iterate_types(AV_HWDEVICE_TYPE_NONE)!=AV_HWDEVICE_TYPE_NONE)return 2;
  if(cpu_transform_allowed(5)||cpu_transform_allowed(7)||!cpu_transform_allowed(8))return 3;
  if(!transform(0,0)||!transform(0,1)||!transform(1,0)||!transform(1,1))return 4;
  puts("CPU HDR processing passed; DV5/7 pixel transformations intentionally unsupported");return 0;
}
