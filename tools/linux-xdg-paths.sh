#!/bin/sh

# Some sandboxed IDE terminals export their own private XDG_DATA_HOME. Files
# installed there are invisible to the user's real desktop shell. Keep Amiga
# File Forge registrations in the host user's data directory instead.
amiga_host_data_home() {
    candidate=${XDG_DATA_HOME:-}
    case "$candidate" in
        "") printf '%s\n' "$HOME/.local/share" ;;
        "$HOME"/snap/*) printf '%s\n' "$HOME/.local/share" ;;
        *) printf '%s\n' "$candidate" ;;
    esac
}
