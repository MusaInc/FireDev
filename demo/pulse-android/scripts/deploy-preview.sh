#!/usr/bin/env bash
# Publishes the latest debug APK to the Pulse preview server and restarts it.
# Preview root: $PULSE_PREVIEW_DIR (default ~/firedev-demo/preview), port: $PULSE_PREVIEW_PORT (default 8131).
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="${PULSE_PREVIEW_DIR:-$HOME/firedev-demo/preview}"
PORT="${PULSE_PREVIEW_PORT:-8131}"
APK=app/build/outputs/apk/debug/app-debug.apk

[ -f "$APK" ] || { echo "No APK at $APK. Build the app first."; exit 1; }
mkdir -p "$ROOT/www"

VERSION=$(sed -n 's/.*versionName = "\(.*\)".*/\1/p' app/build.gradle.kts | head -1)
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo local)
SHA=$(sha256sum "$APK" | cut -c1-12)
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

echo "Uploading Pulse $VERSION ($SHA) to preview"
cp "$APK" "$ROOT/www/pulse-$VERSION.apk"
cat > "$ROOT/www/index.html" <<HTML
<!doctype html><meta charset="utf-8"><title>Pulse preview</title>
<body style="font-family:system-ui;background:#0b0c0e;color:#f4f1ea;padding:48px">
<h1>Pulse $VERSION</h1><p>Preview build $SHA from $COMMIT, deployed $STAMP.</p>
<p><a style="color:#f4f1ea" href="pulse-$VERSION.apk">Download APK</a></p>
HTML
echo "{\"version\":\"$VERSION\",\"sha\":\"$SHA\",\"commit\":\"$COMMIT\",\"deployed\":\"$STAMP\"}" > "$ROOT/www/health.json"

echo "Restarting preview server on port $PORT"
if [ -f "$ROOT/server.pid" ] && kill -0 "$(cat "$ROOT/server.pid")" 2>/dev/null; then
  kill "$(cat "$ROOT/server.pid")" || true
  sleep 0.5
fi
nohup python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$ROOT/www" >> "$ROOT/server.log" 2>&1 &
echo $! > "$ROOT/server.pid"

for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/health.json" 2>/dev/null | grep -q "\"$SHA\""; then
    echo "Health check passed: preview is serving $SHA"
    echo "Preview live at http://127.0.0.1:$PORT/"
    exit 0
  fi
  sleep 0.25
done
echo "Health check failed: preview did not come up"
exit 1
