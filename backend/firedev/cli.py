"""firedev — command line for the FireDev service.

  firedev serve                     start the service (TV WebSocket, phone mic, MCP, Alexa)
  firedev pair <code> [--name N]    pair the TV showing <code>
  firedev devices                   list paired devices (TVs, phones, linked Alexa accounts)
  firedev unpair <device-id>        remove one, e.g. to unlink Alexa
  firedev add-assistant <name>      create a token for an MCP client
  firedev say "<words>"             send a voice command as if spoken
  firedev status [project]          print project status
  firedev ui show <file>|back       put a generated screen (firedev.ui/1 JSON) on the TV, or go back one
  firedev mcp                       MCP over stdio (bridges to the running service)
  firedev demo setup|reset          create or reset the broken sample project
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import config as cfg
from .auth import DeviceStore

ROOT = Path(__file__).resolve().parents[2]


def _base_url(c: cfg.Config) -> str:
    host = "127.0.0.1" if c.settings.host in ("0.0.0.0", "::") else c.settings.host
    return f"http://{host}:{c.settings.port}"


def _admin_token() -> str:
    path = cfg.DEFAULT_HOME / "admin.token"
    if not path.exists():
        sys.exit("firedev: no admin token yet. Start the service once with `firedev serve`.")
    return path.read_text().strip()


def _call(c: cfg.Config, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(_base_url(c) + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token or _admin_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail") or e.reason
        except Exception:  # noqa: BLE001
            detail = e.reason
        sys.exit(f"firedev: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"firedev: can't reach the FireDev service at {_base_url(c)} ({e.reason}). Is `firedev serve` running?")


def cmd_serve(args, c: cfg.Config) -> None:
    import uvicorn

    from .app import create_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("asyncssh").setLevel(logging.WARNING)  # one INFO line per SSH channel otherwise
    devices = DeviceStore(cfg.DEFAULT_HOME)
    host = args.host or c.settings.host
    port = args.port or c.settings.port
    print(f"FireDev on http://{host}:{port}  ·  {len(c.projects)} project(s)  ·  TV preview at /tv/")
    uvicorn.run(create_app(c, devices), host=host, port=port, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20)


def cmd_pair(args, c):
    r = _call(c, "POST", "/api/pair", {"code": args.code, "name": args.name})
    print(f"Paired {r['name']} ({r['device']}).")


def cmd_devices(args, c):
    for d in _call(c, "GET", "/api/devices")["devices"]:
        print(f"{d['id']}  {d['role']:<10} {d['name']}")


def cmd_unpair(args, c):
    _call(c, "DELETE", f"/api/devices/{args.device}")
    print(f"Removed {args.device}.")


def cmd_add_assistant(args, c):
    r = _call(c, "POST", "/api/devices", {"name": args.name, "role": args.role})
    print(r["token"])


def cmd_say(args, c):
    r = _call(c, "POST", "/api/command", {"name": "voice_command", "args": {"text": args.words, "project": args.project, "source": "cli"}})
    print(f"→ {r.get('intent', {}).get('title', '')}: {r.get('message', '')}")


def cmd_ui(args, c):
    if args.action == "back":
        r = _call(c, "POST", "/api/command", {"name": "ui_back", "args": {"project": args.project}})
        print("Back to the previous screen." if r.get("ok") else "There's no earlier screen.")
        return
    if not args.file:
        sys.exit("firedev: name the screen's JSON file (or - for stdin)")
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
    name = "ui_patch" if args.action == "patch" else "ui_show"
    body = {"project": args.project, "ops" if name == "ui_patch" else "surface": json.loads(raw)}
    try:
        r = _call(c, "POST", "/api/command", {"name": name, "args": body})
    except SystemExit:
        raise
    if not r.get("ok"):
        sys.exit("firedev: " + r.get("message", "refused") + "\n" + "\n".join("  - " + e for e in r.get("errors", [])))
    print(f"On the TV (version {r['version']}).")


def cmd_status(args, c):
    r = _call(c, "POST", "/api/command", {"name": "get_project_status", "args": {"project": args.project}})
    print(r.get("spoken"))


def cmd_mcp(args, c):
    token = args.token or os.environ.get("FIREDEV_TOKEN") or _admin_token()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        req = urllib.request.Request(_base_url(c) + "/mcp", method="POST", data=json.dumps(msg).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
        except urllib.error.URLError as e:
            if msg.get("id") is not None:
                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": f"FireDev unreachable: {e}"}}), flush=True)
            continue
        if body:
            print(body.decode(), flush=True)


DEMO_DIR = Path(os.environ.get("FIREDEV_DEMO_DIR", "~/firedev-demo")).expanduser()


def cmd_demo(args, c):
    target = DEMO_DIR / "pulse"
    src = ROOT / "demo" / "pulse-android"
    if args.action == "reset" and (DEMO_DIR / "preview" / "server.pid").exists():
        pid = (DEMO_DIR / "preview" / "server.pid").read_text().strip()
        subprocess.run(["kill", pid], capture_output=True)
        shutil.rmtree(DEMO_DIR / "preview", ignore_errors=True)
    if args.action == "reset":
        # Stop anything still running for the demo (agent turns, builds) so the next take starts clean.
        session = c.projects[args.project].tmux_session if args.project in c.projects else f"firedev-{args.project}"
        subprocess.run(["tmux", "kill-session", "-t", f"={session}"], capture_output=True)
    if target.exists():
        if args.action == "setup":
            sys.exit(f"{target} already exists. Use `firedev demo reset` to start it again from the broken state.")
        shutil.rmtree(target)
    shutil.copytree(src, target, ignore=shutil.ignore_patterns(".gradle", "build", ".firedev", "local.properties"))
    git = ["git", "-C", str(target), "-c", "user.name=Pulse Team", "-c", "user.email=team@pulse.example"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "Pulse 1.2.0: weekly summary on the home screen"], check=True)
    print(f"Broken sample ready at {target} (2 compile errors, 18 unit tests).")
    if args.action == "reset":
        try:
            _call(c, "POST", "/api/command", {"name": "reset_state", "args": {"project": args.project, "forget_thread": not args.keep_thread}})
            print("FireDev state for the project cleared.")
        except SystemExit:
            print("(FireDev isn't running; nothing else to reset.)")
    if not cfg.DEFAULT_CONFIG.exists():
        print(f"No config yet: copy {ROOT / 'config' / 'firedev.example.yaml'} to {cfg.DEFAULT_CONFIG} and edit it.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="firedev", description="FireDev: a voice-first development workstation for Fire TV")
    p.add_argument("--config", help="config file (default ~/.config/firedev/firedev.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--host"); s.add_argument("--port", type=int); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("pair"); s.add_argument("code"); s.add_argument("--name", default="Fire TV"); s.set_defaults(fn=cmd_pair)
    s = sub.add_parser("devices"); s.set_defaults(fn=cmd_devices)
    s = sub.add_parser("unpair"); s.add_argument("device"); s.set_defaults(fn=cmd_unpair)
    s = sub.add_parser("add-assistant"); s.add_argument("name"); s.add_argument("--role", default="assistant", choices=["assistant", "tv", "voice"]); s.set_defaults(fn=cmd_add_assistant)
    s = sub.add_parser("say"); s.add_argument("words"); s.add_argument("--project"); s.set_defaults(fn=cmd_say)
    s = sub.add_parser("status"); s.add_argument("project", nargs="?"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("ui"); s.add_argument("action", choices=["show", "patch", "back"]); s.add_argument("file", nargs="?")
    s.add_argument("--project"); s.set_defaults(fn=cmd_ui)
    s = sub.add_parser("mcp"); s.add_argument("--token"); s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("demo"); s.add_argument("action", choices=["setup", "reset"]); s.add_argument("--project", default="pulse")
    s.add_argument("--keep-thread", action="store_true", help="keep the agent's conversation"); s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    if args.cmd == "demo" and not (Path(args.config).expanduser() if args.config else cfg.DEFAULT_CONFIG).exists():
        c = cfg.parse({})
    else:
        try:
            c = cfg.load(args.config)
        except cfg.ConfigError as e:
            sys.exit(f"firedev: {e}")
    args.fn(args, c)


if __name__ == "__main__":
    main()
