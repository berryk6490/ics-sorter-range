#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
matiec_dir=${MATIEC_DIR:-/home/kevin/vm/OpenPLC_Editor/matiec}
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT

"$matiec_dir/iec2c" -I "$matiec_dir/lib" -T "$build_dir" "$repo_dir/Sorter.st" >/dev/null
cc -std=c11 -include time.h -include POUS.h -I "$build_dir" -I "$matiec_dir/lib/C" \
    "$build_dir/POUS.c" "$repo_dir/tests/first_package.c" -lm -o "$build_dir/first_package"
"$build_dir/first_package"
