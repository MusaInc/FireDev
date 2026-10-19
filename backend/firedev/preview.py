"""Live previews: the project's real running web app, shown on the TV while the agent builds it.

- `detect()` reads a project over SSH: which framework it uses (Vite, Next.js, Expo web, a static site...), which dev
  servers its own processes already listen on, and whether it is a native app that a WebView can't run.
- `PreviewManager` keeps one `Preview` per project. It starts FireDev-managed dev servers in the project's tmux
  session (window "preview", so they outlive the TV, the network and FireDev restarts), adopts servers that are
  already running, sends reload hints for servers without hot reload, answers the agent's `firedev preview` requests
  and offers the preview when the agent changes something visual.
- The TV never talks to the dev server. It gets a one-time ticket and opens the preview on the gateway
  (`gateway.py`), a separate origin that proxies HTTP and WebSockets to 127.0.0.1:<port> on the project's machine
  through the project's SSH connection. localhost on the TV is not localhost on the dev machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
import secrets
import shlex
import time
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

from .ssh import RemoteError

if TYPE_CHECKING:
    from .auth import Client
    from .service import FireDev
    from .session import ProjectSession

log = logging.getLogger("firedev.preview")

TICKET_TTL = 90.0
SESSION_IDLE = 12 * 3600.0
START_TIMEOUT = 120.0
STYLES_GRACE = 90  # seconds a live build waits for the page's own stylesheets before showing it anyway
INSTALL_TIMEOUT = 900.0
PORT_BASE, PORT_SPAN = 5300, 600
WEB_FILES = re.compile(r"\.(html?|css|scss|sass|less|jsx?|tsx?|mjs|cjs|vue|svelte|astro|mdx?|json|svg|png|jpe?g|webp|gif|ico)$", re.I)
VISUAL_WORDS = re.compile(r"\b(page|site|website|web ?app|landing|ui|interface|screen|layout|design|style|css|header|footer|hero|"
                          r"button|menu|nav(bar|igation)?|form|card|colou?rs?|fonts?|theme|dark mode|responsive|mobile|component|"
                          r"dashboard|logo|image|banner|section|animation|frontend|front end)\b", re.I)
BUILD_WORDS = re.compile(r"\b(build|create|make|design|add|change|update|redesign|restyle|improve|fix|move|resize|"
                         r"bigger|smaller|show)\b", re.I)

# Dev server commands. {bin} is node_modules/.bin, {port} the port FireDev chose. Each binds 127.0.0.1 only: the
# gateway reaches it through SSH, so nothing is exposed on the machine's network.
FRAMEWORKS = {
    "next": ("Next.js", "{bin}/next dev -H 127.0.0.1 -p {port}", True),
    "nuxt": ("Nuxt", "{bin}/nuxi dev --host 127.0.0.1 --port {port}", True),
    "sveltekit": ("SvelteKit", "{bin}/vite dev --host 127.0.0.1 --port {port} --strictPort", True),
    "astro": ("Astro", "{bin}/astro dev --host 127.0.0.1 --port {port}", True),
    "remix": ("Remix", "{bin}/vite dev --host 127.0.0.1 --port {port} --strictPort", True),
    "expo": ("Expo (web)", "{bin}/expo start --web --port {port}", True),
    "angular": ("Angular", "{bin}/ng serve --host 127.0.0.1 --port {port}", True),
    "cra": ("Create React App", "HOST=127.0.0.1 PORT={port} BROWSER=none {bin}/react-scripts start", True),
    "vue-cli": ("Vue CLI", "{bin}/vue-cli-service serve --host 127.0.0.1 --port {port}", True),
    "gatsby": ("Gatsby", "{bin}/gatsby develop -H 127.0.0.1 -p {port}", True),
    "vite": ("Vite", "{bin}/vite --host 127.0.0.1 --port {port} --strictPort", True),
    "parcel": ("Parcel", "{bin}/parcel {entry} --host 127.0.0.1 --port {port}", True),
    "webpack": ("webpack", "{bin}/webpack serve --host 127.0.0.1 --port {port}", True),
    "script": ("npm script", "HOST=127.0.0.1 PORT={port} BROWSER=none {pm} run {script}", False),
    "static": ("Static site", "python3 -m http.server {port} --bind 127.0.0.1", False),
}
# Checked in order: the first dependency found decides.
DEP_ORDER = [("next", "next"), ("nuxt", "nuxt"), ("@sveltejs/kit", "sveltekit"), ("astro", "astro"), ("@remix-run/dev", "remix"),
             ("expo", "expo"), ("@angular/cli", "angular"), ("react-scripts", "cra"), ("@vue/cli-service", "vue-cli"),
             ("gatsby", "gatsby"), ("vite", "vite"), ("parcel", "parcel"), ("webpack-dev-server", "webpack")]
WEB_DIRS = ["", "web", "frontend", "client", "site", "apps/web", "packages/web", "www"]
STATIC_DIRS = ["", "public", "site", "www", "docs", "dist", "web", "src"]


@dataclass
class Preview:
    pid: str
    status: str = "unknown"  # unknown | detecting | idle | starting | running | failed | native | none
    kind: str | None = None  # web | static | native
    framework: str | None = None
    label: str = ""
    dir: str = ""  # relative to the project
    port: int | None = None  # on the project's machine
    path: str = "/"
    hmr: bool = False
    managed: bool = False  # FireDev started it (window "preview")
    command: str | None = None
    needs_install: bool = False
    install: str | None = None  # the install command, when dependencies are missing
    message: str = ""
    log: list = field(default_factory=list)
    servers: list = field(default_factory=list)  # project processes listening on ports
    native: dict | None = None
    alternatives: list = field(default_factory=list)
    screenshots: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # console errors the TV saw in the page
    page: str | None = None  # the page the TV shows now
    title: str | None = None
    viewers: dict = field(default_factory=dict)  # TV device id -> {layout, viewport, ts}
    detected: float = 0.0
    updated: float = field(default_factory=time.time)
    started: float | None = None

    def to_dict(self) -> dict:
        return {"project": self.pid, "status": self.status, "kind": self.kind, "framework": self.framework, "label": self.label,
                "dir": self.dir, "port": self.port, "path": self.path, "hmr": self.hmr, "managed": self.managed,
                "command": self.command, "needs_install": self.needs_install, "message": self.message, "log": self.log[-14:],
                "servers": self.servers, "native": self.native, "alternatives": self.alternatives,
                "screenshots": [{"i": i, "name": posixpath.basename(p), "path": p} for i, p in enumerate(self.screenshots)],
                "errors": self.errors[-6:], "page": self.page, "title": self.title, "watching": bool(self.viewers),
                "updated": self.updated}


# --- detection --------------------------------------------------------------------------------------------

def _detect_script(path: str, state_dir: str, tmux: str) -> str:
    q = shlex.quote
    pkgs = " ".join(q(f"{d}/package.json" if d else "package.json") for d in WEB_DIRS)
    markers = ("index.html public/index.html site/index.html www/index.html docs/index.html dist/index.html web/index.html src/index.html "
               "build.gradle build.gradle.kts settings.gradle settings.gradle.kts app/src/main/AndroidManifest.xml "
               "src/main/AndroidManifest.xml pubspec.yaml web/index.html ios Podfile app.json node_modules web/node_modules "
               "frontend/node_modules client/node_modules site/node_modules apps/web/node_modules "
               "pnpm-lock.yaml yarn.lock bun.lockb bun.lock package-lock.json")
    return f"""
P={q(path)}; S={q(state_dir)}
cd "$P" || exit 3
for f in {pkgs}; do [ -f "$f" ] && {{ echo "@@pkg $f"; head -c 60000 "$f"; echo; }}; done
echo "@@markers"; for m in {markers}; do [ -e "$m" ] && echo "$m"; done; ls -d *.xcodeproj 2>/dev/null
echo "@@shots"; find . -maxdepth 7 \\( -path ./node_modules -o -path ./.git -o -path ./build/intermediates \\) -prune -o -type f \\
  \\( -path '*screenshot*' -o -path '*snapshots*' -o -path '*roborazzi*' -o -path '*paparazzi*' -o -path '*/metadata/android/*/images/*' \\) \\
  \\( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.webp' \\) -print 2>/dev/null | head -24
echo "@@ports"
if command -v ss >/dev/null 2>&1; then
  ss -ltnpH 2>/dev/null | while read -r st rq sq local peer procs; do
    port=${{local##*:}}
    for pid in $(printf '%s' "$procs" | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u); do
      cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
      case "$cwd" in "$P"|"$P"/*) printf '%s\\t%s\\t%s\\t%s\\n' "$port" "$pid" "$cwd" "$(tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | head -c 160)";; esac
    done
  done
  echo "@@all"; ss -ltnH 2>/dev/null | awk '{{print $4}}' | sed 's/.*://' | sort -un | tr '\\n' ' '; echo
elif command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP -sTCP:LISTEN -Fpn 2>/dev/null | awk '/^p/{{pid=substr($0,2)}} /^n/{{n=$0; sub(/.*:/,"",n); print n"\\t"pid}}' | sort -u | while IFS="$(printf '\\t')" read -r port pid; do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    case "$cwd" in "$P"|"$P"/*) printf '%s\\t%s\\t%s\\t%s\\n' "$port" "$pid" "$cwd" "$(ps -o command= -p "$pid" | head -c 160)";; esac
  done
  echo "@@all"; lsof -nP -iTCP -sTCP:LISTEN -Fn 2>/dev/null | sed -n 's/^n.*://p' | sort -un | tr '\\n' ' '; echo
fi
echo "@@meta"; cat "$S/preview/meta.json" 2>/dev/null; echo
echo "@@window"; tmux list-windows -t ={q(tmux)} -F '#{{window_name}}' 2>/dev/null | grep -qx preview && echo running
echo "@@log"; [ -f "$S/preview/meta.json" ] && tail -n 30 "$S/preview/out.log" 2>/dev/null
echo "@@end"
"""


def parse_detect(out: str) -> dict:
    """Splits the detection script's output into its sections (pure, for tests)."""
    res: dict = {"packages": {}, "markers": set(), "shots": [], "ports": [], "all_ports": set(), "meta": None, "window": False, "log": []}
    section, cur_pkg, buf = None, None, []

    def flush():
        if section == "pkg" and cur_pkg:
            try:
                res["packages"][cur_pkg] = json.loads("\n".join(buf))
            except ValueError:
                res["packages"][cur_pkg] = {}

    for line in out.splitlines():
        if line.startswith("@@"):
            flush()
            buf = []
            head, _, rest = line[2:].partition(" ")
            section, cur_pkg = head, rest.strip() or None
            continue
        if section == "pkg":
            buf.append(line)
        elif section == "markers" and line.strip():
            res["markers"].add(line.strip().rstrip("/"))
        elif section == "shots" and line.strip():
            res["shots"].append(line.strip()[2:] if line.startswith("./") else line.strip())
        elif section == "ports" and "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].isdigit():
                res["ports"].append({"port": int(parts[0]), "pid": int(parts[1]) if parts[1].isdigit() else None,
                                     "cwd": parts[2], "cmd": (parts[3] if len(parts) > 3 else "").strip()})
        elif section == "all":
            res["all_ports"] |= {int(x) for x in line.split() if x.isdigit()}
        elif section == "meta" and line.strip():
            try:
                res["meta"] = json.loads(line)
            except ValueError:
                pass
        elif section == "window" and line.strip() == "running":
            res["window"] = True
        elif section == "log" and line.strip():
            res["log"].append(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", line)[:300])
    flush()
    return res


def plan(raw: dict, path: str, override: dict | None = None) -> dict:
    """What can be previewed and how (pure). Returns {kind, framework, label, dir, command, hmr, needs_install,
    native, alternatives, message, servers}."""
    o = override or {}
    servers = [s for s in raw["ports"]]
    seen = set()
    servers = [s for s in servers if not (s["port"] in seen or seen.add(s["port"]))]
    markers = raw["markers"]
    out = {"kind": None, "framework": None, "label": "", "dir": "", "command": None, "hmr": False, "needs_install": False,
           "native": None, "alternatives": [], "message": "", "servers": servers}
    if o.get("command"):
        d = str(o.get("dir") or "").strip("/")
        out.update(kind="web", framework=o.get("framework") or "custom", label=o.get("label") or "Dev server", dir=d,
                   command=str(o["command"]), hmr=bool(o.get("hmr", True)))
        return out
    pkgs = raw["packages"]
    for d in WEB_DIRS:
        key = f"{d}/package.json" if d else "package.json"
        pkg = pkgs.get(key)
        if pkg is None:
            continue
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        scripts = pkg.get("scripts") or {}
        fw = next((f for dep, f in DEP_ORDER if dep in deps), None)
        if fw == "expo" and not ("react-native-web" in deps or "react-dom" in deps):
            out["native"] = {"platform": "React Native (Expo)", "why": "This Expo app has no web target (react-native-web isn't installed)."}
            out["alternatives"].append({"op": "agent", "label": "Ask the agent to add Expo web support",
                                        "text": "Add web support to this Expo app (react-native-web and react-dom) so FireDev can preview it."})
            continue
        if not fw and "react-native" in deps:
            out["native"] = {"platform": "React Native", "why": "React Native apps run on a phone or emulator, not in a web page."}
            continue
        script = next((s for s in ("dev", "start", "serve") if s in scripts), None)
        if not fw and not script:
            continue
        pm = "pnpm" if "pnpm-lock.yaml" in markers else "yarn" if "yarn.lock" in markers else "bun" if ("bun.lockb" in markers or "bun.lock" in markers) else "npm"
        nm = f"{d}/node_modules" if d else "node_modules"
        fw = fw or "script"
        label, template, hmr = FRAMEWORKS[fw]
        entry = "index.html" if f"{d + '/' if d else ''}index.html" in markers or not d else "index.html"
        cmd = template.format(bin="./node_modules/.bin", port="{port}", pm=pm, script=script or "dev", entry=entry)
        out.update(kind="web", framework=fw, label=label, dir=d, command=cmd, hmr=hmr, needs_install=nm not in markers)
        if out["needs_install"]:
            out["install"] = f"{pm} install"
        return out
    native = out["native"]
    if not native:
        if ("build.gradle" in markers or "build.gradle.kts" in markers) and any("AndroidManifest" in m for m in markers):
            native = {"platform": "Android", "why": "It's a native Android app: it runs on a device, not in a web page."}
        elif "pubspec.yaml" in markers:
            native = {"platform": "Flutter", "why": "Flutter apps run on a device unless they have a web target."}
        elif any(m.endswith(".xcodeproj") for m in markers) or "Podfile" in markers:
            native = {"platform": "iOS", "why": "iOS apps run on an iPhone or the iOS Simulator, which needs a Mac."}
    static_dir = next((d for d in STATIC_DIRS if (f"{d}/index.html" if d else "index.html") in markers), None)
    if native and native["platform"] == "Flutter" and "web/index.html" in markers:
        out["alternatives"].append({"op": "start", "label": "Run the Flutter web build", "framework": "flutter"})
    if static_dir is not None:
        if native:
            out["alternatives"].append({"op": "start", "label": f"Show {static_dir or 'the project'}/index.html as a web page",
                                        "dir": static_dir})
        else:
            label, template, hmr = FRAMEWORKS["static"]
            out.update(kind="static", framework="static", label=label, dir=static_dir, command=template, hmr=False)
            return out
    if native:
        out.update(kind="native", native=native)
        if raw["shots"]:
            out["alternatives"].append({"op": "screenshots", "label": f"Show {len(raw['shots'])} screenshot image(s) from the project"})
        out["alternatives"].append({"op": "build", "label": "Build the app instead"})
        out["message"] = (f"{native['why']} FireDev's preview only runs real web pages, so it won't pretend to run this one.")
        return out
    if servers:
        out.update(kind="web", framework="running", label="Running server")
        return out
    out["message"] = "There's no web page in this project yet. Ask the agent to build one and FireDev will show it."
    return out


def _port_for(pid: str, busy: set[int], preferred: int | None = None) -> int:
    if preferred and preferred not in busy:
        return preferred
    start = PORT_BASE + zlib.crc32(pid.encode()) % PORT_SPAN
    for i in range(PORT_SPAN):
        p = PORT_BASE + (start - PORT_BASE + i) % PORT_SPAN
        if p not in busy:
            return p
    raise RemoteError("no free port for the preview")


def _diagnose(lines: list[str]) -> str | None:
    text = "\n".join(lines[-60:])
    checks = [
        (r"python3: (command )?not found", "python3 isn't installed on the machine, so FireDev can't serve the static site."),
        (r"EADDRINUSE|address already in use|Port \d+ is (already )?in use", "The port is already in use by another program."),
        (r"Cannot find module|MODULE_NOT_FOUND|\b(vite|next|nuxi|astro|react-scripts|ng|webpack|parcel|expo|gatsby|vue-cli-service)\b:? "
         r"(command )?not found|node_modules/\.bin/\S+: No such file|No such file or directory.*node_modules",
         "The project's dependencies aren't installed. Say “install the dependencies”."),
        (r"SyntaxError|Unexpected token|Failed to compile|error TS\d+|Build failed|ERROR in", "The app has an error, so the server couldn't start. The log is below."),
        (r"EACCES|permission denied", "The server wasn't allowed to start (permission denied)."),
    ]
    for pat, msg in checks:
        if re.search(pat, text, re.I):
            return msg
    return None


# --- the manager --------------------------------------------------------------------------------------------

@dataclass
class Ticket:
    device: str
    pid: str
    to: str
    expires: float


@dataclass
class ViewSession:
    id: str
    device: str
    pid: str
    created: float = field(default_factory=time.time)
    seen: float = field(default_factory=time.time)


class PreviewManager:
    def __init__(self, service: "FireDev") -> None:
        self.service = service
        self.previews: dict[str, Preview] = {pid: Preview(pid) for pid in service.sessions}
        self.tickets: dict[str, Ticket] = {}
        self.view_sessions: dict[str, ViewSession] = {}
        self.tunnels = None  # set by the gateway (it owns the SSH port forwards)
        self._locks: dict[str, asyncio.Lock] = {pid: asyncio.Lock() for pid in service.sessions}
        self._starting: dict[str, asyncio.Task] = {}
        self._watchers: dict[str, asyncio.Task] = {}
        self._reload_at: dict[str, asyncio.TimerHandle] = {}
        self._touched: dict[str, set[str]] = {}
        self._before: dict[str, dict] = {}
        self._live: dict[str, str] = {}  # project -> the agent task whose build is already live on the TV
        self._builds: dict[str, asyncio.Task] = {}  # project -> the file watcher of its running agent task
        self._reload_files: dict[str, list] = {}  # project -> the files saved since the pending reload was scheduled
        self._task: asyncio.Task | None = None

    # --- lifecycle ---------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._listen())

    async def stop(self) -> None:
        for t in [self._task, *self._starting.values(), *self._watchers.values(), *self._builds.values()]:
            if t:
                t.cancel()

    def settings(self, pid: str) -> dict:
        return dict(self.service.config.projects[pid].preview or {})

    def _emit(self, p: Preview) -> None:
        p.updated = time.time()
        self.service.bus.emit("preview_state", p.pid, None, preview=p.to_dict())

    def snapshot(self) -> dict:
        return {pid: p.to_dict() for pid, p in self.previews.items()}

    def get(self, pid: str) -> Preview:
        if pid not in self.previews:
            raise KeyError(pid)
        return self.previews[pid]

    def context(self, pid: str) -> dict:
        """What the voice parser needs to know about this project's preview."""
        p = self.previews.get(pid)
        if not p:
            return {}
        return {"preview": p.status if p.status != "unknown" else None, "preview_visible": bool(p.viewers),
                "preview_kind": p.kind, "preview_needs_install": p.needs_install}

    def gateway_base(self, client_base: str | None, tv_base: str | None = None) -> str | None:
        """Where this TV opens previews. The Fire TV app says itself (its own 127.0.0.1 relay or SSH forward)."""
        st = self.service.config.settings
        t = urlparse(tv_base or "")
        if t.scheme in ("http", "https") and t.hostname and not t.path.strip("/"):
            return f"{t.scheme}://{t.netloc}"
        if st.preview_url:
            return st.preview_url
        u = urlparse(client_base or "")
        if u.scheme in ("http", "https") and u.hostname:
            host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
            return f"{u.scheme}://{host}:{st.preview_port}"
        return None

    def spoken(self, p: Preview, showing: bool = False) -> str:
        s = self.service.sessions[p.pid]
        if p.status == "running":
            return "Here's the live preview." if showing else f"The preview is running: {p.label}, on {s.project.machine.display}."
        if p.status == "starting":
            return f"Starting the {p.label} dev server. It'll appear in a moment." if not p.message.startswith("Waiting") else \
                "Approve starting the dev server on the TV."
        if p.status == "idle" and p.needs_install:
            return "The project's dependencies aren't installed yet. Say “install the dependencies” and I'll start it."
        if p.status == "idle":
            return f"The {p.label} preview isn't running. Say “start the dev server”."
        if p.status == "failed":
            return f"The preview couldn't start. {p.message}"
        if p.status == "native":
            alt = p.alternatives[0]["label"].lower() if p.alternatives else ""
            return f"{p.native['why']} I can't show it as a web page." + (f" I can {alt}." if alt and not alt.startswith("ask") else "")
        return p.message or "There's no web preview for this project yet."

    # --- what the agent does: offers and reloads ------------------------------------------------------------

    async def _listen(self) -> None:
        q = self.service.bus.subscribe(4000)
        try:
            while True:
                ev = await q.get()
                try:
                    await self._on_event(ev)
                except Exception:  # noqa: BLE001 - never let one event stop previews
                    log.exception("preview: handling %s failed", ev.type)
        finally:
            self.service.bus.unsubscribe(q)

    async def _on_event(self, ev) -> None:
        pid = ev.project
        if pid not in self.previews:
            return
        p = self.previews[pid]
        d = ev.data or {}
        if ev.type == "file_changed":
            path = str(d.get("path") or "")
            if WEB_FILES.search(path):
                self._touched.setdefault(pid, set()).add(path)
                watching = self._watchers.get(pid)
                if p.status == "running" and not p.hmr and p.viewers and not (d.get("source") == "watch" and watching and not watching.done()):
                    self._schedule_reload(p, f"{posixpath.basename(path)} changed", [path])
                if posixpath.basename(path) in ("package.json", "index.html") and p.status in ("unknown", "none", "native", "idle"):
                    p.detected = 0  # the project may have just become a web project
                if ev.task and self._live.get(pid) != ev.task and self._live_build(pid):
                    self._live[pid] = ev.task
                    asyncio.get_running_loop().create_task(self._go_live(p, ev.task, path))
        elif ev.type == "session_connected":
            asyncio.get_running_loop().create_task(self.refresh(pid, quiet=True))
        elif ev.type == "task_started":
            task = d.get("task") or {}
            if task.get("kind") == "agent" and task.get("lane") != "ui":
                self._touched[pid] = set()
                self._before[pid] = self._web_changes(pid)
                self.service.code.prime(self.service.sessions[pid])
                if self._live_build(pid) and ev.task:
                    old = self._builds.pop(pid, None)
                    if old and not old.done():
                        old.cancel()
                    self._builds[pid] = asyncio.get_running_loop().create_task(self._watch_build(p, ev.task))
                    # More work on a page that's already live ("make it more premium"): show it now, while the agent
                    # thinks, so every change lands in front of the user. A new build waits for its first page.
                    if task.get("source") not in ("agent", "assistant"):
                        asyncio.get_running_loop().create_task(self._live_if_running(p, ev.task))
        elif ev.type in ("task_completed", "task_failed"):
            task = d.get("task") or {}
            if task.get("kind") != "agent" or task.get("lane") == "ui":
                return
            asyncio.get_running_loop().create_task(self._after_turn(p, task))

    def _live_build(self, pid: str) -> bool:
        st = self.settings(pid)
        return st.get("live_build", True) and st.get("auto_open", "auto") != "never"

    async def _go_live(self, p: Preview, task_id: str, path: str) -> None:
        """Watch it being built: the first web file the agent writes in a task puts the page on the TV, and every
        save after that reloads it. FireDev starts the server itself: its own static server always (it runs no
        project code), the project's dev server when the user asked for this work (asking is the approval;
        installing dependencies still asks on the TV). The TV only switches once the page really answers."""
        s = self.service.sessions[p.pid]
        task = s._task(task_id)
        if not task or task.kind != "agent" or task.lane == "ui" or not self.gateway_available():
            return
        await asyncio.sleep(1.0)  # let the first files land
        user_asked = task.source not in ("agent", "assistant")
        deadline = time.time() + START_TIMEOUT
        styles_by = time.time() + STYLES_GRACE
        try:
            while time.time() < deadline and task.status in ("running", "queued", "waiting_approval", "completed"):
                if p.status != "running":
                    await self.refresh(p.pid, quiet=True)
                if p.status != "running" and p.kind in ("web", "static"):
                    if p.needs_install and task.status != "completed":
                        # The agent is probably installing them right now; wait rather than ask.
                        await asyncio.sleep(3)
                        continue
                    source = "voice" if user_asked else "agent"
                    if self.start_level(p, source) <= 1:
                        await self.ensure_running(p.pid, source)
                if p.status == "running" and p.port:
                    code = await self.probe(p.pid, p.port)
                    if code is not None and code < 400:
                        # Not before its own stylesheets load: bare HTML says "broken", not "being built".
                        missing = await self.missing_styles(p.pid, p.port)
                        if not missing or task.status == "completed" or time.time() > styles_by:
                            break
                        p.message = f"Waiting for {posixpath.basename(missing[0])}…"
                    else:
                        p.message = "Waiting for the first page…"
                elif p.status in ("failed", "native", "none") and task.status == "completed":
                    return
                await asyncio.sleep(2)
            else:
                return
        except Exception:  # noqa: BLE001 - the live view is a bonus; the agent's work goes on regardless
            log.exception("preview: going live failed")
            return
        if p.status != "running" or self._live.get(p.pid) != task_id:
            return
        self.service.bus.emit("preview_offer", p.pid, task_id, preview=p.to_dict(), live=True, auto=True,
                              message=f"Watch {s.adapter.display} build it: the page updates as files change.",
                              files=[path])

    async def _live_if_running(self, p: Preview, task_id: str) -> None:
        """At the start of the user's request: if the project's page is already being served, put it on screen now.
        Never starts anything (a new build goes live with its first page)."""
        if p.status != "running" or time.time() - p.detected > 20:
            try:
                await self.refresh(p.pid, quiet=True)
            except Exception:  # noqa: BLE001
                return
        if p.status == "running" and p.kind in ("web", "static") and self._live.get(p.pid) != task_id:
            self._live[p.pid] = task_id
            await self._go_live(p, task_id, "")

    async def missing_styles(self, pid: str, port: int, timeout: float = 4.0) -> list[str] | None:
        if not self.tunnels or not hasattr(self.tunnels, "missing_styles"):
            return []
        return await self.tunnels.missing_styles(self.service.sessions[pid], port, timeout)

    def gateway_available(self) -> bool:
        st = self.service.config.settings
        return bool(st.preview_port or st.preview_url) and self.tunnels is not None

    async def _watch_build(self, p: Preview, task_id: str) -> None:
        """Follows the files an agent task writes, however it writes them: apply_patch edits arrive as events, but
        `cat > index.html` from its shell doesn't. Every couple of seconds this lists web files modified since the
        last look (git or not), records the ones no event reported as file activity, and lets them drive the live
        preview (going live, reloads)."""
        s = self.service.sessions[p.pid]
        d = f"{s.state_dir}/preview"
        q = shlex.quote
        mark, nxt = f"{d}/watch-{task_id}", f"{d}/watch-{task_id}.next"
        prune = " -o ".join(f"-name {n}" for n in ("node_modules", ".git", ".firedev", "dist", "build", ".next", ".cache", "__pycache__", ".venv"))
        find = (f"cd {q(s.path)} 2>/dev/null && find . \\( {prune} \\) -prune -o -type f -newer {q(mark)} -print 2>/dev/null | head -60")
        try:
            await s.remote.run(f"mkdir -p {q(d)} && touch {q(mark)}", timeout=10)
            _, out, _ = await s.remote.run(f"cd {q(s.path)} 2>/dev/null && find . \\( {prune} \\) -prune -o -type f -print 2>/dev/null | head -4000", timeout=20)
        except RemoteError:
            return
        existed = {ln[2:] for ln in out.splitlines() if ln.startswith("./")}
        pending: dict[str, float] = {}
        try:
            while True:
                task = s._task(task_id)
                if not task or task.status not in ("running", "queued", "waiting_approval"):
                    break
                await asyncio.sleep(2.0)
                try:
                    _, out, _ = await s.remote.run(f"touch {q(nxt)} && {{ {find}; }}; mv -f {q(nxt)} {q(mark)}", timeout=15)
                except RemoteError:
                    continue
                now = time.time()
                for ln in out.splitlines():
                    rel = ln[2:] if ln.startswith("./") else ln
                    if WEB_FILES.search(rel):
                        pending.setdefault(rel, now)
                # A file seen here gets one look later: if the agent's own file event reported it meanwhile, it's
                # already on screen; otherwise it was written from the shell and this is its only record.
                for rel, seen in list(pending.items()):
                    if now - seen < 2.5:
                        continue
                    del pending[rel]
                    reported = any(a["kind"] == "file" and a.get("task") == task_id and a.get("detail") == rel and a["ts"] >= seen - 4
                                   for a in list(s.activity)[-40:])
                    if reported:
                        continue
                    change = "update" if rel in existed else "add"
                    existed.add(rel)
                    if task:
                        task.files[rel] = change
                    s.add_activity("file", f"{'Created' if change == 'add' else 'Edited'} {posixpath.basename(rel)}", detail=rel, level=1, task=task_id)
                    s.emit("file_changed", task_id, path=rel, change=change, source="watch")
                    s._spawn(s.refresh_changes())
                    s._spawn(self.service.code.capture(s, task_id, rel, change))
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await s.remote.run(f"rm -f {q(mark)} {q(nxt)}", timeout=10)
            except Exception:  # noqa: BLE001
                pass

    def _web_changes(self, pid: str) -> dict:
        files = self.service.sessions[pid].changes.get("files") or []
        return {f["path"]: (f.get("added"), f.get("removed")) for f in files if WEB_FILES.search(f["path"])}

    async def _after_turn(self, p: Preview, task: dict) -> None:
        pid = p.pid
        s = self.service.sessions[pid]
        touched = self._touched.pop(pid, set())
        before = self._before.pop(pid, None)
        if before is not None:
            # Files written from the agent's shell (cat > index.html) don't produce file events; git still sees them.
            await s.refresh_changes()
            after = self._web_changes(pid)
            touched |= {f for f, v in after.items() if before.get(f) != v}
        if not touched:
            return
        await self.refresh(pid, quiet=True)
        if p.status == "running":
            if not p.hmr and p.viewers:
                self._schedule_reload(p, "the agent finished")
            elif not p.viewers:
                self._offer(p, task, touched)
        elif p.kind in ("web", "static") and p.status in ("idle", "failed"):
            self._offer(p, task, touched)

    def _offer(self, p: Preview, task: dict, touched: set[str]) -> None:
        s = self.service.sessions[p.pid]
        mode = str(self.settings(p.pid).get("auto_open", "auto"))
        text = f"{task.get('title') or ''} {task.get('text') or ''}"
        visual = bool(VISUAL_WORDS.search(text) and BUILD_WORDS.search(text))
        auto = mode == "open" or (mode == "auto" and visual and task.get("status") == "completed")
        if mode == "never":
            return
        ready = p.status == "running"
        msg = (f"{s.adapter.display} changed {len(touched)} web file{'s' if len(touched) != 1 else ''}. "
               + ("The live preview is ready." if ready else "Start the preview to see it running?"))
        self.service.bus.emit("preview_offer", p.pid, task.get("id"), preview=p.to_dict(), message=msg, auto=auto and (ready or p.kind == "static"),
                              files=sorted(touched)[:8])

    def _schedule_reload(self, p: Preview, reason: str, files: list[str] | None = None) -> None:
        """One reload for a burst of saves. When every file in the burst is a stylesheet, the TV swaps the styles in
        place (no reload: the page keeps its scroll, its state and doesn't flash)."""
        old = self._reload_at.pop(p.pid, None)
        if old:
            old.cancel()
        batch = self._reload_files.setdefault(p.pid, [])
        batch.append(None if files is None else list(files))

        def fire():
            parts = self._reload_files.pop(p.pid, [])
            changed = [f for part in parts if part for f in part]
            css = bool(changed) and all(part is not None for part in parts) and all(f.lower().endswith(".css") for f in changed)
            self.service.bus.emit("preview_reload", p.pid, None, reason=reason, css=css, files=sorted(set(changed))[:8])
        self._reload_at[p.pid] = asyncio.get_running_loop().call_later(0.35, fire)

    # --- detection ---------------------------------------------------------------------------------------

    async def refresh(self, pid: str, quiet: bool = False) -> Preview:
        """Re-reads the project and its listening ports. Never starts anything."""
        p = self.previews[pid]
        s = self.service.sessions[pid]
        if s.conn_state != "connected" or not s.remote.connected:
            if not quiet:
                p.message = "The project isn't connected yet."
            return p
        async with self._locks[pid]:
            if pid in self._starting and not self._starting[pid].done():
                return p
            try:
                _, out, _ = await s.remote.run(_detect_script(s.path, s.state_dir, s.project.tmux_session), timeout=25)
            except RemoteError as e:
                p.message = f"Couldn't read the project: {e}"
                self._emit(p)
                return p
            raw = parse_detect(out)
            self._apply(p, raw, plan(raw, s.path, self.settings(pid)))
            p.detected = time.time()
            if p.status == "running" and not p.managed:
                # Only adopt a port that answers HTTP: a project can also run a database or a debugger.
                prefer = self.settings(pid).get("port")
                ports = sorted({sv["port"] for sv in p.servers}, key=lambda x: (x != prefer, x))
                p.port = None
                for x in ports:
                    if await self.probe(pid, x) is not None:
                        p.port = x
                        break
                if p.port is None:
                    self._apply_idle(p, plan(raw, s.path, self.settings(pid)))
                else:
                    p.message = f"Found a server on port {p.port} already running in the project."
            elif p.status == "running" and await self.probe(pid, p.port) is None:
                p.status = "starting"
            if p.status == "starting" and p.managed and not (pid in self._starting and not self._starting[pid].done()):
                self._starting[pid] = asyncio.get_running_loop().create_task(self._wait_ready(p, time.time() + START_TIMEOUT))
            self._emit(p)
            if p.status == "running" and not p.hmr and p.viewers:
                self._ensure_watcher(p)
            return p

    def _apply(self, p: Preview, raw: dict, info: dict) -> None:
        meta = raw["meta"] or {}
        if meta.get("project_path") and meta["project_path"] != self.service.sessions[p.pid].path:
            meta = {}  # another FireDev install's project with the same id: not ours to adopt
        p.kind, p.framework, p.label, p.dir = info["kind"], info["framework"], info["label"], info["dir"]
        p.hmr, p.needs_install, p.native, p.alternatives = info["hmr"], info["needs_install"], info["native"], info["alternatives"]
        p.install = info.get("install")
        p.servers = info["servers"]
        p.screenshots = raw["shots"][:24]
        p.path = str(self.settings(p.pid).get("path") or p.path or "/")
        if info["command"]:
            p.command = info["command"]
        listening = {sv["port"] for sv in p.servers}
        managed_port = meta.get("port") if raw["window"] else None
        if managed_port and int(managed_port) in listening | raw["all_ports"]:
            p.port, p.managed, p.status = int(managed_port), True, "running"
            p.framework, p.label = meta.get("framework") or p.framework, meta.get("label") or p.label
            p.hmr = bool(meta.get("hmr", p.hmr))
            p.message = f"{p.label} is running (started by FireDev)."
        elif managed_port and raw["window"]:
            p.port, p.managed, p.status = int(managed_port), True, "starting"
        elif meta.get("port") and not listening:
            # FireDev started a server here and it has stopped by itself: say so, with its last words.
            p.port, p.managed, p.status = None, False, "failed"
            p.log = raw["log"][-30:]
            p.message = (_diagnose(p.log) or "The dev server stopped.") + " Say “start the dev server” to start it again."
        elif listening:
            # A server the project's own processes already run (the user's `npm run dev`, say); refresh() checks
            # which of its ports speaks HTTP before adopting it.
            p.port, p.managed, p.status = None, False, "running"
            if info["framework"] in (None, "running"):
                p.kind, p.framework, p.label = "web", "running", "Running server"
            if p.framework in ("static", "script", "running"):
                p.hmr = False
        else:
            self._apply_idle(p, info)

    def _apply_idle(self, p: Preview, info: dict) -> None:
        p.port, p.managed = None, False
        if p.framework == "running":
            p.kind, p.framework, p.label = None, None, ""
        if p.kind in ("web", "static"):
            p.status = "idle"
            p.message = f"{p.label} project. The preview isn't running yet." + (" Its dependencies need installing first." if p.needs_install else "")
        elif p.kind == "native":
            p.status, p.message = "native", info["message"]
        else:
            p.status = "none"
            p.message = info["message"] or "There's no web page in this project yet. Ask the agent to build one and FireDev will show it."

    def suspect(self, pid: str) -> None:
        """The gateway couldn't reach the server: check (at most every few seconds) whether it is still there."""
        p = self.previews.get(pid)
        if not p or time.time() - p.detected < 4 or (pid in self._starting and not self._starting[pid].done()):
            return
        p.detected = time.time()
        asyncio.get_running_loop().create_task(self.refresh(pid, quiet=True))

    async def probe(self, pid: str, port: int, timeout: float = 4.0) -> int | None:
        """HTTP status of GET / through the tunnel, or None if nothing answers."""
        if not self.tunnels:
            return None
        try:
            return await self.tunnels.probe(self.service.sessions[pid], port, timeout)
        except Exception:  # noqa: BLE001
            return None

    # --- starting and stopping ---------------------------------------------------------------------------

    def start_level(self, p: Preview, source: str) -> int:
        """FireDev's own static server runs no project code. Any other dev server runs the project's code (its
        config and scripts) outside the agent's sandbox: an agent (or an assistant such as Alexa or an MCP client)
        asking for it needs the TV's approval; the user asking on the TV or by voice is the approval. Installing dependencies downloads and runs code, so it always asks."""
        if p.needs_install:
            return 2
        if p.framework == "static" and not self.settings(p.pid).get("command"):
            return 0
        return 2 if source in ("agent", "assistant") else 1

    async def ensure_running(self, pid: str, source: str, *, install: bool = False, dir: str | None = None,
                             wait: bool = True) -> Preview:
        p = self.previews[pid]
        s = self.service.sessions[pid]
        if s.conn_state != "connected":
            await s.open()
        if not p.detected or time.time() - p.detected > 20 or dir is not None:
            await self.refresh(pid, quiet=True)
        if dir is not None and p.kind == "native":
            alt = next((a for a in p.alternatives if a.get("op") == "start" and "dir" in a and a["dir"] == dir), None)
            if alt is not None:
                p.kind, p.framework, p.label, p.dir, p.hmr = "static", "static", FRAMEWORKS["static"][0], dir, False
                p.command, p.needs_install = FRAMEWORKS["static"][1], False
        if p.status == "running" or (p.status == "starting" and not wait):
            return p
        t = self._starting.get(pid)
        if p.status == "starting" and t and not t.done():
            await asyncio.shield(t)
            return p
        if p.kind not in ("web", "static") or not p.command:
            return p
        if p.needs_install and not install:
            p.message = "The dependencies aren't installed yet. Say “install the dependencies” to install them and start the preview."
            self._emit(p)
            return p
        t = self._starting.get(pid)
        if not t or t.done():
            t = asyncio.get_running_loop().create_task(self._start(p, source))
            self._starting[pid] = t
        if wait:
            await asyncio.shield(t)
        return p

    async def _start(self, p: Preview, source: str) -> None:
        s = self.service.sessions[p.pid]
        level = self.start_level(p, source)
        q = shlex.quote
        cmd = p.command or ""
        pm_install = p.install if p.needs_install else None
        workdir = posixpath.join(s.path, p.dir) if p.dir else s.path
        try:
            _, out, _ = await s.remote.run(_detect_script(s.path, s.state_dir, s.project.tmux_session), timeout=25)
            raw = parse_detect(out)
        except RemoteError as e:
            p.status, p.message = "failed", str(e)
            self._emit(p)
            return
        conf_port = self.settings(p.pid).get("port")
        meta = raw["meta"] or {}
        port = _port_for(p.pid, raw["all_ports"], int(conf_port) if conf_port else meta.get("port"))
        full = cmd.replace("{port}", str(port))
        shown = (pm_install + " && " if pm_install else "") + full
        if level >= 2:
            p.status, p.message = "starting", "Waiting for your approval on the TV"
            self._emit(p)
            who = s.adapter.display if source == "agent" else "FireDev"
            ok, note = await self.service.request_approval(
                s, task=s.current if source == "agent" else None, command=f"cd {p.dir or '.'} && {shown}",
                title=("Install dependencies and start the dev server" if pm_install else f"Start the {p.label} dev server"),
                reason=(f"{who} wants to show the live preview on the TV." if source == "agent" else "You asked for the live preview."),
                risk=("Downloads packages from the internet and runs their install scripts, then runs the project's dev server "
                      "outside the agent's sandbox." if pm_install else
                      "Runs the project's own dev server and config outside the agent's sandbox, on 127.0.0.1 only."),
                level=2, source="agent" if source == "agent" else "voice")
            if not ok:
                p.status, p.message = "idle", f"Not started: {note or 'rejected on the TV'}"
                self._emit(p)
                return
        p.status, p.port, p.managed, p.started = "starting", port, True, time.time()
        p.log, p.errors = [], []
        p.message = ("Installing dependencies…" if pm_install else f"Starting {p.label}…")
        self._emit(p)
        d = f"{s.state_dir}/preview"
        script = "\n".join([
            "#!/usr/bin/env bash",
            f"cd {q(workdir)} || {{ echo 'The project folder is missing'; exit 1; }}",
            f"export PORT={port} HOST=127.0.0.1 BROWSER=none FORCE_COLOR=0 NO_COLOR=1 CI=1 FIREDEV_PREVIEW=1",
            f"export PATH={q(workdir + '/node_modules/.bin')}:\"$PATH\"",
            (f"{pm_install} || exit $?" if pm_install else ""),
            f"echo 'FireDev: starting {p.label} on 127.0.0.1:{port}'",
            f"{full}",
            "",
        ])
        meta = {"port": port, "framework": p.framework, "label": p.label, "hmr": p.hmr, "dir": p.dir, "command": full,
                "started": time.time(), "source": source, "project_path": s.path}
        try:
            await s.remote.write_file(f"{d}/cmd.sh", script, mode="755")
            await s.remote.write_file(f"{d}/meta.json", json.dumps(meta))
            await s.remote.run(f": > {q(d)}/out.log")
            await s._ensure_tmux()
            t = q(f"={s.project.tmux_session}:preview")
            await s.remote.run(f"tmux kill-window -t {t} 2>/dev/null; tmux new-window -d -t ={q(s.project.tmux_session)}: -n preview "
                               f"-c {q(workdir)} {q(f'bash -l {d}/cmd.sh 2>&1 | tee -a {d}/out.log')}", check=True)
        except RemoteError as e:
            p.status, p.message = "failed", f"Couldn't start the server: {e}"
            self._emit(p)
            return
        s.add_activity("preview", f"Starting the live preview ({p.label})", detail=shown, level=level, status="running")
        await self._wait_ready(p, time.time() + (INSTALL_TIMEOUT if pm_install else START_TIMEOUT))

    async def _wait_ready(self, p: Preview, deadline: float) -> None:
        """Follows a FireDev-managed server's log until it answers HTTP, its window dies, or time runs out."""
        s = self.service.sessions[p.pid]
        q = shlex.quote
        d = f"{s.state_dir}/preview"
        port = p.port
        started = p.started or time.time()
        last_log = ""
        while time.time() < deadline:
            await asyncio.sleep(0.8)
            if not s.remote.connected:
                continue
            try:
                _, out, _ = await s.remote.run(
                    f"tail -n 40 {q(d)}/out.log 2>/dev/null; echo '@@w'; tmux list-windows -t ={q(s.project.tmux_session)} "
                    f"-F '#{{window_name}}' 2>/dev/null | grep -qx preview && echo alive", timeout=10)
            except RemoteError:
                continue
            text, _, w = out.partition("@@w")
            lines = [ln for ln in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text).splitlines() if ln.strip()]
            if text != last_log:
                last_log = text
                p.log = lines[-30:]
                self._emit(p)
            if await self.probe(p.pid, port, 2.0) is not None:
                p.port = port
                p.status, p.message = "running", f"{p.label} is running."
                self._emit(p)
                s.add_activity("preview", f"Live preview running ({p.label})", detail=f"127.0.0.1:{port} on {s.project.machine.display}",
                               level=0)
                if not p.hmr and p.viewers:
                    self._ensure_watcher(p)
                return
            if "alive" not in w:
                p.status = "failed"
                p.message = _diagnose(lines) or "The dev server stopped. The log is below."
                self._emit(p)
                s.add_activity("preview", "The live preview couldn't start", detail="\n".join(lines[-12:]), level=0, status="failed")
                return
            # Some servers ignore the port they're given: adopt a new port the project's processes opened.
            if time.time() - started > 12:
                try:
                    _, o2, _ = await s.remote.run(_detect_script(s.path, s.state_dir, s.project.tmux_session), timeout=20)
                    raw = parse_detect(o2)
                    others = [sv["port"] for sv in raw["ports"] if sv["port"] != port]
                except RemoteError:
                    others, raw = [], None
                for other in others:
                    if await self.probe(p.pid, other, 2.0) is not None:
                        port = p.port = other
                        meta = dict((raw or {}).get("meta") or {}, port=other)
                        await s.remote.write_file(f"{d}/meta.json", json.dumps(meta))
                        break
        p.status = "failed"
        p.message = _diagnose(p.log) or "The dev server didn't answer in time."
        self._emit(p)

    async def reset(self, pid: str) -> None:
        """Between demo takes: stop FireDev's own server for the project and forget everything seen, so the next
        build is detected and goes live from scratch. The repository is not touched."""
        for d in (self._starting, self._watchers, self._builds):
            t = d.pop(pid, None)
            if t:
                t.cancel()
        h = self._reload_at.pop(pid, None)
        if h:
            h.cancel()
        for d in (self._live, self._reload_files, self._touched, self._before):
            d.pop(pid, None)
        p = self.previews[pid]
        if p.managed and p.status in ("running", "starting", "failed"):
            try:
                await self.stop_server(pid)
            except (RemoteError, OSError):
                pass
        self.previews[pid] = Preview(pid)
        self._emit(self.previews[pid])

    async def stop_server(self, pid: str) -> Preview:
        p = self.previews[pid]
        s = self.service.sessions[pid]
        if not p.managed:
            p.message = "That server wasn't started by FireDev, so FireDev leaves it running."
            self._emit(p)
            return p
        await s.remote.run(f"tmux kill-window -t {shlex.quote(f'={s.project.tmux_session}:preview')} 2>/dev/null")
        await s.remote.run(f"rm -f {shlex.quote(s.state_dir)}/preview/meta.json")
        p.status, p.managed, p.port = "idle", False, None
        p.message = "Stopped the preview server."
        self._emit(p)
        return p

    # --- the TV: tickets, viewers, reports --------------------------------------------------------------

    def issue_ticket(self, client: "Client", pid: str, to: str | None = None) -> str:
        now = time.time()
        for k in [k for k, t in self.tickets.items() if t.expires < now]:
            del self.tickets[k]
        tok = secrets.token_urlsafe(32)
        self.tickets[tok] = Ticket(device=client.id, pid=pid, to=_safe_path(to or self.previews[pid].page or self.previews[pid].path),
                                   expires=now + TICKET_TTL)
        return tok

    def redeem(self, tok: str) -> tuple[ViewSession, str] | None:
        t = self.tickets.pop(tok or "", None)
        if not t or t.expires < time.time() or not self.device_ok(t.device):
            return None
        now = time.time()
        for k in [k for k, v in self.view_sessions.items() if now - v.seen > SESSION_IDLE]:
            del self.view_sessions[k]
        vs = ViewSession(id=secrets.token_urlsafe(32), device=t.device, pid=t.pid)
        self.view_sessions[vs.id] = vs
        return vs, t.to

    def session_for(self, sid: str | None) -> ViewSession | None:
        vs = self.view_sessions.get(sid or "")
        if not vs:
            return None
        if time.time() - vs.seen > SESSION_IDLE or not self.device_ok(vs.device):
            self.view_sessions.pop(vs.id, None)
            return None
        vs.seen = time.time()
        return vs

    def device_ok(self, device: str) -> bool:
        if device == "admin":
            return True
        d = next((d for d in self.service.devices.devices if d["id"] == device), None)
        return bool(d) and d.get("role") in ("tv", "admin") and not (d.get("expires") and d["expires"] < time.time())

    def target(self, vs: ViewSession) -> tuple["ProjectSession", int] | None:
        p = self.previews.get(vs.pid)
        if not p or not p.port or p.status not in ("running", "starting"):
            return None
        return self.service.sessions[vs.pid], p.port

    def set_viewer(self, client: "Client", pid: str, visible: bool, layout: str | None = None, viewport: str | None = None) -> Preview:
        p = self.previews[pid]
        was = bool(p.viewers)
        if visible:
            p.viewers[client.id] = {"layout": layout, "viewport": viewport, "ts": time.time()}
        else:
            p.viewers.pop(client.id, None)
        if bool(p.viewers) != was:
            if p.viewers and p.status == "running" and not p.hmr:
                self._ensure_watcher(p)
            self._emit(p)
        return p

    def report(self, pid: str, data: dict) -> None:
        p = self.previews[pid]
        if data.get("page") is not None:
            p.page = _safe_path(str(data["page"]))
            p.title = str(data.get("title") or "")[:120] or None
        err = data.get("error")
        if err:
            msg = str(err)[:400]
            if not p.errors or p.errors[-1]["message"] != msg:
                p.errors.append({"message": msg, "ts": time.time(), "page": p.page})
                del p.errors[:-10]
                self._emit(p)

    # --- static servers: reload when files change ---------------------------------------------------------

    def _ensure_watcher(self, p: Preview) -> None:
        t = self._watchers.get(p.pid)
        if t and not t.done():
            return
        self._watchers[p.pid] = asyncio.get_running_loop().create_task(self._watch(p))

    async def _watch(self, p: Preview) -> None:
        s = self.service.sessions[p.pid]
        q = shlex.quote
        root = posixpath.join(s.path, p.dir) if p.dir else s.path
        stamp = f"{s.state_dir}/preview/stamp"
        # Exits with the SSH session that started it (the same rule as the other helpers), or when no TV watches.
        script = (f"mkdir -p {q(posixpath.dirname(stamp))}; touch {q(stamp)}; parent=$PPID; cd {q(root)} || exit 0; "
                  f"while kill -0 $parent 2>/dev/null && [ \"$(ps -o ppid= -p $$ | tr -d ' ')\" != 1 ]; do sleep 0.7; "
                  f"n=$(find . \\( -path ./node_modules -o -path ./.git -o -path ./.firedev \\) -prune -o -type f -newer {q(stamp)} -print 2>/dev/null | head -1); "
                  f"if [ -n \"$n\" ]; then touch {q(stamp)}; echo \"changed $n\"; fi; done")
        try:
            async for chunk in s.remote.stream(script):
                if not p.viewers or p.hmr or p.status != "running":
                    break
                files = [ln[8:].lstrip("./") for ln in chunk.splitlines() if ln.startswith("changed ")]
                if files:
                    self._schedule_reload(p, f"{posixpath.basename(files[-1])} changed", files)
        except RemoteError:
            pass

    # --- the agent's `firedev preview` -----------------------------------------------------------------------

    async def agent_request(self, s: "ProjectSession", req: dict) -> tuple[str, str]:
        """Returns (state line, output) for the helper."""
        sub = (req.get("action") or "status").strip()
        arg = (req.get("command") or "").strip()
        pid = s.project.id
        if sub == "status":
            p = await self.refresh(pid)
            return "done", self.describe(p)
        if sub == "stop":
            p = await self.stop_server(pid)
            return "done", p.message
        if sub in ("start", "open", "show"):
            if arg:
                if arg.startswith("/"):
                    self.previews[pid].page = _safe_path(arg)
            # Building what the user asked for: their request covers starting the dev server (installs still ask).
            task = s.current
            source = "voice" if task and task.kind == "agent" and task.source not in ("agent", "assistant") else "agent"
            p = await self.ensure_running(pid, source, install=self.previews[pid].needs_install)
            code = await self.probe(pid, p.port) if p.status == "running" and p.port else None
            if p.status == "running" and (code is None or code >= 400):
                return "done", self.describe(p) + f"\nThe server answers {code or 'nothing yet'} on {p.path}: the TV shows it once the page loads."
            if p.status == "running" and sub in ("open", "show"):
                self.service.bus.emit("preview_control", pid, None, op="show", layout="split", path=self.previews[pid].page,
                                      source="agent")
                return "done", self.describe(p) + "\nThe live preview is now on the TV."
            return "done", self.describe(p)
        return "error", f"unknown preview command '{sub}' (try: status, start, open [/path], stop)"

    def describe(self, p: Preview) -> str:
        lines = [f"Preview: {p.status}" + (f" · {p.label}" if p.label else "") + (f" · 127.0.0.1:{p.port} on the dev machine" if p.port else "")]
        if p.message:
            lines.append(p.message)
        if p.status == "running":
            lines.append("Hot reload: on (the TV updates as you save)." if p.hmr else
                         "No hot reload: FireDev reloads the TV's preview when files change.")
            if p.viewers:
                lines.append(f"The user is watching it on the TV{f', page {p.page}' if p.page else ''}.")
        if p.log and p.status in ("failed", "starting"):
            lines.append("Server log (last lines):\n" + "\n".join(p.log[-12:]))
        if p.errors:
            lines.append("Errors the page showed on the TV:\n" + "\n".join("- " + e["message"] for e in p.errors[-5:]))
        if p.native:
            lines.append("Alternatives: " + "; ".join(a["label"] for a in p.alternatives))
        return "\n".join(lines)

    def agent_context(self, pid: str, text: str = "") -> str | None:
        """Lines for the agent's prompt: how to show web work on the TV, and what the user is watching right now."""
        p = self.previews.get(pid)
        if not p or p.kind == "native":
            return None
        lines = []
        if p.kind in ("web", "static") or VISUAL_WORDS.search(text or ""):
            lines.append("FireDev shows this project's web pages live on the TV. When you have built or changed something visible, run "
                         "`firedev preview open` (or `firedev preview open /some/page`): FireDev starts the dev server outside your "
                         "sandbox, asking the user first if it runs project code, and puts the real page on the TV. "
                         "`firedev preview status` shows the server log and any errors the page had on the TV. Don't start dev "
                         "servers yourself; they can't run in the sandbox.")
        if p.status == "running" and p.viewers:
            v = next(iter(p.viewers.values()))
            lines.append(f"The user is watching the live preview on the TV right now ({p.label}, "
                         f"{'hot reload' if p.hmr else 'FireDev reloads it when files change'}"
                         + (f", page {p.page}" if p.page else "") + (f", {v.get('viewport')} view" if v.get("viewport") else "")
                         + "). Edit the source and the change appears there; no need to run `firedev preview open` again.")
            if p.errors:
                lines.append("The page showed these errors on the TV: " + "; ".join(e["message"][:160] for e in p.errors[-3:]))
        return "\n".join(lines) or None

    # --- screenshots (native apps) ------------------------------------------------------------------------

    async def screenshot(self, pid: str, i: int) -> tuple[bytes, str] | None:
        p = self.previews[pid]
        if not 0 <= i < len(p.screenshots):
            return None
        rel = p.screenshots[i]
        if rel.startswith("/") or ".." in rel.split("/"):
            return None
        s = self.service.sessions[pid]
        data = await s.remote.read_bytes(posixpath.join(s.path, rel))
        ext = rel.rsplit(".", 1)[-1].lower()
        return data, {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/png")


def _safe_path(path: str | None) -> str:
    """A path on the preview's own origin: never a URL elsewhere."""
    path = (path or "/").strip()
    u = urlparse(path)
    if u.scheme or u.netloc or not path.startswith("/") or path.startswith("//"):
        return "/"
    return quote(path[:600], safe="/?&=#%:@!$'()*+,;~-._")
