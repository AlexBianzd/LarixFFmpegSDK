"""Native static dependencies for the CPU-only SDK; all builds out of tree."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path
import shutil, subprocess
from scripts.common.dependencies import prepare_dependencies, selected_dependencies
from scripts.common.source import _tree_manifest

def build_dependencies(repo: Path, output: Path, profile: str, target: str) -> Path:
    windows=target=='windows-x64-msvc'
    if target not in {'windows-x64-msvc','macos-arm64'}:raise ValueError('unsupported dependency target')
    if windows != (os.name=='nt'):raise ValueError('dependency build must run on its native target')
    if not windows and os.uname().machine!='arm64':raise ValueError('arm64 host required')
    sources=prepare_dependencies(repo,output,profile)
    before={k:_tree_manifest(v) for k,v in sources.items()}
    prefix=output/'prefix';prefix.mkdir(parents=True,exist_ok=True)
    for sub in ['lib','include','lib/pkgconfig']:(prefix/sub).mkdir(exist_ok=True)
    commands=[];environment=dict(os.environ)
    environment.update(PKG_CONFIG_PATH='',PKG_CONFIG_LIBDIR=str(prefix/'lib/pkgconfig'),GIT_CEILING_DIRECTORIES=str(output),SOURCE_DATE_EPOCH='1786492800')
    if not windows:environment['MACOSX_DEPLOYMENT_TARGET']='12.0'
    jobs=str(min(os.cpu_count() or 2,16))
    cmake=environment.get('LARIX_CMAKE') or shutil.which('cmake')
    if not cmake:raise RuntimeError('CMake is required')
    def run(args,cwd=output,env=None):
        cwd.mkdir(parents=True,exist_ok=True)
        commands.append({'argv':[str(a) for a in args],'cwd':str(cwd)})
        subprocess.run([str(a) for a in args],cwd=cwd,env=env or environment,check=True)
    def configure(source,build,extra):
        args=[cmake,'-S',source,'-B',build,'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_POSITION_INDEPENDENT_CODE=ON']
        if windows:
            flags=f'/Brepro /pathmap:{source}=dependency-source /pathmap:{build}=dependency-build'
            args+=['-G','NMake Makefiles','-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL', f'-DCMAKE_C_FLAGS={flags}',f'-DCMAKE_CXX_FLAGS={flags}']
        else:
            maps=f'-ffile-prefix-map={source}=dependency-source -fdebug-prefix-map={build}=dependency-build'
            args+=['-DCMAKE_OSX_ARCHITECTURES=arm64','-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0',f'-DCMAKE_C_FLAGS={maps}',f'-DCMAKE_CXX_FLAGS={maps}']
        run(args+extra)
        run([cmake,'--build',build,'--config','Release','--parallel',jobs])
    zbuild=output/'build/zimg'
    configure(repo/'cmake/zimg',zbuild,[f'-DZIMG_SOURCE={sources["zimg"]}',f'-DCMAKE_INSTALL_PREFIX={prefix}'])
    run([cmake,'--install',zbuild,'--config','Release'])
    def pc(name,version,lib,extra=''):
        # These physical paths are build inputs only; they never enter SDK provenance.
        (prefix/f'lib/pkgconfig/{name}.pc').write_text(f'prefix={prefix.as_posix()}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\nName: {name}\nDescription: hash-locked {name}\nVersion: {version}\nLibs: -L${{libdir}} -l{lib}\nLibs.private: {extra}\nCflags: -I${{includedir}}\n',encoding='utf-8')
    pc('zimg','3.0.6','zimg','' if windows else '-lc++')
    if profile=='gpl':
        xb=output/'build/x264';xb.mkdir(parents=True,exist_ok=True)
        xenv=dict(environment)
        if windows:
            xenv.update(CC='cl',LD='link',AR='lib.exe')
            bash=environment.get('LARIX_MSYS2_BASH') or shutil.which('bash');make=environment.get('LARIX_MSYS2_MAKE') or shutil.which('make')
            if not bash or not make:raise RuntimeError('MSYS2 bash/make required')
            xenv['PATH']=environment.get('PATH','')+os.pathsep+str(Path(bash).parent)+os.pathsep+str(Path(make).parent)
            def msys(path):
                v=Path(path).resolve().as_posix();return '/'+v[0].lower()+v[2:]
            configure_cmd=[bash,msys(sources['x264']/'configure'),'--host=x86_64-pc-mingw32']
            xflags=f'-Brepro -pathmap:{sources["x264"]}=x264-source -pathmap:{xb}=x264-build'
        else:
            make=shutil.which('make');configure_cmd=['bash',sources['x264']/'configure','--host=aarch64-apple-darwin']
            xflags=f'-mmacosx-version-min=12.0 -ffile-prefix-map={sources["x264"]}=x264-source -fdebug-prefix-map={xb}=x264-build'
        run(configure_cmd+['--enable-static','--enable-pic','--disable-cli','--disable-opencl','--disable-avs','--disable-lavf','--disable-swscale','--disable-ffms','--disable-gpac','--disable-lsmash','--bit-depth=all','--chroma-format=420',f'--extra-cflags={xflags}'],xb,xenv)
        run([make,'-j'+jobs],xb,xenv)
        shutil.copyfile(xb/('libx264.lib' if windows else 'libx264.a'),prefix/'lib'/('libx264.lib' if windows else 'libx264.a'))
        shutil.copyfile(sources['x264']/'x264.h',prefix/'include/x264.h');shutil.copyfile(xb/'x264_config.h',prefix/'include/x264_config.h')
        libs={}
        for depth in [10,8]:
            build=output/f'build/x265-{depth}'
            opts=['-DENABLE_SHARED=OFF','-DENABLE_CLI=OFF','-DENABLE_LIBNUMA=OFF','-DNATIVE_BUILD=OFF', '-DENABLE_PIC=ON', '-DENABLE_ASSEMBLY=OFF', '-DENABLE_NEON=OFF', '-DENABLE_SVE=OFF', '-DENABLE_SVE2=OFF', '-DENABLE_SVT_HEVC=OFF', f'-DHIGH_BIT_DEPTH={"ON" if depth==10 else "OFF"}',f'-DEXPORT_C_API={"OFF" if depth==10 else "ON"}',f'-DENABLE_HDR10_PLUS={"ON" if depth==10 else "OFF"}']
            if depth==8:opts += ['-DLINKED_10BIT=ON',f'-DEXTRA_LIB={libs[10]}']
            configure(sources['x265']/'source',build,opts)
            libs[depth]=build/('x265-static.lib' if windows else 'libx265.a')
        combined=prefix/'lib'/('x265.lib' if windows else 'libx265.a')
        if windows:run(['lib.exe','/nologo',f'/OUT:{combined}',libs[8],libs[10]])
        else:run(['/usr/bin/libtool','-static','-o',combined,libs[8],libs[10]])
        shutil.copyfile(sources['x265']/'source/x265.h',prefix/'include/x265.h')
        shutil.copyfile(output/'build/x265-8/x265_config.h',prefix/'include/x265_config.h')
        pc('x264','0.164','libx264' if windows else 'x264','' if windows else '-lpthread -lm')
        pc('x265','4.1','x265','' if windows else '-lc++ -lpthread -lm')
    for name,root in sources.items():
        if _tree_manifest(root)!=before[name]:raise ValueError(f'dependency build changed source tree: {name}')
    def sanitize(value):
        if isinstance(value,str):
            # Longest aliases first to avoid partial replacements.
            for path,alias in sorted([(str(repo),'${REPO}'),(str(output),'${DEPENDENCIES}')],key=lambda a:-len(a[0])):value=value.replace(path,alias)
            return value.replace('\\','/')
        if isinstance(value,list):return [sanitize(v) for v in value]
        if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items()}
        return value
    files={p.relative_to(prefix).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(prefix.rglob('*')) if p.is_file() and p.suffix in {'.a','.lib','.h'}}
    record={'schemaVersion':1,'target':target,'profile':profile,'dependencies':selected_dependencies(repo,profile),'commands':sanitize(commands),'files':files,'zimgImplementation':'upstream scalar C++','x265Assembly':False,'sourceDateEpoch':1786492800}
    (output/'build.json').write_text(json.dumps(record,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return prefix

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo-root',type=Path,default=Path.cwd());p.add_argument('--output',type=Path,required=True);p.add_argument('--profile',choices=['lgpl','gpl'],required=True);p.add_argument('--target',choices=['windows-x64-msvc','macos-arm64'],required=True)
    a=p.parse_args();print(build_dependencies(a.repo_root.resolve(),a.output.resolve(),a.profile,a.target))
if __name__=='__main__':main()
