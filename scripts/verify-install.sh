#!/bin/sh
# Thin launcher: verification policy lives in scripts/skill_install.py.
set -u
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
engine="$script_dir/skill_install.py"
source_dir=""
home_dir=""

usage() {
  echo "usage: verify-install.sh [--source DIR] [--home DIR]" >&2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --source) source_dir="$2"; shift 2 ;;
    --source=*) source_dir="${1#--source=}"; shift ;;
    --home) home_dir="$2"; shift 2 ;;
    --home=*) home_dir="${1#--home=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required on PATH" >&2
  exit 1
fi

# shellcheck disable=SC2086
exec python3 "$engine" verify \
  ${source_dir:+--source "$source_dir"} \
  ${home_dir:+--home "$home_dir"}
