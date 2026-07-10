#!/usr/bin/env bash
# Deploy the CartoSky frontend without breaking tabs that hold the previous build.
#
# Builds into dist-staging, then layers the result onto the live dist/ so old
# hashed chunks stay servable for a week. Copy order matters: new chunks land
# before the index.html that references them, so nginx never serves an
# index/chunk mismatch mid-deploy.
#
# Usage (on the server, after git pull):
#   ./deployment/deploy_frontend.sh

set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../frontend" && pwd)"
cd "$FRONTEND_DIR"

# npm ci only when the lockfile changed since the last install.
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  echo "==> npm ci (lockfile changed or node_modules missing)"
  npm ci
else
  echo "==> npm ci skipped (lockfile unchanged)"
fi

echo "==> building to dist-staging"
rm -rf dist-staging
npx vite build --outDir dist-staging --emptyOutDir

if [ ! -d dist ]; then
  echo "==> first deploy: moving dist-staging into place"
  mv dist-staging dist
  echo "==> done"
  exit 0
fi

echo "==> layering new build onto dist (old hashed chunks kept)"
mkdir -p dist/assets
cp -a dist-staging/assets/. dist/assets/
rsync -a --exclude=assets --exclude=index.html dist-staging/ dist/
cp dist-staging/index.html dist/index.html
rm -rf dist-staging

echo "==> pruning hashed assets older than 7 days"
find dist/assets -type f -mtime +7 -print -delete

echo "==> done"
