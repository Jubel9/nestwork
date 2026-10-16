#!/usr/bin/env bash
# Regenerates the throwaway C2PA test certificate chain the awakening tests sign with:
#   c2pa_test_chain.pem  signer cert (CN=Test Signer) + intermediate (CN=Test Intermediate)
#   c2pa_test_key.pk8    the signer's private key (PKCS#8, unencrypted)
# ES256 (P-256), valid 10 years. The root CA and intermediate keys are thrown away. Test-only: Nestwork never trusts
# this chain, and no real signing key belongs here.
#
# Usage: tests/fixtures/make_c2pa_test_chain.sh
set -euo pipefail

out="$(cd "$(dirname "$0")" && pwd)"
days=3650
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"

cat > ca.ext <<'EOF'
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
EOF
cat > intermediate.ext <<'EOF'
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF
cat > signer.ext <<'EOF'
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = emailProtection
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

key() { openssl ecparam -name prime256v1 -genkey -noout -out "$1"; }

key root.key
openssl req -new -key root.key -subj "/C=US/O=Test/CN=Test Root CA" -out root.csr
openssl x509 -req -in root.csr -signkey root.key -days "$days" -sha256 -extfile ca.ext -out root.pem

key intermediate.key
openssl req -new -key intermediate.key -subj "/C=US/O=Test/CN=Test Intermediate" -out intermediate.csr
openssl x509 -req -in intermediate.csr -CA root.pem -CAkey root.key -CAcreateserial -days "$days" -sha256 \
  -extfile intermediate.ext -out intermediate.pem

key signer.key
openssl req -new -key signer.key -subj "/C=US/O=Test/CN=Test Signer" -out signer.csr
openssl x509 -req -in signer.csr -CA intermediate.pem -CAkey intermediate.key -CAcreateserial -days "$days" -sha256 \
  -extfile signer.ext -out signer.pem

openssl verify -CAfile root.pem -untrusted intermediate.pem signer.pem
cat signer.pem intermediate.pem > "$out/c2pa_test_chain.pem"
openssl pkcs8 -topk8 -nocrypt -in signer.key -out "$out/c2pa_test_key.pk8"
