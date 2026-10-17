"""Starts a throwaway FireDev service for the browser tests: a fixture project on localhost over real SSH and tmux,
the scripted stand-in agent (backend/tests/fixtures/fake-codex), and tokens written to <dir>/tokens.json."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = Path(os.environ.get("FIREDEV_E2E_DIR", ROOT / "e2e" / ".tmp"))
PORT = int(os.environ.get("FIREDEV_E2E_PORT", "8139"))

shutil.rmtree(DIR, ignore_errors=True)
proj = DIR / "proj"
(proj / "src").mkdir(parents=True)
(proj / "src" / "app.txt").write_text("broken\n")
(proj / "README.md").write_text("Fixture\n" + "".join(f"line {i}\n" for i in range(60)))
scripts = {
    "test.sh": """\
        mkdir -p reports
        if grep -q fixed src/app.txt; then f=""; code=0; else f='<failure message="app is broken"/>'; code=1; fi
        printf '<testsuite name="t"><testcase classname="app.AppTest" name="a"/><testcase classname="app.AppTest" name="b"/><testcase classname="app.AppTest" name="c">%s</testcase></testsuite>' "$f" > reports/TEST-app.xml
        echo "ran 3 tests"; exit $code""",
    "build.sh": """\
        if grep -q fixed src/app.txt; then echo "BUILD SUCCESSFUL in 2s"; mkdir -p out; head -c 2400000 /dev/zero > out/app.apk; exit 0; fi
        echo "e: file://$PWD/src/Main.kt:3:5 Unresolved reference 'x'."; echo "BUILD FAILED in 1s"; exit 1""",
    "deploy.sh": "echo Uploading; sleep 0.5; echo 'Health check passed: preview is serving abc123'; echo 'Preview live at http://127.0.0.1:9/'",
}
for name, body in scripts.items():
    (proj / name).write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body) + "\n")
    (proj / name).chmod(0o755)
(proj / ".gitignore").write_text("reports/\nout/\n")
g = ["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@t"]
subprocess.run(["git", "init", "-q", str(proj)], check=True)
subprocess.run([*g, "add", "-A"], check=True)
subprocess.run([*g, "commit", "-qm", "init"], check=True)

home = DIR / "home"
cfg = {
    "server": {"host": "127.0.0.1", "port": PORT, "data_dir": str(DIR / "data")},
    "machines": {"local": {"label": "test-box", "host": "localhost", "user": os.environ.get("USER", "abdul"), "auth": "key",
                           "key_file": "~/.config/firedev/keys/firedev_ed25519"}},
    "projects": [{"id": "e2e", "name": "Fixture App", "machine": "local", "path": str(proj), "agent": "codex",
                  "tmux_session": f"firedev-e2e-{PORT}", "agent_options": {"binary": str(ROOT / "backend/tests/fixtures/fake-codex")},
                  "actions": {
                      "test": {"run": "./test.sh", "kind": "tests", "reports": "reports/*.xml", "title": "Run the tests"},
                      "build": {"run": "./build.sh", "kind": "build", "artifact": "out/app.apk", "title": "Build the app"},
                      "deploy_preview": {"run": "./deploy.sh", "kind": "deploy", "level": 2, "title": "Deploy the preview",
                                         "reason": "Publish the latest build to the preview server.", "risk": "Testers get the new build."},
                      "wipe": {"run": "rm -rf out reports", "kind": "command", "level": 3, "title": "Wipe build outputs",
                               "reason": "Start from a clean slate.", "risk": "Deletes every build output."},
                  }}],
}
DIR.mkdir(parents=True, exist_ok=True)
import yaml  # noqa: E402

(DIR / "firedev.yaml").write_text(yaml.safe_dump(cfg))
os.environ["FIREDEV_HOME"] = str(home)
os.environ["FIREDEV_CONFIG"] = str(DIR / "firedev.yaml")
subprocess.run(["tmux", "kill-session", "-t", f"=firedev-e2e-{PORT}"], capture_output=True)
sys.path.insert(0, str(ROOT / "backend"))

from firedev import config  # noqa: E402
from firedev.app import create_app  # noqa: E402
from firedev.auth import DeviceStore  # noqa: E402

devices = DeviceStore(home)
tv, _ = devices.create("E2E TV", "tv")
voice, _ = devices.create("E2E phone", "voice")
(DIR / "tokens.json").write_text(json.dumps({"tv": tv, "voice": voice, "admin": (home / "admin.token").read_text().strip()}))

import uvicorn  # noqa: E402

uvicorn.run(create_app(config.load(DIR / "firedev.yaml"), devices), host="127.0.0.1", port=PORT, log_level="warning")
