#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output_dir=${1:-"$project_root/dist"}
version=$(sed -n '1p' "$project_root/VERSION")

if [ -z "$version" ]; then
    echo "VERSION must contain the release version." >&2
    exit 2
fi

mkdir -p "$output_dir"
git -C "$project_root" archive \
    --format=tar.gz \
    --prefix="AmigaFileForge-$version/" \
    -o "$output_dir/AmigaFileForge-$version-source.tar.gz" \
    HEAD

echo "$output_dir/AmigaFileForge-$version-source.tar.gz"
