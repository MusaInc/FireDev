#!/usr/bin/env bash
# Browser checks for the site (run by `firedev run test`). Uses FireDev's Playwright install.
set -euo pipefail
cd "$(dirname "$0")"
: "${FIREDEV_ROOT:=$HOME/workspace/projects/FireDev}"
export NODE_PATH="$FIREDEV_ROOT/node_modules${NODE_PATH:+:$NODE_PATH}"
exec node tests/site.check.mjs
