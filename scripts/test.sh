#!/usr/bin/env bash
# Run the test suite the way CI does, on a machine whose own Python is too old.
#
#   scripts/test.sh              # everything
#   scripts/test.sh -k version   # any pytest arguments pass straight through
#
# The image is built once and reused; the working tree is mounted, so an edit
# is picked up without a rebuild. Only a change to requirements-test.txt costs
# a real rebuild.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="kinboard-ha-test"

# --quiet so a cached build is silent; a real build still reports its progress
# on stderr, which is what you want to see when it takes a few minutes.
docker build --quiet -f "$repo/scripts/Dockerfile.test" -t "$image" "$repo" >/dev/null

exec docker run --rm -v "$repo":/work -w /work "$image" pytest "$@"
