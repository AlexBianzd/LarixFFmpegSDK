#!/usr/bin/env bash
set -euo pipefail

sdk_root=
expected_csv=
report_path=
while (($#)); do
  case "$1" in
    --sdk-root) sdk_root=${2:?}; shift 2 ;;
    --expected-macho-csv) expected_csv=${2:?}; shift 2 ;;
    --report-path) report_path=${2:?}; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -n $sdk_root && -n $expected_csv && -n $report_path ]] || {
  echo "inspection arguments are required" >&2
  exit 2
}
for tool in file otool vtool python3 clang xcodebuild xcrun; do
  command -v "$tool" >/dev/null || { echo "required inspection tool is missing: $tool" >&2; exit 1; }
done

temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
dependencies_tsv="$temporary/dependencies.tsv"
: > "$dependencies_tsv"
IFS=';' read -r -a expected <<< "$expected_csv"

while IFS= read -r -d '' candidate; do
  candidate_identity=$(file -b "$candidate")
  if [[ $candidate_identity == *"Mach-O"* ]]; then
    relative=${candidate#"$sdk_root"/}
    case ";$expected_csv;" in
      *";$relative;"*) ;;
      *) echo "unexpected Mach-O: $relative" >&2; exit 1 ;;
    esac
  fi
done < <(find "$sdk_root" -type f -print0)

for relative in "${expected[@]}"; do
  binary="$sdk_root/$relative"
  [[ -f $binary && ! -L $binary ]] || { echo "missing Mach-O: $relative" >&2; exit 1; }
  identity=$(file -b "$binary")
  [[ $identity == *"Mach-O 64-bit"* && $identity == *"arm64"* ]] || {
    echo "non-arm64 Mach-O: $relative: $identity" >&2
    exit 1
  }
  build_version=$(vtool -show-build "$binary")
  [[ $build_version == *"platform MACOS"* && $build_version == *"minos 12.0"* ]] || {
    echo "invalid macOS deployment target: $relative" >&2
    exit 1
  }
  if [[ $relative == lib/*.dylib ]]; then
    dylib_id=$(otool -D "$binary" | tail -n +2 | sed '/^[[:space:]]*$/d')
    [[ $dylib_id == "@rpath/$(basename "$binary")" ]] || {
      echo "invalid dylib install name: $relative: $dylib_id" >&2
      exit 1
    }
  fi
  while IFS= read -r dependency; do
    dependency=${dependency%% (*}
    dependency=${dependency#${dependency%%[![:space:]]*}}
    [[ -n $dependency ]] || continue
    case "$dependency" in
      @rpath/lib*.dylib|/usr/lib/*|/System/Library/Frameworks/*) ;;
      *) echo "unexpected Mach-O dependency: $relative: $dependency" >&2; exit 1 ;;
    esac
    printf '%s\t%s\n' "$relative" "$dependency" >> "$dependencies_tsv"
  done < <(otool -L "$binary" | tail -n +2)
done

compiler_output=$(clang --version)
compiler=${compiler_output%%$'\n'*}
xcode_output=$(xcodebuild -version)
xcode=${xcode_output%%$'\n'*}
macos_sdk=$(xcrun --sdk macosx --show-sdk-version)
python3 - "$dependencies_tsv" "$report_path" "$compiler" "$xcode" "$macos_sdk" <<'PY'
import json
import pathlib
import sys

source, destination = map(pathlib.Path, sys.argv[1:3])
dependencies = {}
for line in source.read_text(encoding="utf-8").splitlines():
    path, dependency = line.split("\t", 1)
    dependencies.setdefault(path, []).append(dependency)
for path in dependencies:
    dependencies[path] = sorted(set(dependencies[path]))
value = {
    "runtimeDependencies": dict(sorted(dependencies.items())),
    "toolchain": {
        "compiler": sys.argv[3],
        "xcode": sys.argv[4],
        "macosSdk": sys.argv[5],
    },
}
destination.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
