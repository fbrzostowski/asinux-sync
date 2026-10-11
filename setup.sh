#!/usr/bin/env bash
# Sets up Asinux System Configuration (ASC) and Asinux Sync on a Fedora/GNOME
# machine. Safe to run again: it updates what is there and leaves the rest.
#
#   curl -fsSL https://raw.githubusercontent.com/fbrzostowski/asinux-sync/master/setup.sh | bash
#   ./setup.sh --asc-from you@other-pc:/opt/asinux-system-configuration/
#
# Run `./setup.sh --help` for the options.
set -euo pipefail

SYNC_REPO="https://github.com/fbrzostowski/asinux-sync"
ASC_REPO="https://github.com/fbrzostowski/asinux-system-configuration"
PREFIX="/opt"
ASC_FROM=""
INSTALL_PACKAGES=1
SETUP_ASC=1
DRY_RUN=0

PACKAGES=(git python3-gobject gtk4 libadwaita flatpak polkit rclone gnome-control-center rsync)

usage() {
    cat <<EOF
Usage: setup.sh [options]

  --asc-from SRC   where to get Asinux System Configuration from:
                     - a git URL (default: $ASC_REPO)
                     - an rsync source, e.g. you@other-pc:/opt/asinux-system-configuration/
                       or a local folder. Use this to copy the exact state of
                       a machine whose ASC has changes that are not on GitHub.
  --sync-repo URL  git URL of Asinux Sync (default: $SYNC_REPO)
  --prefix DIR     install under DIR (default: /opt). The ASC launchers refer
                   to /opt, so anything else is only useful for testing.
  --no-packages    do not install distribution packages
  --no-asc         set up only Asinux Sync
  --dry-run        show what would be done, change nothing
  -h, --help       this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --asc-from)    ASC_FROM="${2:?--asc-from needs a value}"; shift 2 ;;
        --sync-repo)   SYNC_REPO="${2:?--sync-repo needs a value}"; shift 2 ;;
        --prefix)      PREFIX="${2:?--prefix needs a value}"; shift 2 ;;
        --no-packages) INSTALL_PACKAGES=0; shift ;;
        --no-asc)      SETUP_ASC=0; shift ;;
        --dry-run)     DRY_RUN=1; shift ;;
        -h|--help)     usage; exit 0 ;;
        *)             echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Runs a command, or only prints it with --dry-run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ %s\n' "$*"
    else
        "$@"
    fi
}

SYNC_DIR="$PREFIX/asinux-sync"
ASC_DIR="$PREFIX/asinux-system-configuration"

check_python() {
    command -v python3 >/dev/null 2>&1 || die "python3 is not installed"
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || die "Python 3.11 or newer is needed (found $(python3 -V 2>&1))"
}

install_packages() {
    if [ "$INSTALL_PACKAGES" -eq 0 ]; then
        log "Skipping packages (--no-packages)"
        return
    fi
    if ! command -v dnf >/dev/null 2>&1; then
        warn "dnf not found - install these yourself: ${PACKAGES[*]}"
        return
    fi
    local missing=() package
    for package in "${PACKAGES[@]}"; do
        rpm -q --quiet "$package" || missing+=("$package")
    done
    if [ "${#missing[@]}" -eq 0 ]; then
        log "All packages are already installed"
        return
    fi
    log "Installing: ${missing[*]} (administrator password needed)"
    run sudo dnf install -y "${missing[@]}"
}

# Creates DIR owned by the current user (asks for sudo only if it has to).
prepare_dir() {
    local dir="$1"
    if [ -d "$dir" ] && [ -w "$dir" ]; then
        return
    fi
    log "Creating $dir"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ mkdir -p %s (and make it yours)\n' "$dir"
        return
    fi
    mkdir -p "$dir" 2>/dev/null || sudo mkdir -p "$dir"
    [ -w "$dir" ] || sudo chown "$(id -un):$(id -gn)" "$dir"
}

is_git_url() {
    case "$1" in
        http://*|https://*|git@*|ssh://*|git://*|*.git) return 0 ;;
        *) return 1 ;;
    esac
}

fetch_git() {
    local url="$1" dir="$2"
    if [ -d "$dir/.git" ]; then
        log "Updating $dir"
        run git -C "$dir" pull --ff-only || warn "could not fast-forward $dir (local changes?) - left as it is"
    elif [ -z "$(ls -A "$dir" 2>/dev/null || true)" ]; then
        log "Cloning $url into $dir"
        run git clone "$url" "$dir"
    else
        die "$dir exists, is not empty and is not a git checkout - move it away or pick another --prefix"
    fi
}

fetch_rsync() {
    local source="$1" dir="$2"
    command -v rsync >/dev/null 2>&1 || [ "$DRY_RUN" -eq 1 ] || die "rsync is not installed"
    log "Copying $source into $dir"
    # Trailing slash: copy the folder's contents. Never --delete: the target may hold local work.
    run rsync -a --exclude __pycache__ --exclude .git --exclude .pytest_cache "${source%/}/" "$dir/"
}

setup_sync() {
    log "Asinux Sync"
    prepare_dir "$SYNC_DIR"
    fetch_git "$SYNC_REPO" "$SYNC_DIR"
}

setup_asc() {
    log "Asinux System Configuration"
    prepare_dir "$ASC_DIR"
    if [ -z "$ASC_FROM" ]; then
        warn "taking ASC from GitHub; if your other machine has newer, unpushed panels, re-run with"
        warn "  --asc-from you@other-pc:/opt/asinux-system-configuration/"
        fetch_git "$ASC_REPO" "$ASC_DIR"
    elif is_git_url "$ASC_FROM"; then
        fetch_git "$ASC_FROM" "$ASC_DIR"
    else
        fetch_rsync "$ASC_FROM" "$ASC_DIR"
    fi
}

# The same three links ASC uses on the machine it was developed on: the app
# launcher, and the GNOME Shell search provider (which needs its .ini where
# Shell looks for it and a D-Bus service file to be started on demand).
link_launchers() {
    log "Linking the launcher and the search provider"
    [ "$PREFIX" = "/opt" ] || warn "the launchers point at /opt/asinux-system-configuration, not $ASC_DIR"
    local data="$HOME/.local/share"
    run mkdir -p "$data/applications" "$data/dbus-1/services" \
        "$data/flatpak/exports/share/gnome-shell/search-providers"
    run ln -sfn "$ASC_DIR/com.asinux.SystemConfiguration.desktop" \
        "$data/applications/com.asinux.SystemConfiguration.desktop"
    run ln -sfn "$ASC_DIR/com.asinux.SystemConfiguration.SearchProvider.service" \
        "$data/dbus-1/services/com.asinux.SystemConfiguration.SearchProvider.service"
    run ln -sfn "$ASC_DIR/com.asinux.SystemConfiguration.search-provider.ini" \
        "$data/flatpak/exports/share/gnome-shell/search-providers/com.asinux.SystemConfiguration.search-provider.ini"
    if command -v update-desktop-database >/dev/null 2>&1; then
        run update-desktop-database "$data/applications" || true
    fi
}

verify() {
    [ "$DRY_RUN" -eq 1 ] && return
    log "Checking the result"
    python3 - <<'PY' || die "PyGObject, GTK 4 or libadwaita is missing - install python3-gobject gtk4 libadwaita"
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk
PY
    command -v rclone >/dev/null 2>&1 || warn "rclone is not installed yet (the Asinux Sync tile offers to install it)"
    [ -f "$SYNC_DIR/main.py" ] || die "$SYNC_DIR/main.py is missing"
    if [ "$SETUP_ASC" -eq 1 ]; then
        [ -f "$ASC_DIR/main.py" ] || die "$ASC_DIR/main.py is missing"
    fi
}

next_steps() {
    cat <<EOF

Done. Next:
EOF
    if [ "$SETUP_ASC" -eq 1 ]; then
        echo "  1. Open Asinux System Configuration (Activities search, or: python3 $ASC_DIR/main.py)"
        echo "     and choose System -> Asinux Sync."
    else
        echo "  1. Run: python3 $SYNC_DIR/main.py"
    fi
    cat <<EOF
  2. Sign in to OneDrive, name this device and continue. The background
     service is installed and started for you.
  3. The first cycle downloads your synced settings and applies them here.
     Changes show up after a minute (or after the next login for language).

Update later by running this script again.
EOF
}

main() {
    [ "$DRY_RUN" -eq 1 ] && log "Dry run: nothing will be changed"
    check_python
    install_packages
    setup_sync
    if [ "$SETUP_ASC" -eq 1 ]; then
        setup_asc
        link_launchers
    fi
    verify
    next_steps
}

main
