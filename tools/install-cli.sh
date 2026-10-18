#!/usr/bin/env bash
# Puts `firedev` on every shell's PATH: a symlink in /usr/local/bin (sudo) to this checkout's wrapper.
# Pass a directory to install somewhere else, e.g. tools/install-cli.sh ~/.local/bin (no sudo).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
dest="${1:-/usr/local/bin}"
[ -x "$here/.venv/bin/python" ] || { echo "No virtualenv yet: run tools/setup.sh first." >&2; exit 1; }
if [ -w "$dest" ]; then ln -sfn "$here/firedev" "$dest/firedev"; else sudo ln -sfn "$here/firedev" "$dest/firedev"; fi
echo "Installed $dest/firedev -> $here/firedev"
