#!/usr/bin/env bash
set -euo pipefail

profile=lgpl
configuration=Release
output_root=

while (($#)); do
  case "$1" in
    --profile)
      profile=${2:?missing value for --profile}
      shift 2
      ;;
    --configuration)
      configuration=${2:?missing value for --configuration}
      shift 2
      ;;
    --output-root)
      output_root=${2:?missing value for --output-root}
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$profile" in lgpl|gpl) ;; *) echo "invalid profile: $profile" >&2; exit 2 ;; esac
[[ $configuration == Release ]] || { echo "only Release is supported" >&2; exit 2; }

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ -z $output_root ]]; then
  output_root="$repository_root/build/macos-$profile"
elif [[ $output_root != /* ]]; then
  output_root="$repository_root/$output_root"
fi

exec "$repository_root/scripts/platforms/macos/build.sh" \
  --repo-root "$repository_root" \
  --profile "$profile" \
  --configuration "$configuration" \
  --output-root "$output_root"
