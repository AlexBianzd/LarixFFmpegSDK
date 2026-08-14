#!/usr/bin/env bash
set -euo pipefail

repo_root=
profile=
configuration=
output_root=
while (($#)); do
  case "$1" in
    --repo-root) repo_root=${2:?}; shift 2 ;;
    --profile) profile=${2:?}; shift 2 ;;
    --configuration) configuration=${2:?}; shift 2 ;;
    --output-root) output_root=${2:?}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n $repo_root && -n $output_root ]] || { echo "repo and output roots are required" >&2; exit 2; }
case "$profile" in lgpl|gpl) ;; *) echo "invalid profile: $profile" >&2; exit 2 ;; esac
[[ $configuration == Release ]] || { echo "only Release is supported" >&2; exit 2; }
[[ $(uname -s) == Darwin ]] || { echo "macOS is required" >&2; exit 2; }
[[ $(uname -m) == arm64 ]] || { echo "Apple Silicon arm64 is required" >&2; exit 2; }

for tool in python3 clang make cmake file otool vtool xcodebuild xcrun; do
  command -v "$tool" >/dev/null || { echo "required tool is missing: $tool" >&2; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3, 12), sys.version'

export MACOSX_DEPLOYMENT_TARGET=12.0
repo_root=$(cd "$repo_root" && pwd -P)
export PYTHONPATH="$repo_root"
mkdir -p "$output_root"
output_root=$(cd "$output_root" && pwd -P)
source_root="$output_root/source-proof/source/ffmpeg-9.0.1"
build_root="$output_root/build"
install_root="$output_root/install"
stage_root="$output_root/stage"
package_root="$output_root/package"
for path in "$build_root" "$install_root" "$stage_root" "$package_root"; do
  [[ ! -e $path ]] || { echo "output path already exists: $path" >&2; exit 1; }
done

cd "$repo_root"
python3 -m scripts.common.source --repo-root "$repo_root" --output "$output_root/source-proof"
mkdir -p "$build_root" "$install_root" "$stage_root" "$package_root"

configure_args=()
while IFS= read -r argument; do
  configure_args+=("$argument")
done < <(python3 -c 'import pathlib,sys; from scripts.common.model import compose_configure_args; print("\n".join(compose_configure_args(pathlib.Path(sys.argv[1]), sys.argv[2], "macos-arm64")))' "$repo_root" "$profile")
configure_args+=(
  --arch=arm64
  --target-os=darwin
  --cc=clang
  --install-name-dir=@rpath
  --prefix=../install
  "--extra-cflags=-mmacosx-version-min=12.0 -fdebug-compilation-dir=larix-build -ffile-prefix-map=./src=larix-source -fdebug-prefix-map=./src=larix-source -ffile-prefix-map=.=larix-build -fdebug-prefix-map=.=larix-build"
  "--extra-ldflags=-mmacosx-version-min=12.0 -Wl,-headerpad_max_install_names"
)

cd "$build_root"
ln -s "$source_root" "$build_root/src"
absolute_prefix_maps="-ffile-prefix-map=$source_root=larix-source -fdebug-prefix-map=$source_root=larix-source -ffile-prefix-map=$build_root=larix-build -fdebug-prefix-map=$build_root=larix-build"
export CFLAGS="$absolute_prefix_maps"
export CXXFLAGS="$absolute_prefix_maps"
export OBJCFLAGS="$absolute_prefix_maps"
export ASFLAGS="$absolute_prefix_maps"
"$build_root/src/configure" "${configure_args[@]}"
make -j"$(sysctl -n hw.logicalcpu)"
make install
unset CFLAGS CXXFLAGS OBJCFLAGS ASFLAGS

mkdir -p \
  "$stage_root/bin" "$stage_root/include" "$stage_root/lib" \
  "$stage_root/lib/cmake/LarixFFmpegSDK" \
  "$stage_root/share/larix-ffmpeg-sdk"
cp "$install_root/bin/ffprobe" "$stage_root/bin/ffprobe"
for component in avutil avcodec avformat swresample swscale; do
  version=$(python3 -c 'import json,pathlib,sys; value=json.loads(pathlib.Path(sys.argv[1]).read_text()); print(value["libraryVersions"][sys.argv[2]])' "$repo_root/config/ffmpeg.lock.json" "$component")
  cp -L "$install_root/lib/lib${component}.${version}.dylib" "$stage_root/lib/lib${component}.${version}.dylib"
  cp -R "$install_root/include/lib${component}" "$stage_root/include/"
done
cp "$repo_root/cmake/LarixFFmpegSDKConfig.cmake.in" \
  "$stage_root/lib/cmake/LarixFFmpegSDK/LarixFFmpegSDKConfig.cmake"

python3 -m scripts.common.stage_sdk \
  --source-root "$source_root" --repo-root "$repo_root" \
  --stage-root "$stage_root" --profile "$profile" --target macos-arm64

runtime_csv=$(python3 -c 'from scripts.common.release_manifest import runtime_files_for_target; print(";".join(runtime_files_for_target("macos-arm64")))')
inspection="$output_root/inspection.json"
"$repo_root/scripts/platforms/macos/inspect.sh" \
  --sdk-root "$stage_root" --expected-macho-csv "$runtime_csv" \
  --report-path "$inspection"

python3 - "$output_root/build-info.json" "$inspection" "$source_root" "$build_root" "$install_root" "$stage_root" "$output_root" <<'PY'
import json
import pathlib
import sys

destination, inspection, *paths = map(pathlib.Path, sys.argv[1:])
observed = json.loads(inspection.read_text(encoding="utf-8"))
value = {
    "forbiddenPaths": [str(path.resolve()) for path in paths],
    "runtimeDependencies": observed["runtimeDependencies"],
    "toolchain": observed["toolchain"],
}
destination.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

python3 -m scripts.common.release_manifest \
  --repo-root "$repo_root" --sdk-root "$stage_root" --profile "$profile" \
  --target macos-arm64 --build-info "$output_root/build-info.json"
asset=$(python3 -c 'import pathlib,sys; from scripts.common.model import load_lock,load_target,target_asset_name; print(target_asset_name(load_lock(pathlib.Path(sys.argv[1])),sys.argv[3],load_target(pathlib.Path(sys.argv[2]))))' "$repo_root/config/ffmpeg.lock.json" "$repo_root/config/targets/macos-arm64.json" "$profile")
archive="$package_root/$asset"
python3 -m scripts.common.package --sdk-root "$stage_root" --archive "$archive"
python3 -m scripts.common.verify_sdk --repo-root "$repo_root" --archive "$archive"
echo "Created verified SDK: $archive"
