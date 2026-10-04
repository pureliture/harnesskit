#!/usr/bin/env bash
# HarnessKit beta installer for macOS 13+ on Apple Silicon.
#
# Default: download the published beta DMG, verify it against the SHA-256
# pinned below, verify the app's code signature, then install HarnessKit.app.
# Needs only tools that ship with macOS (curl, hdiutil, shasum, codesign, ditto).
#
# --from-source: build the pinned beta source instead. This needs developer
# tools (Xcode Command Line Tools, Python 3.11+, Node.js 20+, uv, Rust).
#
# Everything the script downloads or builds goes into one temporary folder that
# is removed at the end. Tools you installed yourself are never removed.
set -euo pipefail

TAG="${HARNESSKIT_TAG:-v0.1.0-beta.3}"
DMG_NAME="HarnessKit_0.1.0_aarch64.dmg"
DMG_URL="${HARNESSKIT_DMG_URL:-https://github.com/pureliture/harnesskit/releases/download/${TAG}/${DMG_NAME}}"
DMG_SHA256="${HARNESSKIT_DMG_SHA256:-d0e3bd1eed8c277f8b08891e8557a5b5cee6dd85dd7ec0b13ba61cc04b5c886a}"
REPO_URL="${HARNESSKIT_REPO_URL:-https://github.com/pureliture/harnesskit.git}"
EXPECTED_SHA="${HARNESSKIT_EXPECTED_SHA:-a5bc523e7c304cdb6dba1c51f10a00a55cc1f76b}"
BUNDLE_ID="io.github.pureliture.harnesskit"
DEST_DIR="${HARNESSKIT_INSTALL_DEST:-/Applications}"
APP_NAME="HarnessKit.app"
APP_REL="src-tauri/target/aarch64-apple-darwin/release/bundle/macos/${APP_NAME}"

CHECK_ONLY=0
KEEP_WORK=0
FROM_SOURCE=0
WORK=""
MOUNT=""
STAGE=""
TARGET=""
NEW_APP=""
PROBLEMS=0

usage() {
  cat <<'USAGE'
Usage: install-beta.sh [--check] [--from-source] [--keep-work]

  (no option)    Download the verified beta app and install it.
  --check        Only check requirements. Nothing is downloaded or installed.
  --from-source  Build from source instead (needs Python, Node.js, uv, Rust).
  --keep-work    Keep the temporary folder (for debugging a failure).

Environment: HARNESSKIT_INSTALL_DEST changes the install folder
(default /Applications).
USAGE
}

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local status=$?
  if [ -n "$MOUNT" ] && [ -d "$MOUNT" ]; then
    /usr/bin/hdiutil detach -quiet "$MOUNT" >/dev/null 2>&1 \
      || /usr/bin/hdiutil detach -quiet -force "$MOUNT" >/dev/null 2>&1 || true
  fi
  if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
    # Restore the previous app if the swap was interrupted.
    if [ -d "$STAGE/previous.app" ] && [ ! -e "$TARGET" ]; then
      mv "$STAGE/previous.app" "$TARGET" && log "Previous app restored: $TARGET"
    fi
    rm -rf "$STAGE"
  fi
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then
    if [ "$KEEP_WORK" = 1 ]; then
      log "Temporary folder kept: $WORK"
    else
      rm -rf "$WORK"
      log "Temporary files removed."
    fi
  fi
  exit "$status"
}

version_at_least() { # version_at_least "3.13.1" 3 11
  local major minor
  IFS=. read -r major minor _ <<<"$1"
  [ "${major:-0}" -gt "$2" ] || { [ "${major:-0}" -eq "$2" ] && [ "${minor:-0}" -ge "$3" ]; }
}

need() { # need <command> <hint>
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'MISSING: %s -> %s\n' "$1" "$2" >&2
    PROBLEMS=1
  fi
}

check_platform() {
  if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
    version_at_least "$(sw_vers -productVersion)" 13 0 \
      || { printf 'TOO OLD: macOS 13 or newer is required.\n' >&2; PROBLEMS=1; }
  else
    printf 'MISSING: macOS on Apple Silicon (arm64) is required.\n' >&2; PROBLEMS=1
  fi
}

check_source_tools() {
  xcode-select -p >/dev/null 2>&1 \
    || { printf 'MISSING: Xcode Command Line Tools -> xcode-select --install\n' >&2; PROBLEMS=1; }
  need git "install Xcode Command Line Tools"
  need uv "https://docs.astral.sh/uv/getting-started/installation/"
  # A rustup shim can exist without a usable toolchain, so run the tools.
  if ! cargo --version >/dev/null 2>&1 || ! rustc --version >/dev/null 2>&1; then
    printf 'MISSING: a working Rust toolchain -> https://rustup.rs/ (then: rustup default stable)\n' >&2
    PROBLEMS=1
  fi
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' >/dev/null 2>&1; then
    version_at_least "$(python3 -c 'import platform; print(platform.python_version())')" 3 11 \
      || { printf 'TOO OLD: Python 3.11 or newer is required.\n' >&2; PROBLEMS=1; }
  else
    printf 'MISSING: python3 (3.11 or newer)\n' >&2; PROBLEMS=1
  fi
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    version_at_least "$(node --version | sed 's/^v//')" 20 0 \
      || { printf 'TOO OLD: Node.js 20 or newer is required.\n' >&2; PROBLEMS=1; }
  else
    printf 'MISSING: node and npm (Node.js 20 or newer)\n' >&2; PROBLEMS=1
  fi
}

check_prerequisites() {
  PROBLEMS=0
  check_platform
  if [ "$FROM_SOURCE" = 1 ]; then
    check_source_tools
  else
    need curl "ships with macOS"
  fi
  [ "$PROBLEMS" -eq 0 ] || die "Install the missing items above and run this script again."
  log "Requirements look good."
}

# ---------- default path: verified prebuilt app ----------

fetch_prebuilt() {
  log "Downloading $DMG_NAME ($TAG)"
  curl --fail --location --silent --show-error --retry 3 \
    --output "$WORK/$DMG_NAME" "$DMG_URL"
  local actual
  actual="$(/usr/bin/shasum -a 256 "$WORK/$DMG_NAME" | awk '{print $1}')"
  [ "$actual" = "$DMG_SHA256" ] \
    || die "Download checksum mismatch: expected $DMG_SHA256 but got $actual. Nothing was installed."
  log "Download checksum verified."

  MOUNT="$WORK/mnt"
  mkdir -p "$MOUNT"
  /usr/bin/hdiutil attach -readonly -nobrowse -noautoopen -quiet \
    -mountpoint "$MOUNT" "$WORK/$DMG_NAME"
  [ -d "$MOUNT/$APP_NAME" ] || die "$APP_NAME was not found in the download."
  /usr/bin/ditto "$MOUNT/$APP_NAME" "$WORK/$APP_NAME"
  /usr/bin/hdiutil detach -quiet "$MOUNT"
  MOUNT=""
  NEW_APP="$WORK/$APP_NAME"
}

# ---------- --from-source path ----------

tauri_cli_is_v2() {
  local version
  version="$(cargo tauri --version 2>/dev/null)" || return 1
  case "$version" in
    "tauri-cli 2."*) return 0 ;;
    *) return 1 ;;
  esac
}

prepare_tauri_cli() {
  if tauri_cli_is_v2; then
    log "Using the Tauri CLI 2 that is already installed."
    return
  fi
  log "Installing a temporary Tauri CLI 2 (removed at the end; this can take several minutes)."
  cargo install tauri-cli --version "^2" --locked --root "$WORK/tauri-cli"
  export PATH="$WORK/tauri-cli/bin:$PATH"
  tauri_cli_is_v2 || die "Temporary Tauri CLI 2 is not usable."
}

build_from_source() {
  log "Downloading source $TAG"
  git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"
  local actual
  actual="$(git -C "$WORK/src" rev-parse HEAD)"
  [ "$actual" = "$EXPECTED_SHA" ] \
    || die "Source commit mismatch: expected $EXPECTED_SHA but got $actual. Nothing was installed."
  log "Source commit verified: $actual"
  prepare_tauri_cli
  log "Building (first build compiles many Rust dependencies and can take a long time)."
  (
    cd "$WORK/src"
    python3 scripts/package/prepare_install_runtime.py --repo-root .
    python3 scripts/package/prepare_install_runtime.py --repo-root . --verify-only
    CI=true uv run --no-project --no-cache python \
      scripts/package/build_verified_macos_package.py \
      --repo-root . --evidence-root "$WORK/evidence"
  )
  [ -d "$WORK/src/$APP_REL" ] || die "Build finished but $APP_NAME was not found."
  NEW_APP="$WORK/src/$APP_REL"
}

# ---------- shared verification and install ----------

verify_app() { # verify_app <path>
  /usr/bin/codesign --verify --deep --strict "$1" \
    || die "App failed the code signature check. Nothing was installed."
  local id
  id="$(/usr/bin/defaults read "$1/Contents/Info" CFBundleIdentifier 2>/dev/null || true)"
  [ "$id" = "$BUNDLE_ID" ] || die "Unexpected app identifier '$id'. Nothing was installed."
}

install_app() {
  TARGET="$DEST_DIR/$APP_NAME"
  mkdir -p "$DEST_DIR"

  # 1. Copy the new app next to the destination and verify it there first.
  STAGE="$(mktemp -d "$DEST_DIR/.HarnessKit-install.XXXXXX")"
  /usr/bin/ditto "$NEW_APP" "$STAGE/$APP_NAME"
  verify_app "$STAGE/$APP_NAME"

  # 2. Back up the existing app to a folder unique to this run.
  if [ -e "$TARGET" ]; then
    local backup_root="$HOME/Library/Application Support/HarnessKit-install-backup"
    local backup_dir
    mkdir -p "$backup_root"
    backup_dir="$(mktemp -d "$backup_root/HarnessKit-$(date +%Y%m%d-%H%M%S).XXXXXX")"
    /usr/bin/ditto "$TARGET" "$backup_dir/$APP_NAME"
    log "Existing app backed up to: $backup_dir/$APP_NAME"
    mv "$TARGET" "$STAGE/previous.app"
  fi

  # 3. Swap in the verified app. On failure, cleanup restores the previous app.
  mv "$STAGE/$APP_NAME" "$TARGET"
  rm -rf "$STAGE"
  STAGE=""
  log "Installed: $TARGET"
}

main() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --check) CHECK_ONLY=1 ;;
      --from-source) FROM_SOURCE=1 ;;
      --keep-work) KEEP_WORK=1 ;;
      -h|--help) usage; return 0 ;;
      *) usage >&2; die "Unknown option: $1" ;;
    esac
    shift
  done

  check_prerequisites
  [ "$CHECK_ONLY" = 0 ] || return 0

  WORK="$(mktemp -d "${TMPDIR:-/tmp}/harnesskit-install.XXXXXX")"
  trap cleanup EXIT
  if [ "$FROM_SOURCE" = 1 ]; then
    build_from_source
  else
    fetch_prebuilt
  fi
  verify_app "$NEW_APP"
  install_app
  log "Done. Open HarnessKit from $DEST_DIR. This app is ad-hoc signed and not notarized by Apple."
}

main "$@"
