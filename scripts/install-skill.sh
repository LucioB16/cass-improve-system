#!/bin/sh
# Thin launcher: all install policy lives in scripts/skill_install.py so
# Windows/POSIX behavior cannot diverge. Requires python3.
set -u
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
engine="$script_dir/skill_install.py"
target=both
source_dir=""
home_dir=""
force=""

usage() {
  echo "usage: install-skill.sh [--target agents|both|claude|cline|all] [--source DIR] [--home DIR] [--force]" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="$2"; shift 2 ;;
    --target=*) target="${1#--target=}"; shift ;;
    --source) source_dir="$2"; shift 2 ;;
    --source=*) source_dir="${1#--source=}"; shift ;;
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
exec python3 "$engine" install --target "$target" \
  ${source_dir:+--source "$source_dir"} \
  ${home_dir:+--home "$home_dir"} \
  $force
