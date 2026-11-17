/* Original SDK acceptance consumer. Synthetic pictures/metadata are test inputs,
 * not Dolby certification or a tool for creating grading metadata. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/dovi_meta.h>
#include <libavutil/hdr_dynamic_metadata.h>
#include <libavutil/hwcontext.h>
#include <libavutil/mastering_display_metadata.h>
#include <libavutil/opt.h>
#define N 16
#define W 128
#define H 96
#define REQUIRE(x) do { if (!(x)) { fprintf(stderr,"encoding check failed: %s:%d: %s\n",__FILE__,__LINE__,#x); goto done; } } while(0)
/* mode: 0 SDR, 1 HDR10, 2 HLG, 3 HDR10+, 4 DV8.1, 5 DV8.4. */
typedef struct Check { int depth,mode,count,metadata,dynamic,dovi,bframes; unsigned seen; AVRational tb; AVFrame **frames; } Check;
static void fill(AVFrame *f,int depth,int index) {
  for(int p=0;p<3;p++) for(int y=0;y<(p?H/2:H);y++) for(int x=0;x<(p?W/2:W);x++) {
    int value=(p==0?48+index*8:p==1?96:160) << (depth-8);
    if(depth==8) f->data[p][y*f->linesize[p]+x]=(uint8_t)value;
    else ((uint16_t*)(f->data[p]+y*f->linesize[p]))[x]=(uint16_t)value;
  }
}
static int check_frame(AVFrame *f, Check *c) {
  int index=(int)av_rescale_q(f->pts,c->tb,(AVRational){1,25});
  if(index<0||index>=N||(c->seen&(1u<<index))||f->width!=W||f->height!=H) return 0;
  if(f->format!=(c->depth==8?AV_PIX_FMT_YUV420P:AV_PIX_FMT_YUV420P10LE)) return 0;
  if(f->color_primaries!=(c->mode?AVCOL_PRI_BT2020:AVCOL_PRI_BT709)||
     f->colorspace!=(c->mode?AVCOL_SPC_BT2020_NCL:AVCOL_SPC_BT709)||
     f->color_trc!=((c->mode==2||c->mode==5)?AVCOL_TRC_ARIB_STD_B67:c->mode?AVCOL_TRC_SMPTE2084:AVCOL_TRC_BT709)||f->color_range!=AVCOL_RANGE_MPEG) return 0;
  for(int p=0;p<3;p++) {
    int expected=(p==0?48+index*8:p==1?96:160) << (c->depth-8);
    int actual=c->depth==8?f->data[p][0]:((uint16_t*)f->data[p])[0];
    if(abs(actual-expected)>(10<<(c->depth-8))) return 0;
  }
  AVFrameSideData *md=av_frame_get_side_data(f,AV_FRAME_DATA_MASTERING_DISPLAY_METADATA);
  AVFrameSideData *cll=av_frame_get_side_data(f,AV_FRAME_DATA_CONTENT_LIGHT_LEVEL);
  if(md&&cll) {
    AVMasteringDisplayMetadata *m=(void*)md->data; AVContentLightMetadata *l=(void*)cll->data;
    if(!m->has_luminance||!m->has_primaries||av_cmp_q(m->max_luminance,(AVRational){1000,1})||l->MaxCLL!=1000||l->MaxFALL!=400) return 0;
    c->metadata++;
  }
  AVFrameSideData *plus=av_frame_get_side_data(f,AV_FRAME_DATA_DYNAMIC_HDR_PLUS);
  if(plus) {
    AVDynamicHDRPlus *m=(void*)plus->data;
    if(m->num_windows!=1||m->application_version!=1||av_cmp_q(m->params[0].average_maxrgb,(AVRational){10000+index*100,100000})) return 0;
    c->dynamic++;
  }
  AVFrameSideData *dv=av_frame_get_side_data(f,AV_FRAME_DATA_DOVI_METADATA);
  if(dv) {
    AVDOVIMetadata *m=(void*)dv->data; AVDOVIRpuDataHeader *h=av_dovi_get_header(m);
    AVDOVIDataMapping *map=av_dovi_get_mapping(m);
    if(h->bl_bit_depth!=10||!h->disable_residual_flag||map->curves[0].num_pivots!=2||map->curves[0].pivots[1]!=1023) return 0;
    c->dovi++;
  }
  if(c->mode==0&&(md||cll||plus||dv)) return 0;
  if(c->frames){c->frames[index]=av_frame_clone(f);if(!c->frames[index])return 0;}
  if(f->pict_type==AV_PICTURE_TYPE_B)c->bframes++;
  c->seen|=1u<<index;c->count++;return 1;
}
static int receive(AVCodecContext *dec,AVFrame *frame,Check *c) {
  int r;
  while((r=avcodec_receive_frame(dec,frame))>=0) { if(!check_frame(frame,c))return 0;av_frame_unref(frame); }
  return r==AVERROR(EAGAIN)||r==AVERROR_EOF;
}
static int complete(Check *c) {
  fprintf(stderr,"decoded frames=%d seen=%x depth=%d mode=%d metadata=%d dynamic=%d dovi=%d\n",c->count,c->seen,c->depth,c->mode,c->metadata,c->dynamic,c->dovi);
  return c->count==N&&c->seen==0xffff&&
    (!(c->mode==1||c->mode==3||c->mode==4)||c->metadata>0)&&
    (c->mode!=3||c->dynamic==N)&&
    (c->mode<4||c->dovi==N);
}
static int add_static(AVCodecContext *enc) {
  AVFrameSideData *md=av_frame_side_data_new(&enc->decoded_side_data,&enc->nb_decoded_side_data,AV_FRAME_DATA_MASTERING_DISPLAY_METADATA,sizeof(AVMasteringDisplayMetadata),0);
  AVFrameSideData *cll=av_frame_side_data_new(&enc->decoded_side_data,&enc->nb_decoded_side_data,AV_FRAME_DATA_CONTENT_LIGHT_LEVEL,sizeof(AVContentLightMetadata),0);
  if(!md||!cll)return 0;
  AVMasteringDisplayMetadata *m=(void*)md->data;AVContentLightMetadata *l=(void*)cll->data;
  int prim[3][2]={{34000,16000},{13250,34500},{7500,3000}};
  for(int p=0;p<3;p++)for(int xy=0;xy<2;xy++)m->display_primaries[p][xy]=(AVRational){prim[p][xy],50000};
  m->white_point[0]=(AVRational){15635,50000};m->white_point[1]=(AVRational){16450,50000};
  m->max_luminance=(AVRational){1000,1};m->min_luminance=(AVRational){1,10000};m->has_primaries=m->has_luminance=1;
  l->MaxCLL=1000;l->MaxFALL=400;return 1;
}
static AVFrameSideData *add_dovi(AVCodecContext *enc) {
  size_t size;AVDOVIMetadata *m=av_dovi_metadata_alloc(&size);if(!m)return NULL;
  AVDOVIRpuDataHeader *h=av_dovi_get_header(m);AVDOVIDataMapping *map=av_dovi_get_mapping(m);AVDOVIColorMetadata *color=av_dovi_get_color(m);
  h->rpu_type=2;h->rpu_format=18;h->vdr_rpu_profile=1;h->coef_log2_denom=23;h->vdr_rpu_normalized_idc=1;
  h->bl_bit_depth=10;h->el_bit_depth=10;h->vdr_bit_depth=12;h->disable_residual_flag=1;
  map->num_x_partitions=map->num_y_partitions=1;map->nlq_method_idc=AV_DOVI_NLQ_NONE;map->mapping_chroma_format_idc=1;
  for(int c=0;c<3;c++){map->curves[c].num_pivots=2;map->curves[c].pivots[1]=1023;map->curves[c].poly_order[0]=1;map->curves[c].poly_coef[0][1]=1<<23;}
  for(int i=0;i<9;i++){color->ycc_to_rgb_matrix[i]=(AVRational){i%4==0,1};color->rgb_to_lms_matrix[i]=(AVRational){i%4==0,1};}
  for(int i=0;i<3;i++)color->ycc_to_rgb_offset[i]=(AVRational){0,1};
  color->scene_refresh_flag=1;color->signal_bit_depth=12;color->source_max_pq=3079;
  AVFrameSideData *sd=av_frame_side_data_new(&enc->decoded_side_data,&enc->nb_decoded_side_data,AV_FRAME_DATA_DOVI_METADATA,size,0);
  if(sd)memcpy(sd->data,m,size);av_free(m);return sd;
}
static int container_roundtrip(const char *path,const char *format,AVCodecContext *enc,AVPacket **packets,int count,int depth,int mode) {
  int ok=0;AVFormatContext *out=NULL,*in=NULL;AVCodecContext *dec=NULL;AVPacket *pkt=av_packet_alloc();AVFrame *frame=av_frame_alloc();
  Check check={0};check.depth=depth;check.mode=mode;
  REQUIRE(pkt&&frame&&avformat_alloc_output_context2(&out,NULL,format,path)>=0);
  AVStream *stream=avformat_new_stream(out,NULL);REQUIRE(stream);stream->time_base=enc->time_base;
  REQUIRE(avcodec_parameters_from_context(stream->codecpar,enc)>=0);REQUIRE(avio_open(&out->pb,path,AVIO_FLAG_WRITE)>=0);REQUIRE(avformat_write_header(out,NULL)>=0);
  for(int i=0;i<count;i++){REQUIRE(av_packet_ref(pkt,packets[i])>=0);av_packet_rescale_ts(pkt,enc->time_base,stream->time_base);pkt->stream_index=0;REQUIRE(av_interleaved_write_frame(out,pkt)>=0);av_packet_unref(pkt);}
  REQUIRE(av_write_trailer(out)>=0);avio_closep(&out->pb);avformat_free_context(out);out=NULL;
  REQUIRE(avformat_open_input(&in,path,NULL,NULL)>=0&&avformat_find_stream_info(in,NULL)>=0);
  int index=av_find_best_stream(in,AVMEDIA_TYPE_VIDEO,-1,-1,NULL,0);REQUIRE(index>=0);
  dec=avcodec_alloc_context3(avcodec_find_decoder(in->streams[index]->codecpar->codec_id));REQUIRE(dec);
  REQUIRE(avcodec_parameters_to_context(dec,in->streams[index]->codecpar)>=0);check.tb=in->streams[index]->time_base;dec->pkt_timebase=check.tb;
  REQUIRE(avcodec_open2(dec,NULL,NULL)>=0);
  while(av_read_frame(in,pkt)>=0){if(pkt->stream_index==index){REQUIRE(avcodec_send_packet(dec,pkt)>=0);REQUIRE(receive(dec,frame,&check));}av_packet_unref(pkt);}
  REQUIRE(avcodec_send_packet(dec,NULL)>=0&&receive(dec,frame,&check)&&complete(&check));ok=1;
done:
  if(out){if(out->pb)avio_closep(&out->pb);avformat_free_context(out);}avformat_close_input(&in);avcodec_free_context(&dec);av_frame_free(&frame);av_packet_free(&pkt);return ok;
}
static int run_case(const char *name,int depth,const char *profile,int mode,const char *directory, AVFrame **supplied) {
  int ok=0,count=0;AVCodecContext *enc=NULL,*dec=NULL;AVFrame *input=av_frame_alloc(),*decoded=av_frame_alloc();AVPacket *packet=av_packet_alloc(),*packets[64]={0};AVDictionary *options=NULL;const AVFrameSideData *dovi=NULL;
  AVFrame *recovered[N]={0};Check check={0};check.frames=supplied?NULL:recovered;check.depth=depth;check.mode=mode;check.tb=(AVRational){1,25};
  const AVCodec *encoder=avcodec_find_encoder_by_name(name);REQUIRE(encoder&&input&&decoded&&packet);
  enc=avcodec_alloc_context3(encoder);REQUIRE(enc);enc->flags|=AV_CODEC_FLAG_GLOBAL_HEADER;enc->width=W;enc->height=H;enc->pix_fmt=depth==8?AV_PIX_FMT_YUV420P:AV_PIX_FMT_YUV420P10LE;enc->time_base=(AVRational){1,25};enc->framerate=(AVRational){25,1};enc->gop_size=8;enc->max_b_frames=!strcmp(profile,"baseline")?0:2;enc->thread_count=1;
  enc->color_primaries=mode?AVCOL_PRI_BT2020:AVCOL_PRI_BT709;enc->colorspace=mode?AVCOL_SPC_BT2020_NCL:AVCOL_SPC_BT709;enc->color_trc=(mode==2||mode==5)?AVCOL_TRC_ARIB_STD_B67:mode?AVCOL_TRC_SMPTE2084:AVCOL_TRC_BT709;enc->color_range=AVCOL_RANGE_MPEG;enc->chroma_sample_location=AVCHROMA_LOC_LEFT;
  av_dict_set(&options,"preset","medium",0);av_dict_set(&options,"profile",profile,0);av_dict_set(&options,"crf","18",0);
  if(supplied){
    for(int i=0;i<supplied[0]->nb_side_data;i++) {
      AVFrameSideData *sd=supplied[0]->side_data[i];
      if(sd->type==AV_FRAME_DATA_MASTERING_DISPLAY_METADATA||sd->type==AV_FRAME_DATA_CONTENT_LIGHT_LEVEL||sd->type==AV_FRAME_DATA_DOVI_METADATA)REQUIRE(av_frame_side_data_clone(&enc->decoded_side_data,&enc->nb_decoded_side_data,sd,0)>=0);
    }
  } else if(mode==1||mode==3||mode==4)REQUIRE(add_static(enc));
  if(mode>=4){dovi=supplied?av_frame_side_data_get(enc->decoded_side_data,enc->nb_decoded_side_data,AV_FRAME_DATA_DOVI_METADATA):add_dovi(enc);REQUIRE(dovi);av_dict_set(&options,"dolbyvision","1",0);}
  if(!strcmp(name,"libx264"))av_dict_set(&options,"x264-params","b-adapt=0",0);
  if(!strcmp(name,"libx265")) {
    char params[512];snprintf(params,sizeof(params),"pools=none:frame-threads=1:wpp=0:bframes=2:b-adapt=0:log-level=error:repeat-headers=1:vbv-maxrate=10000:vbv-bufsize=10000%s",mode==3?":dhdr10-info=hdr10plus.json":"");av_dict_set(&options,"x265-params",params,0);
  }
  REQUIRE(avcodec_open2(enc,encoder,&options)>=0&&av_dict_count(options)==0);
  dec=avcodec_alloc_context3(avcodec_find_decoder(encoder->id));REQUIRE(dec);AVCodecParameters *parameters=avcodec_parameters_alloc();REQUIRE(parameters);REQUIRE(avcodec_parameters_from_context(parameters,enc)>=0&&avcodec_parameters_to_context(dec,parameters)>=0);avcodec_parameters_free(&parameters);dec->pkt_timebase=enc->time_base;REQUIRE(avcodec_open2(dec,NULL,NULL)>=0);
  input->format=enc->pix_fmt;input->width=W;input->height=H;input->color_primaries=enc->color_primaries;input->colorspace=enc->colorspace;input->color_trc=enc->color_trc;input->color_range=enc->color_range;REQUIRE(av_frame_get_buffer(input,32)>=0);
  if(dovi){AVFrameSideData *sd=av_frame_new_side_data(input,AV_FRAME_DATA_DOVI_METADATA,dovi->size);REQUIRE(sd);memcpy(sd->data,dovi->data,dovi->size);}
  for(int i=0;i<=N;i++) {
    if(i<N){if(supplied){av_frame_free(&input);input=av_frame_clone(supplied[i]);REQUIRE(input);}else{REQUIRE(av_frame_make_writable(input)>=0);fill(input,depth,i);input->pts=i;}input->duration=1;}
    REQUIRE(avcodec_send_frame(enc,i==N?NULL:input)>=0);
    int r;
    while((r=avcodec_receive_packet(enc,packet))>=0){REQUIRE(count<64);if(!packet->duration)packet->duration=1;packets[count]=av_packet_clone(packet);REQUIRE(packets[count]);count++;REQUIRE(avcodec_send_packet(dec,packet)>=0&&receive(dec,decoded,&check));av_packet_unref(packet);}
    REQUIRE(r==AVERROR(EAGAIN)||r==AVERROR_EOF);
  }
  REQUIRE(avcodec_send_packet(dec,NULL)>=0&&receive(dec,decoded,&check)&&complete(&check));
  REQUIRE(count==N);
  int expected_profile=!strcmp(name,"libx265")?(depth==10?AV_PROFILE_HEVC_MAIN_10:AV_PROFILE_HEVC_MAIN):!strcmp(profile,"baseline")?AV_PROFILE_H264_BASELINE:!strcmp(profile,"main")?AV_PROFILE_H264_MAIN:depth==10?AV_PROFILE_H264_HIGH_10:AV_PROFILE_H264_HIGH;
  REQUIRE((dec->profile & ~AV_PROFILE_H264_CONSTRAINED)==expected_profile);
  if(strcmp(profile,"baseline"))REQUIRE(check.bframes>0);
  for(int ext=0;ext<2;ext++) {
    char path[1024];snprintf(path,sizeof(path),"%s/%s-%d-%s-%d.%s",directory,name,depth,profile,mode,ext?"mkv":"mp4");
    REQUIRE(container_roundtrip(path,ext?"matroska":"mp4",enc,packets,count,depth,mode));
  }
  fprintf(stderr,"PASS %s %s %d-bit mode=%d frames=%d metadata=%d hdr10plus=%d dovi=%d\n",name,profile,depth,mode,check.count,check.metadata,check.dynamic,check.dovi);if(!supplied&&mode)REQUIRE(run_case(name,depth,profile,mode,directory,recovered));ok=1;
done:
  for(int i=0;i<N;i++)av_frame_free(&recovered[i]);
  for(int i=0;i<count;i++)av_packet_free(&packets[i]);av_dict_free(&options);avcodec_free_context(&enc);avcodec_free_context(&dec);av_frame_free(&input);av_frame_free(&decoded);av_packet_free(&packet);return ok;
}
int main(int argc,char **argv) {
  if(argc!=3)return 2;av_log_set_level(AV_LOG_ERROR);
  if(av_hwdevice_iterate_types(AV_HWDEVICE_TYPE_NONE)!=AV_HWDEVICE_TYPE_NONE)return 3;
  if(!strcmp(argv[1],"lgpl")){if(avcodec_find_encoder_by_name("libx264")||avcodec_find_encoder_by_name("libx265"))return 4;puts("LGPL encoder isolation passed");return 0;}
  if(strcmp(argv[1],"gpl"))return 2;
  const char *profiles[]={"baseline","main","high","high10"};
  for(int i=0;i<4;i++)if(!run_case("libx264",i==3?10:8,profiles[i],0,argv[2],NULL))return 10+i;
  if(!run_case("libx265",8,"main",0,argv[2],NULL)||!run_case("libx265",10,"main10",0,argv[2],NULL))return 20;
  for(int mode=1;mode<=5;mode++)if(!run_case("libx265",10,"main10",mode,argv[2],NULL))return 30+mode;
  puts("CPU H264/HEVC 8/10-bit, HDR10/HLG/HDR10+ and limited DV8 acceptance passed");return 0;
}
