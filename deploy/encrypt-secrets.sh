#!/bin/sh
# Encrypt the complete production dotenv without exposing tool diagnostics.
set -eu
if [ "$#" -ne 0 ]; then
    echo 'Usage: encrypt-secrets.sh (no arguments)' >&2
    exit 1
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
command -v sops >/dev/null 2>&1 || { echo 'SOPS is required.' >&2; exit 1; }
test -r .env.server && test -r .sops.yaml || {
    echo 'Readable .env.server and .sops.yaml are required.' >&2
    exit 1
}
umask 077
temporary=$(mktemp secrets/.production.enc.XXXXXX)
trap 'rm -f "$temporary"' EXIT
trap 'exit 1' HUP INT TERM
if ! sops --config "$root/.sops.yaml" --encrypt --input-type dotenv \
    --output-type dotenv .env.server > "$temporary" 2>/dev/null; then
    echo 'Encryption failed; existing production secrets were preserved. SOPS diagnostics suppressed.' >&2
    exit 1
fi
test -s "$temporary" || { echo 'SOPS returned an empty result.' >&2; exit 1; }
mv -- "$temporary" secrets/production.enc.env
echo 'Encrypted secrets/production.enc.env. No Git operations performed.'
