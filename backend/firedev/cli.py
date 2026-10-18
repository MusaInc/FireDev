"""firedev — command line for the FireDev service.

  firedev init [--lan|--no-lan]     first-time setup on this computer: config, this machine, LAN mode
  firedev add [path] [--agent A]    add a project (default: the current folder) on this machine
  firedev serve                     start the service (TV WebSocket, phone mic, MCP, Alexa)
  firedev up [--json]               start the service in the background if it isn't running; print how to reach it
  firedev pair <code> [--name N]    pair the TV showing <code>
  firedev pair --issue [--name N]   a TV token for a TV that logged in over SSH (used by the Fire TV app)
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
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import config as cfg
from .auth import DeviceStore

ROOT = Path(__file__).resolve().parents[2]
from shlex import quote as shlex_quote  # noqa: E402


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
    if args.issue:
        # The Fire TV app logged in over SSH as this machine's user, which is the proof pairing is for.
        r = _call(c, "POST", "/api/devices", {"name": args.name, "role": "tv"})
        print(json.dumps({"token": r["token"], "device": r["device"]["id"], "port": c.settings.port,
                          "preview_port": c.settings.preview_port}) if args.json else r["token"])
        return
    if not args.code:
        sys.exit("firedev: give the code the TV shows, e.g. firedev pair 123456")
    r = _call(c, "POST", "/api/pair", {"code": args.code, "name": args.name})
    print(f"Paired {r['name']} ({r['device']}).")


def _health(c: cfg.Config) -> dict | None:
    for path in ("/api/info", "/api/health"):  # an older FireDev has no /api/info
        try:
            with urllib.request.urlopen(_base_url(c) + path, timeout=3) as r:
                data = json.loads(r.read())
                return data if path == "/api/info" else {"name": os.uname().nodename, "lan": False, **data}
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def cmd_up(args, c):
    """Makes sure the service runs (in a tmux session firedev-service-<port>, or detached with a log)."""
    import time as _time
    info = _health(c)
    started = False
    if not info:
        log = cfg.DEFAULT_HOME / "service.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        exe = [sys.executable, "-m", "firedev"] + (["--config", args.config] if args.config else []) + ["serve"]
        env = dict(os.environ, PYTHONPATH=str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""))
        if shutil.which("tmux"):
            # A running tmux server keeps its own environment: pass FireDev's settings on the command line.
            keep = {k: env[k] for k in ("FIREDEV_HOME", "FIREDEV_CONFIG", "PYTHONPATH") if env.get(k)}
            cmd = "env " + " ".join(f"{k}={shlex_quote(v)}" for k, v in keep.items()) + " " + \
                " ".join(shlex_quote(x) for x in exe) + f" >> {shlex_quote(str(log))} 2>&1"
            subprocess.run(["tmux", "new-session", "-d", "-s", f"firedev-service-{c.settings.port}", f"cd {shlex_quote(str(ROOT))} && " + cmd],
                           env=env, check=False)
        else:
            subprocess.Popen(exe, stdout=log.open("a"), stderr=subprocess.STDOUT, start_new_session=True, env=env, cwd=str(ROOT))
        started = True
        for _ in range(60):
            _time.sleep(0.5)
            info = _health(c)
            if info:
                break
    if not info:
        sys.exit(f"firedev: the service didn't start; see {cfg.DEFAULT_HOME / 'service.log'}")
    out = {"ok": True, "started": started, "port": c.settings.port, "preview_port": c.settings.preview_port, **info}
    if args.json:
        print(json.dumps(out))
    else:
        print(f"FireDev is running on {info['name']} (port {c.settings.port}{', LAN on' if info.get('lan') else ''}).")


def cmd_init(args, c):
    """First run on a computer: a config with this machine (no SSH needed) and LAN mode for TVs on the same Wi-Fi."""
    path = Path(args.config).expanduser() if args.config else cfg.DEFAULT_CONFIG
    if path.exists() and not args.force:
        print(f"{path} already exists. Add projects with `firedev add <folder>`; `firedev init --force` starts over.")
        return
    import yaml
    raw = {"server": {"host": "127.0.0.1", "port": 8130, "lan": not args.no_lan},
           "machines": {"this-computer": {"local": True}}, "projects": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# FireDev on this computer. `firedev add <folder>` adds projects; `firedev serve` starts it.\n"
                    + yaml.safe_dump(raw, sort_keys=False))
    print(f"Wrote {path}.")
    print("Next: `firedev add ~/code/my-app`, then `firedev serve`, then open FireDev on your TV" +
          (" on the same Wi-Fi: it finds this computer by itself." if not args.no_lan else "."))


def cmd_add(args, c):
    import yaml
    path = Path(args.config).expanduser() if args.config else cfg.DEFAULT_CONFIG
    if not path.exists():
        sys.exit("firedev: no config yet. Run `firedev init` first.")
    folder = Path(args.path or ".").expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"firedev: {folder} isn't a folder")
    raw = yaml.safe_load(path.read_text()) or {}
    projects = raw.setdefault("projects", [])
    pid = re.sub(r"[^a-z0-9-]+", "-", (args.id or folder.name).lower()).strip("-")[:40] or "project"
    if any(p.get("id") == pid for p in projects):
        sys.exit(f"firedev: there is already a project called {pid}; use --id")
    machine = args.machine or next((m for m, v in (raw.get("machines") or {}).items() if (v or {}).get("local")), "local")
    home = str(Path.home())
    shown = "~" + str(folder)[len(home):] if str(folder).startswith(home + "/") else str(folder)
    projects.append({"id": pid, "name": args.name or folder.name.replace("-", " ").replace("_", " ").title(), "machine": machine,
                     "path": shown, "agent": args.agent})
    cfg.parse(raw)  # refuses anything invalid before writing
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    print(f"Added {pid} ({shown}, {args.agent}). Restart `firedev serve` to see it on the TV.")


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
    s = sub.add_parser("init"); s.add_argument("--no-lan", action="store_true", help="don't listen on the local network")
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("add"); s.add_argument("path", nargs="?"); s.add_argument("--agent", default="codex", choices=sorted(cfg.AGENTS))
    s.add_argument("--id"); s.add_argument("--name"); s.add_argument("--machine"); s.set_defaults(fn=cmd_add)
    s = sub.add_parser("serve"); s.add_argument("--host"); s.add_argument("--port", type=int); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("up"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_up)
    s = sub.add_parser("pair"); s.add_argument("code", nargs="?"); s.add_argument("--name", default="Fire TV")
    s.add_argument("--issue", action="store_true", help="print a new TV token (for a TV that logged in over SSH)")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_pair)
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
    if args.cmd in ("demo", "init") and not (Path(args.config).expanduser() if args.config else cfg.DEFAULT_CONFIG).exists():
        c = cfg.parse({})
    else:
        try:
            c = cfg.load(args.config)
        except cfg.ConfigError as e:
            sys.exit(f"firedev: {e}")
    args.fn(args, c)


if __name__ == "__main__":
    main()
