#!/usr/bin/env bash
# One-time setup on the machine that runs the FireDev service: Python venv, TV interface bundle, config file.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -q -r backend/requirements.txt
if command -v npm >/dev/null; then npm install --no-audit --no-fund --silent && npm run build --silent; fi
mkdir -p ~/.config/firedev && chmod 700 ~/.config/firedev
if [ ! -f ~/.config/firedev/firedev.yaml ]; then
  # Local-first: this computer is the machine (no SSH to itself), and TVs on the same Wi-Fi find it.
  ./firedev init
  chmod 600 ~/.config/firedev/firedev.yaml
fi
echo "FireDev is set up. Next: ./firedev add ~/code/my-app, then ./firedev serve"
