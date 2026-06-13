#!/usr/bin/env bash
# Pinned-env consistency check.
#
# Validates that infra/versions/pinned.env is shell-sourceable, that every
# declared image/version/model key needed by compose and CI is non-empty, and
# that every IMAGE_* value is digest-qualified.

set -euo pipefail

PINS="${1:-infra/versions/pinned.env}"

if [ ! -f "$PINS" ]; then
  echo "::error::Pinned env file not found: $PINS"
  exit 2
fi

if ! bash -n "$PINS"; then
  echo "::error::$PINS does not parse as a shell-sourceable file"
  exit 1
fi

set -a
source "$PINS"
set +a

if grep -nE 'TBD|PLACEHOLDER|FILL_ME|sha256:<digest>' "$PINS"; then
  echo "::error::Placeholder strings remain in $PINS"
  exit 1
fi

image_violations=$(grep -E '^IMAGE_[A-Z_]+=' "$PINS" | \
  grep -vE '@sha256:[a-f0-9]{64}$' || true)
if [ -n "$image_violations" ]; then
  echo "::error::IMAGE_* keys without a sha256 digest:"
  echo "$image_violations"
  exit 1
fi

missing=0
while IFS= read -r key; do
  value="${!key:-}"
  if [ -z "$value" ]; then
    echo "::error::$key is empty in $PINS"
    missing=1
  fi
done < <(grep -oE \
  '^(IMAGE_[A-Z_]+|[A-Z_]+_VERSION|QWEN_MODEL_ID|OPENAI_[A-Z_]+|PGVECTOR_INSTALL_METHOD|JOERN_RELEASE_URL|SCIP_[A-Z_]+|OPENGREP_[A-Z_]+)=' \
  "$PINS" | sed 's/=//' | sort -u)

if [ "$missing" -ne 0 ]; then
  exit 1
fi

echo "Pinned-env consistency check passed: $PINS"
