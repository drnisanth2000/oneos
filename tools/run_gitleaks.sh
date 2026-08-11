#!/usr/bin/env bash
set -u

repo=${1:-.}
gitleaks_bin=${GITLEAKS_BIN:-gitleaks}
required=8.30.1

actual=$("$gitleaks_bin" version 2>/dev/null) || {
  echo "Gitleaks $required is required" >&2
  exit 2
}
if [ "$actual" != "$required" ]; then
  echo "required Gitleaks version is $required" >&2
  exit 2
fi

temp_root=${TMPDIR:-/tmp}
temp_root=${temp_root%/}
report=$(mktemp "$temp_root/oneos-gitleaks.XXXXXX.json") || exit 2
trap 'rm -f "$report"' EXIT HUP INT TERM

"$gitleaks_bin" git --no-banner --redact=100 --exit-code=1 \
  --report-format=json --report-path="$report" "$repo"
