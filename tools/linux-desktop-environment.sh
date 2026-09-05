#!/bin/sh

# Shared process environment for checkout-local and packaged Linux launchers.
amiga_prepare_desktop_environment() {
    webkit_fallback=${AMIGA_FILE_FORGE_DISABLE_WEBKIT_SANDBOX:-auto}
    case $webkit_fallback in
        0)
            unset WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS
            ;;
        1)
            WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
            export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS
            ;;
        auto)
            unset WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS
            if [ -r /proc/sys/kernel/apparmor_restrict_unprivileged_userns ] &&
                [ "$(sed -n '1p' /proc/sys/kernel/apparmor_restrict_unprivileged_userns)" = 1 ]; then
                WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1
                export WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS
            fi
            ;;
        *)
            echo "AMIGA_FILE_FORGE_DISABLE_WEBKIT_SANDBOX must be auto, 0 or 1." >&2
            return 2
            ;;
    esac

    case ${SNAP:-} in
        "") ;;
        *)
            unset \
                GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GIO_MODULE_DIR \
                GSETTINGS_SCHEMA_DIR GTK_EXE_PREFIX GTK_IM_MODULE_FILE GTK_MODULES \
                GTK_PATH LOCPATH
            case ${XDG_DATA_HOME:-} in
                "$HOME"/snap/*) unset XDG_DATA_HOME ;;
            esac
            if [ -n "${XDG_DATA_DIRS_VSCODE_SNAP_ORIG:-}" ]; then
                XDG_DATA_DIRS=$XDG_DATA_DIRS_VSCODE_SNAP_ORIG
                export XDG_DATA_DIRS
            fi
            ;;
    esac
}
