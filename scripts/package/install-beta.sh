#!/usr/bin/env bash
# HarnessKit developer-beta installer for macOS Apple Silicon.
#
# Builds the pinned beta source in a temporary folder, copies HarnessKit.app to
# the destination folder, then removes everything it created in the temporary
# folder (source, build cache and a temporary Tauri CLI). Tools you installed
# yourself (Rust, Node, uv, ...) and Cargo's shared download cache are kept.
set -euo pipefail

REPO_URL="${HARNESSKIT_REPO_URL:-https://github.com/pureliture/harnesskit.git}"
TAG="${HARNESSKIT_TAG:-v0.1.0-beta.3}"
EXPECTED_SHA="${HARNESSKIT_EXPECTED_SHA:-a5bc523e7c304cdb6dba1c51f10a00a55cc1f76b}"
DEST_DIR="${HARNESSKIT_INSTALL_DEST:-/Applications}"
APP_NAME="HarnessKit.app"
APP_REL="src-tauri/target/aarch64-apple-darwin/release/bundle/macos/${APP_NAME}"

CHECK_ONLY=0
KEEP_WORK=0
WORK=""
STAGE=""
TARGET=""

usage() {
  cat <<'USAGE'
Usage: install-beta.sh [--check] [--keep-work]

  --check      Only check prerequisites. Nothing is downloaded or installed.
  --keep-work  Keep the temporary build folder (for debugging a failed build).

Environment: HARNESSKIT_INSTALL_DEST changes the install folder
(default /Applications).
USAGE
}

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local status=$?
  if [ -n "$STAGE" ] && [ -d "$STAGE" ]; then
    # Restore the previous app if the swap was interrupted.
    if [ -d "$STAGE/previous.app" ] && [ ! -e "$TARGET" ]; then
      mv "$STAGE/previous.app" "$TARGET" && log "Previous app restored: $TARGET"
    fi
    rm -rf "$STAGE"
  fi
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then
    if [ "$KEEP_WORK" = 1 ] || { [ "$status" -ne 0 ] && [ "${HARNESSKIT_KEEP_ON_FAIL:-0}" = 1 ]; }; then
      log "Temporary folder kept: $WORK"
    else
      rm -rf "$WORK"
      log "Temporary build files removed."
    fi
  fi
  exit "$status"
}

version_at_least() { # version_at_least "3.13.1" 3 11
  local major minor
  IFS=. read -r major minor _ <<<"$1"
  [ "${major:-0}" -gt "$2" ] || { [ "${major:-0}" -eq "$2" ] && [ "${minor:-0}" -ge "$3" ]; }
}

check_prerequisites() {
  local problems=0
  need() { # need <command> <hint>
    if ! command -v "$1" >/dev/null 2>&1; then
      printf 'MISSING: %s -> %s\n' "$1" "$2" >&2
      problems=1
    fi
  }

  if [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ]; then
    version_at_least "$(sw_vers -productVersion)" 13 0 \
      || { printf 'TOO OLD: macOS 13 or newer is required.\n' >&2; problems=1; }
  else
    printf 'MISSING: macOS on Apple Silicon (arm64) is required.\n' >&2; problems=1
  fi
  xcode-select -p >/dev/null 2>&1 \
    || { printf 'MISSING: Xcode Command Line Tools -> xcode-select --install\n' >&2; problems=1; }
  need git "install Xcode Command Line Tools"
  need uv "https://docs.astral.sh/uv/getting-started/installation/"
  need cargo "install Rust with https://rustup.rs/"
  need rustc "install Rust with https://rustup.rs/"

  if command -v python3 >/dev/null 2>&1; then
    version_at_least "$(python3 -c 'import platform; print(platform.python_version())')" 3 11 \
      || { printf 'TOO OLD: Python 3.11 or newer is required.\n' >&2; problems=1; }
  else
    printf 'MISSING: python3 (3.11 or newer)\n' >&2; problems=1
  fi
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    version_at_least "$(node --version | sed 's/^v//')" 20 0 \
      || { printf 'TOO OLD: Node.js 20 or newer is required.\n' >&2; problems=1; }
  else
    printf 'MISSING: node and npm (Node.js 20 or newer)\n' >&2; problems=1
  fi

  [ "$problems" -eq 0 ] || die "Install the missing items above and run this script again."
  log "Prerequisites look good."
}

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

fetch_source() {
  log "Downloading $TAG"
  git clone --quiet --depth 1 --branch "$TAG" "$REPO_URL" "$WORK/src"
  local actual
  actual="$(git -C "$WORK/src" rev-parse HEAD)"
  [ "$actual" = "$EXPECTED_SHA" ] \
    || die "Source commit mismatch: expected $EXPECTED_SHA but got $actual. Nothing was installed."
  log "Source commit verified: $actual"
}

build_app() {
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
  /usr/bin/codesign --verify --deep --strict "$WORK/src/$APP_REL" \
    || die "Built app failed the code signature check. Nothing was installed."
}

install_app() {
  TARGET="$DEST_DIR/$APP_NAME"
  mkdir -p "$DEST_DIR"

  # 1. Copy the new app next to the destination and verify it there first.
  STAGE="$(mktemp -d "$DEST_DIR/.HarnessKit-install.XXXXXX")"
  /usr/bin/ditto "$WORK/src/$APP_REL" "$STAGE/$APP_NAME"
  /usr/bin/codesign --verify --deep --strict "$STAGE/$APP_NAME" \
    || die "Copied app failed the code signature check. The existing app was not changed."

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
  fetch_source
  prepare_tauri_cli
  build_app
  install_app
  log "Done. Open HarnessKit from $DEST_DIR. This app is ad-hoc signed and not notarized by Apple."
}

main "$@"
