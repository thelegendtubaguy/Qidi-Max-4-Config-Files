#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

for tool in python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing required tool: $tool" >&2
    exit 1
  fi
done

mode="install"
plain_arg=""
debug_arg=""
dry_run_arg=""
demo_tui_arg=""
yes_arg=""
skip_system_arg=""
disable_ai_arg=""
keep_ai_arg=""
keep_system_arg=""
reboot_host_arg=""

package_manifest_path() {
  if [ -f installer/package.yaml ]; then
    printf '%s\n' "installer/package.yaml"
  elif [ -f ../package.yaml ]; then
    printf '%s\n' "../package.yaml"
  else
    printf '%s\n' "installer/package.yaml"
  fi
}

package_version() {
  PACKAGE_MANIFEST_PATH=$(package_manifest_path) python3 -I -S - <<'PY'
from pathlib import Path
import os
import re
import sys

path = Path(os.environ["PACKAGE_MANIFEST_PATH"])
text = path.read_text(encoding="utf-8")
match = re.search(r'(?m)^package:\n(?:^[ \t]+.*\n)*?^[ \t]+version:\s*["\']?([^"\'\n]+)', text)
if match is None:
    sys.exit(f"Could not read package.version from {path}")
print(match.group(1))
PY
}

show_version() {
  version=$(package_version)
  printf 'QIDI Max 4 Optimized installer %s\n' "$version"
}

show_help() {
  show_version
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  -h, --help                    Show this help text and exit.
  -v, --version                 Show the installer package version and exit.
  --uninstall                   Restore stock/previous Klipper config changes and uninstall TLTG configs.
  --clear-recovery-sentinel     Clear the recovery sentinel after a verified manual restore.
  --plain                       Use plain text output instead of the rich terminal UI.
  --debug                       Print debug events and tracebacks on failure.
  --dry-run                     Preview install or uninstall actions without writing changes.
  --demo-tui                    Render a demo of the install/uninstall TUI without writing changes.
  --yes                         Run non-interactively using default yes-mode choices.
  --skip-system-optimizations   Skip DNS, APT, qidiclient GIF, Rockchip root-mount, VPN, Bluetooth, and AI service changes.
  --disable-ai-detection        Disable the QIDI AI detection backend service when system optimizations run.
  --keep-ai-detection           Keep the QIDI AI detection backend service enabled when system optimizations run.
  --keep-system-optimizations   During uninstall, leave installer-managed system settings in place.
  --reboot-host                 Authorize a pending managed host OS reboot after success.

Common examples:
  ./install.sh --plain
  ./install.sh --dry-run --plain
  ./install.sh --uninstall --plain
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    -v|--version)
      show_version
      exit 0
      ;;
    --uninstall)
      if [ "$mode" != "install" ]; then
        echo "Unsupported argument combination." >&2
        exit 1
      fi
      mode="uninstall"
      ;;
    --clear-recovery-sentinel)
      if [ "$mode" != "install" ]; then
        echo "Unsupported argument combination." >&2
        exit 1
      fi
      mode="clear-recovery-sentinel"
      ;;
    --plain)
      plain_arg="--plain"
      ;;
    --debug)
      debug_arg="--debug"
      ;;
    --dry-run)
      dry_run_arg="--dry-run"
      ;;
    --demo-tui)
      demo_tui_arg="--demo-tui"
      ;;
    --yes)
      yes_arg="--yes"
      ;;
    --skip-system-optimizations)
      skip_system_arg="--skip-system-optimizations"
      ;;
    --disable-ai-detection)
      disable_ai_arg="--disable-ai-detection"
      ;;
    --keep-ai-detection)
      keep_ai_arg="--keep-ai-detection"
      ;;
    --keep-system-optimizations)
      keep_system_arg="--keep-system-optimizations"
      ;;
    --reboot-host)
      reboot_host_arg="--reboot-host"
      ;;
    *)
      echo "Unsupported argument: $1" >&2
      exit 1
      ;;
  esac
  shift
done

set -- python3 -I -S installer/runtime/bootstrap.py "$mode"
if [ -n "$plain_arg" ]; then
  set -- "$@" "$plain_arg"
fi
if [ -n "$debug_arg" ]; then
  set -- "$@" "$debug_arg"
fi
if [ -n "$dry_run_arg" ]; then
  set -- "$@" "$dry_run_arg"
fi
if [ -n "$demo_tui_arg" ]; then
  set -- "$@" "$demo_tui_arg"
fi
if [ -n "$yes_arg" ]; then
  set -- "$@" "$yes_arg"
fi
if [ -n "$skip_system_arg" ]; then
  set -- "$@" "$skip_system_arg"
fi
if [ -n "$disable_ai_arg" ]; then
  set -- "$@" "$disable_ai_arg"
fi
if [ -n "$keep_ai_arg" ]; then
  set -- "$@" "$keep_ai_arg"
fi
if [ -n "$keep_system_arg" ]; then
  set -- "$@" "$keep_system_arg"
fi
if [ -n "$reboot_host_arg" ]; then
  set -- "$@" "$reboot_host_arg"
fi
exec "$@"
