#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output_dir=${1:-"$project_root/dist"}
version=$(sed -n '1p' "$project_root/VERSION")

if [ -n "$(git -C "$project_root" status --porcelain)" ]; then
    echo "The release tree is not clean. Commit or stash changes before packaging." >&2
    exit 2
fi

"$project_root/tools/build-source-archive.sh" "$output_dir"

"$project_root/tools/build-linux-package.sh" "$output_dir"

sha256sum "$output_dir/AmigaFileForge-$version-source.tar.gz" \
    "$output_dir"/amiga-file-forge_*.deb \
    > "$output_dir/SHA256SUMS"

echo "Release artefacts are ready in $output_dir"
