#!/bin/sh
set -eu

output_dir=${1:?Usage: build-hxc-runtime.sh OUTPUT_DIRECTORY}
hxc_revision=b1eee4cd73391ceaf2ad4ac57e28bf11c91333ba
hxc_jobs=${AMIGA_HXC_BUILD_JOBS:-2}
build_root=$(mktemp -d)

case $hxc_jobs in
    *[!0-9]*|0) echo "AMIGA_HXC_BUILD_JOBS must be a positive integer." >&2; exit 2 ;;
esac

cleanup() {
    rm -rf -- "$build_root"
}
trap cleanup EXIT HUP INT TERM

for command in git make gcc; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "$command is required to build the HxC runtime." >&2
        exit 2
    fi
done

git clone --filter=blob:none --no-checkout \
    https://github.com/jfdelnero/HxCFloppyEmulator.git \
    "$build_root/source"
git -C "$build_root/source" checkout --detach "$hxc_revision"
make -j "$hxc_jobs" -C "$build_root/source/build" HxCFloppyEmulator_cmdline

mkdir -p "$output_dir/bin" "$output_dir/lib" "$output_dir/share/licenses"
install -m 755 "$build_root/source/build/hxcfe" "$output_dir/bin/hxcfe"
install -m 644 "$build_root/source/build/libhxcfe.so" "$output_dir/lib/libhxcfe.so"
install -m 644 "$build_root/source/build/libusbhxcfe.so" "$output_dir/lib/libusbhxcfe.so"
install -m 644 \
    "$build_root/source/HxCFloppyEmulator_cmdline/COPYING" \
    "$output_dir/share/licenses/HxCFloppyEmulator-COPYING"

LD_LIBRARY_PATH="$output_dir/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$output_dir/bin/hxcfe" -help >/dev/null
