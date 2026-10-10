#!/bin/sh
# Thin launcher: uninstall policy lives in scripts/skill_install.py.
# Removing a compatibility link never touches the canonical directory.
# Removing the canonical directory requires --force.
set -u
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
engine="$script_dir/skill_install.py"
target=claude
home_dir=""
force=""

usage() {
  echo "usage: uninstall-skill.sh [--target agents|both|claude|cline|all] [--home DIR] [--force]" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --target=*) target="${1#--target=}"; shift ;;
    --home) home_dir="$2"; shift 2 ;;
    --home=*) home_dir="${1#--home=}"; shift ;;
    --force) force="--force"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

case "$target" in
  agents|both|claude|cline|all) ;;
  *) echo "invalid --target: $target" >&2; usage; exit 2 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required on PATH" >&2
  exit 1
fi

# shellcheck disable=SC2086
exec python3 "$engine" uninstall --target "$target" \
  ${home_dir:+--home "$home_dir"} \
  $force
