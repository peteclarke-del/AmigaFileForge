#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
. "$project_root/tools/linux-xdg-paths.sh"
inherited_data_home=${XDG_DATA_HOME:-}
data_home=$(amiga_host_data_home)
host_data_dirs=${XDG_DATA_DIRS_VSCODE_SNAP_ORIG:-${XDG_DATA_DIRS:-/usr/local/share:/usr/share}}
registered_launcher="$HOME/.local/bin/amiga-file-forge"

rm -f \
    "$data_home/applications/uk.co.amigafileforge.AmigaFileForge.desktop" \
    "$data_home/icons/hicolor/scalable/apps/uk.co.amigafileforge.AmigaFileForge.svg" \
    "$data_home/icons/hicolor/scalable/apps/amiga-file-forge.svg" \
    "$data_home/mime/packages/uk.co.amigafileforge.AmigaFileForge.xml"
if [ -d "$data_home/icons/hicolor" ]; then
    find "$data_home/icons/hicolor" \
        -path "*/apps/amiga-file-forge.png" -delete
fi
if [ -L "$registered_launcher" ]; then
    rm -f "$registered_launcher"
fi
case "$inherited_data_home" in
    "$HOME"/snap/*)
        rm -f \
            "$inherited_data_home/applications/uk.co.amigafileforge.AmigaFileForge.desktop" \
            "$inherited_data_home/icons/hicolor/scalable/apps/uk.co.amigafileforge.AmigaFileForge.svg" \
            "$inherited_data_home/icons/hicolor/scalable/apps/amiga-file-forge.svg" \
            "$inherited_data_home/mime/packages/uk.co.amigafileforge.AmigaFileForge.xml"
        ;;
esac
rm -rf "$project_root/.venv-desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
        update-desktop-database "$data_home/applications"
fi
if command -v update-mime-database >/dev/null 2>&1; then
    XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
        update-mime-database "$data_home/mime"
fi
for icon_cache_tool in gtk4-update-icon-cache gtk-update-icon-cache; do
    if command -v "$icon_cache_tool" >/dev/null 2>&1; then
        XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
            "$icon_cache_tool" -f -t "$data_home/icons/hicolor" \
            >/dev/null 2>&1 || true
    fi
done
echo "Amiga File Forge desktop was removed. Working images under the XDG data directory were retained."
