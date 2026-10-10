#!/usr/bin/env bash
# Build a pinned release tarball on a workstation (Linux, macOS, WSL or Git Bash).
# The client is built here, not on the 1 GiB host.
#
#   deploy/scripts/build_release.sh            # -> dist-release/shadowtrace-<sha>.tar.gz
#
# Refuses to build from a dirty tree, so a release always maps to one commit.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  echo "refusing: working tree has uncommitted changes (ALLOW_DIRTY=1 to override for a test build)" >&2
  exit 1
fi
SHA=$(git rev-parse --short=12 HEAD)
OUT=dist-release
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

# NPM_INSTALL=skip reuses an existing node_modules (installed from the same lock),
# e.g. when a running dev server holds esbuild open on Windows.
if [ "${NPM_INSTALL:-ci}" = "skip" ] && [ -d client/node_modules ]; then
  (cd client && npm run build)
else
  (cd client && npm ci && npm run build)
fi

mkdir -p "$STAGE/shadowtrace-$SHA"
git -c core.autocrlf=false archive HEAD src pyproject.toml requirements-lock.txt requirements-postgres.txt README.md \
  config deploy tools/ops docs/DEPLOYMENT.md | tar -x -C "$STAGE/shadowtrace-$SHA"
mkdir -p "$STAGE/shadowtrace-$SHA/client"
cp -r client/dist "$STAGE/shadowtrace-$SHA/client/dist"
printf '%s\n' "$SHA" > "$STAGE/shadowtrace-$SHA/RELEASE"

mkdir -p "$OUT"
tar -C "$STAGE" -czf "$OUT/shadowtrace-$SHA.tar.gz" "shadowtrace-$SHA"
( cd "$OUT" && sha256sum "shadowtrace-$SHA.tar.gz" > "shadowtrace-$SHA.tar.gz.sha256" )
echo "built $OUT/shadowtrace-$SHA.tar.gz"
