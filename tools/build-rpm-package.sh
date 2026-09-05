#!/bin/sh
set -eu

# Build an RPM from the same installed tree as the Debian package.
#
# The layout is not reimplemented here. The Debian package is built first and
# its payload is unpacked as the RPM payload, so both distributions install
# byte-identical application trees and a fault reproduced on one is reproduced
# on the other. Only the metadata, dependency names and scriptlets differ.

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output_dir=${1:-"$project_root/dist"}
version=$(sed -n '1p' "$project_root/VERSION")
rpm_version=$(printf '%s' "$version" | sed 's/-rc\./~rc./')
release=${AMIGA_RPM_RELEASE:-1}

if ! printf '%s\n' "$release" | grep -Eq '^[0-9][A-Za-z0-9._]*$'; then
    echo "AMIGA_RPM_RELEASE must start with a digit, for example 1 or 2.fc41." >&2
    exit 2
fi

for command in rpmbuild dpkg-deb python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "$command is required to build the RPM package." >&2
        echo "Install rpm-build (Fedora, RHEL) or rpm (openSUSE) and dpkg." >&2
        exit 2
    fi
done

SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$project_root" log -1 --format=%ct)}
export SOURCE_DATE_EPOCH

build_root=$(mktemp -d)
cleanup() {
    rm -rf -- "$build_root"
}
trap cleanup EXIT HUP INT TERM

# Build the Debian package purely to obtain the shared installed tree.
deb_dir="$build_root/deb"
mkdir -p "$deb_dir"
deb_path=$("$project_root/tools/build-linux-package.sh" "$deb_dir")

payload="$build_root/amiga-file-forge-$rpm_version"
mkdir -p "$payload"
dpkg-deb --extract "$deb_path" "$payload"

# The Debian maintainer scripts have no meaning in an RPM; its scriptlets are
# declared in the spec instead.
rm -rf "$payload/DEBIAN"

tarball="$build_root/rpmbuild/SOURCES/amiga-file-forge-$rpm_version.tar.gz"
mkdir -p \
    "$build_root/rpmbuild/SOURCES" \
    "$build_root/rpmbuild/SPECS" \
    "$build_root/rpmbuild/BUILD" \
    "$build_root/rpmbuild/RPMS"
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner \
    -czf "$tarball" -C "$build_root" "amiga-file-forge-$rpm_version"

spec="$build_root/rpmbuild/SPECS/amiga-file-forge.spec"
sed \
    -e "s/@VERSION@/$rpm_version/g" \
    -e "s/@RELEASE@/$release/g" \
    "$project_root/packaging/rpm/amiga-file-forge.spec.in" > "$spec"

rpmbuild \
    --define "_topdir $build_root/rpmbuild" \
    --define "_binary_payload w2.xzdio" \
    -bb "$spec"

mkdir -p "$output_dir"
found=$(find "$build_root/rpmbuild/RPMS" -name '*.rpm' -type f | head -n 1)
if [ -z "$found" ]; then
    echo "rpmbuild did not produce a package." >&2
    exit 1
fi
cp "$found" "$output_dir/"
echo "$output_dir/$(basename "$found")"
