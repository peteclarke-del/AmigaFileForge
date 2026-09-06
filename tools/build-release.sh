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

# Record the names as they are published. GitHub rewrites the Debian tilde
# in an asset name to a dot, so a checksum file naming the built artefact
# cannot verify the downloaded one.
for package in "$output_dir"/amiga-file-forge_*'~'*.deb; do
    [ -e "$package" ] || continue
    mv -- "$package" "$(printf '%s' "$package" | tr '~' '.')"
done

(
    cd "$output_dir"
    sha256sum -- "AmigaFileForge-$version-source.tar.gz" amiga-file-forge_*.deb \
        | sort --key=2 > SHA256SUMS
)

echo "Release artefacts are ready in $output_dir"
