#!/usr/bin/env bash
# Refreshes nestwork/c2pa_trust_list.pem, the trust anchors provenance.py uses to tell a verified C2PA signer from
# an unverified one, from the C2PA organisation's own published trust list:
#   https://github.com/c2pa-org/conformance-public/tree/main/trust-list
# The file is replaced only when the download parses as PEM certificates. Review the diff before committing.
#
# Usage: tools/refresh_c2pa_trust_list.sh
set -euo pipefail

url="https://raw.githubusercontent.com/c2pa-org/conformance-public/main/trust-list/C2PA-TRUST-LIST.pem"
out="$(cd "$(dirname "$0")/.." && pwd)/nestwork/c2pa_trust_list.pem"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL --max-time 60 "$url" -o "$tmp/list.pem"

count="$(grep -c -- '-----BEGIN CERTIFICATE-----' "$tmp/list.pem" || true)"
[ "$count" -gt 0 ] || { echo "No certificates in the download; $out left unchanged." >&2; exit 1; }
# every certificate must parse
awk -v dir="$tmp" '/-----BEGIN CERTIFICATE-----/ {n++} n {print > (dir "/cert" n ".pem")}' "$tmp/list.pem"
for c in "$tmp"/cert*.pem; do
  openssl x509 -in "$c" -noout 2>/dev/null || { echo "A certificate didn't parse; $out left unchanged." >&2; exit 1; }
done

{
  echo "# C2PA trust list: trust anchors for verified C2PA signers (see nestwork/provenance.py)."
  echo "# Source: $url"
  echo "# Downloaded: $(date -u +%Y-%m-%d) by tools/refresh_c2pa_trust_list.sh"
  echo "# Upstream SHA-256: $(shasum -a 256 "$tmp/list.pem" | cut -d' ' -f1)"
  echo
  cat "$tmp/list.pem"
} > "$out"
echo "Saved $count certificates to $out"
