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

  [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] \
    || { printf 'MISSING: macOS on Apple Silicon (arm64) is required.\n' >&2; problems=1; }
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

prepare_tauri_cli() {
  if cargo tauri --version >/dev/null 2>&1; then
    log "Using the Tauri CLI that is already installed."
    return
  fi
  log "Installing a temporary Tauri CLI (removed at the end; this can take several minutes)."
  cargo install tauri-cli --version "^2" --locked --root "$WORK/tauri-cli"
  export PATH="$WORK/tauri-cli/bin:$PATH"
  cargo tauri --version >/dev/null 2>&1 || die "Temporary Tauri CLI is not usable."
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
    npm ci --prefix src-frontend --ignore-scripts
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
  local target="$DEST_DIR/$APP_NAME"
  mkdir -p "$DEST_DIR"
  if [ -e "$target" ]; then
    local backup_dir="$HOME/Library/Application Support/HarnessKit-install-backup"
    local stamp backup
    stamp="$(date +%Y%m%d-%H%M%S)"
    backup="$backup_dir/HarnessKit-$stamp.app"
    mkdir -p "$backup_dir"
    log "Existing app backed up to: $backup"
    /usr/bin/ditto "$target" "$backup"
    rm -rf "$target"
  fi
  /usr/bin/ditto "$WORK/src/$APP_REL" "$target"
  log "Installed: $target"
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
