#!/bin/sh
# entrypoint.sh — variant dispatcher for unified Dockerfile.
#
# The Dockerfile sets $CODEORACLE_VARIANT from the VARIANT build arg.
# This script execs `python -m codeoracle.$CODEORACLE_VARIANT` so each variant
# runs as the container's PID 1 with proper signal handling.

set -e

if [ -z "${CODEORACLE_VARIANT}" ]; then
    echo "entrypoint.sh: CODEORACLE_VARIANT is not set" >&2
    exit 1
fi

exec python -m "codeoracle.${CODEORACLE_VARIANT}" "$@"
