#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv="$project_root/.venv-desktop"
. "$project_root/tools/linux-xdg-paths.sh"
inherited_data_home=${XDG_DATA_HOME:-}
data_home=$(amiga_host_data_home)
host_data_dirs=${XDG_DATA_DIRS_VSCODE_SNAP_ORIG:-${XDG_DATA_DIRS:-/usr/local/share:/usr/share}}
applications="$data_home/applications"
icon_theme="$data_home/icons/hicolor"
icons="$icon_theme/scalable/apps"
mime_packages="$data_home/mime/packages"
desktop_file="$applications/uk.co.amigafileforge.AmigaFileForge.desktop"
launcher="$project_root/tools/amiga-file-forge-desktop"
user_bin="$HOME/.local/bin"
registered_launcher="$user_bin/amiga-file-forge"

if ! command -v make >/dev/null 2>&1 || ! command -v cc >/dev/null 2>&1; then
    echo "Native build tools are missing. On Ubuntu or Debian install build-essential and python3-dev." >&2
    exit 2
fi

python3 - <<'PY'
try:
    import gi
    gi.require_version("Adw", "1")
    gi.require_version("Gtk", "4.0")
    gi.require_version("WebKit", "6.0")
    from gi.repository import Adw, Gtk, WebKit  # noqa: F401
except (ImportError, ValueError) as exc:
    raise SystemExit(
        "GTK desktop dependencies are missing. On Ubuntu or Debian install: "
        "python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0"
    ) from exc
PY

python3 -m venv --system-site-packages "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install -r "$project_root/requirements.txt"

mkdir -p "$applications" "$icons" "$mime_packages" "$user_bin"
if [ -e "$registered_launcher" ] && [ ! -L "$registered_launcher" ]; then
    echo "Cannot register $registered_launcher because it already exists and is not a symbolic link." >&2
    exit 2
fi
ln -sfn "$launcher" "$registered_launcher"
rm -f "$icons/uk.co.amigafileforge.AmigaFileForge.svg"
cp "$project_root/app/static/favicon.svg" \
    "$icons/amiga-file-forge.svg"
cp -a "$project_root/packaging/linux/icons/." "$icon_theme/"
find "$icon_theme" -path "*/apps/amiga-file-forge.png" \
    -exec chmod 644 {} +
cp "$project_root/packaging/linux/uk.co.amigafileforge.AmigaFileForge.xml" \
    "$mime_packages/uk.co.amigafileforge.AmigaFileForge.xml"
sed \
    -e "s|@EXEC@|$registered_launcher|g" \
    -e "s|@TRY_EXEC@|$registered_launcher|g" \
    "$project_root/packaging/linux/uk.co.amigafileforge.AmigaFileForge.desktop.in" \
    > "$desktop_file"
chmod 755 "$launcher"
chmod 644 "$desktop_file" \
    "$icons/amiga-file-forge.svg" \
    "$mime_packages/uk.co.amigafileforge.AmigaFileForge.xml"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop_file"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
        update-desktop-database "$applications"
fi
for icon_cache_tool in gtk4-update-icon-cache gtk-update-icon-cache; do
    if command -v "$icon_cache_tool" >/dev/null 2>&1; then
        XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
            "$icon_cache_tool" -f -t "$icon_theme" >/dev/null 2>&1 || true
    fi
done
if command -v update-mime-database >/dev/null 2>&1; then
    XDG_DATA_HOME="$data_home" XDG_DATA_DIRS="$host_data_dirs" \
        update-mime-database "$data_home/mime"
fi

case "$inherited_data_home" in
    "$HOME"/snap/*)
        if [ "$inherited_data_home" != "$data_home" ]; then
            rm -f \
                "$inherited_data_home/applications/uk.co.amigafileforge.AmigaFileForge.desktop" \
                "$inherited_data_home/icons/hicolor/scalable/apps/uk.co.amigafileforge.AmigaFileForge.svg" \
                "$inherited_data_home/icons/hicolor/scalable/apps/amiga-file-forge.svg" \
                "$inherited_data_home/mime/packages/uk.co.amigafileforge.AmigaFileForge.xml"
        fi
        ;;
esac

echo "Amiga File Forge desktop is installed for this user."
echo "Launch it from the application menu or run: $registered_launcher"
if ! command -v gw >/dev/null 2>&1; then
    echo "Optional: install the official Greaseweazle tools to enable physical floppy writing."
    echo "See docs/PHYSICAL-FLOPPY-GUIDE.md for setup and verification guidance."
fi
