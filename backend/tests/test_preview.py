"""Live preview: detection, voice commands, tickets, the gateway (HTTP, HTML helper, headers, WebSockets), and over real
SSH (FIREDEV_TEST_SSH=1) a static site started, proxied through the tunnel, adopted after a restart and stopped."""

import asyncio
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute

from firedev import config as cfg
from firedev import intents
from firedev.auth import Client, DeviceStore
from firedev.gateway import COOKIE, Gateway, serve_gateway
from firedev.preview import Preview, _diagnose, _port_for, _safe_path, parse_detect, plan
from firedev.service import FireDev

TV = Client("tv1", "Living room TV", "tv")


def raw(packages=None, markers=(), ports=(), shots=()):
    return {"packages": packages or {}, "markers": set(markers), "shots": list(shots), "ports": list(ports), "all_ports": set(),
            "meta": None, "window": False}


# --- detection ----------------------------------------------------------------------------------------------

def test_plan_frameworks():
    vite = plan(raw({"package.json": {"devDependencies": {"vite": "5"}, "scripts": {"dev": "vite"}}}, {"node_modules", "package-lock.json"}), "/p")
    assert vite["framework"] == "vite" and vite["hmr"] and not vite["needs_install"]
    assert "--host 127.0.0.1" in vite["command"] and "{port}" in vite["command"]
    nxt = plan(raw({"package.json": {"dependencies": {"next": "15", "react": "19"}}}, {"pnpm-lock.yaml"}), "/p")
    assert nxt["framework"] == "next" and nxt["needs_install"] and nxt["install"] == "pnpm install"
    sub = plan(raw({"web/package.json": {"devDependencies": {"vite": "5"}}}, {"web/node_modules"}), "/p")
    assert sub["dir"] == "web" and not sub["needs_install"]
    script = plan(raw({"package.json": {"scripts": {"start": "node server.js"}}}, {"node_modules"}), "/p")
    assert script["framework"] == "script" and "run start" in script["command"] and not script["hmr"]
    static = plan(raw({}, {"index.html"}), "/p")
    assert static["kind"] == "static" and static["dir"] == ""
    docs = plan(raw({}, {"docs/index.html"}), "/p")
    assert docs["kind"] == "static" and docs["dir"] == "docs"


def test_plan_native_is_honest():
    android = plan(raw({}, {"build.gradle.kts", "app/src/main/AndroidManifest.xml"}, shots=["app/screenshots/home.png"]), "/p")
    assert android["kind"] == "native" and android["native"]["platform"] == "Android"
    assert [a["op"] for a in android["alternatives"]] == ["screenshots", "build"]
    assert "won't pretend" in android["message"]
    rn = plan(raw({"package.json": {"dependencies": {"react-native": "0.76"}}}), "/p")
    assert rn["kind"] == "native" and rn["native"]["platform"] == "React Native"
    expo = plan(raw({"package.json": {"dependencies": {"expo": "52", "react-native": "0.76"}}}), "/p")
    assert expo["kind"] == "native" and expo["alternatives"][0]["op"] == "agent"
    expo_web = plan(raw({"package.json": {"dependencies": {"expo": "52", "react-native-web": "0.19", "react-dom": "18"}}}, {"node_modules"}), "/p")
    assert expo_web["framework"] == "expo" and "--web" in expo_web["command"]
    flutter = plan(raw({}, {"pubspec.yaml", "web/index.html"}), "/p")
    assert flutter["kind"] == "native" and any(a["op"] == "start" for a in flutter["alternatives"])
    android_docs = plan(raw({}, {"build.gradle", "app/src/main/AndroidManifest.xml", "docs/index.html"}), "/p")
    assert android_docs["kind"] == "native" and any(a.get("dir") == "docs" for a in android_docs["alternatives"])


def test_plan_override_and_nothing():
    o = plan(raw({}, {"index.html"}), "/p", {"command": "make serve PORT={port}", "dir": "site"})
    assert o["command"] == "make serve PORT={port}" and o["dir"] == "site" and o["kind"] == "web"
    none = plan(raw(), "/p")
    assert none["kind"] is None and "no web page" in none["message"].lower()
    running = plan(raw(ports=[{"port": 4000, "pid": 1, "cwd": "/p", "cmd": "ruby"}]), "/p")
    assert running["framework"] == "running" and running["servers"][0]["port"] == 4000


def test_parse_detect_sections():
    out = """@@pkg package.json
{"devDependencies": {"vite": "5"}}
@@markers
index.html
node_modules
@@shots
./app/screenshots/a.png
@@ports
5173\t123\t/p\tnode vite
@@all
22 5173 8130
@@meta
{"port": 5321, "framework": "vite"}
@@window
running
@@end
"""
    r = parse_detect(out.replace("@@end", "@@log\n\x1b[31mError: boom\x1b[0m\n@@end"))
    assert r["log"] == ["Error: boom"]
    assert r["packages"]["package.json"]["devDependencies"]["vite"] == "5"
    assert r["markers"] == {"index.html", "node_modules"} and r["shots"] == ["app/screenshots/a.png"]
    assert r["ports"][0]["port"] == 5173 and r["all_ports"] == {22, 5173, 8130}
    assert r["meta"]["port"] == 5321 and r["window"]


def test_ports_avoid_conflicts():
    p = _port_for("alpha", set())
    assert _port_for("alpha", set()) == p, "stable for a project"
    assert _port_for("alpha", {p}) != p
    assert _port_for("alpha", set(), preferred=6001) == 6001
    assert _port_for("alpha", {6001}, preferred=6001) != 6001
    assert len({_port_for(n, set()) for n in ("alpha", "beta", "gamma", "delta")}) == 4


def test_diagnose_and_safe_path():
    assert "dependencies" in _diagnose(["sh: 1: vite: not found"])
    assert "port" in _diagnose(["Error: listen EADDRINUSE: address already in use 127.0.0.1:5173"]).lower()
    assert _diagnose(["all good"]) is None
    assert _safe_path("/pricing?x=1") == "/pricing?x=1"
    for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)", "pricing"):
        assert _safe_path(bad) == "/"


# --- voice ---------------------------------------------------------------------------------------------------

CTX = {"project": "web", "agent": "Codex", "actions": {}, "has_surface": True}


def p(text, **ctx):
    return intents.parse(text, [{"id": "web", "name": "Web", "aliases": []}], {**CTX, **ctx})


@pytest.mark.parametrize("text,op,extra", [
    ("Show me the preview", "show", {}),
    ("Codex, show me the website", "show", {}),
    ("Open the live preview on the TV", "show", {}),
    ("Show it fullscreen", "show", {"layout": "full"}),
    ("Full screen", "show", {"layout": "full"}),
    ("Split screen", "show", {"layout": "split"}),
    ("Go back to Codex", "close", {}),
    ("Close the preview", "close", {}),
    ("Start the dev server", "start", {}),
    ("Stop the dev server", "stop", {}),
    ("Restart the dev server", "restart", {}),
    ("Is the preview running?", "status", {}),
])
def test_preview_intents(text, op, extra):
    i = p(text, preview="running")
    assert i.name == "preview" and i.args["op"] == op, (text, i)
    for k, v in extra.items():
        assert i.args[k] == v


@pytest.mark.parametrize("text,args", [
    ("Show me the mobile version", {"op": "viewport", "viewport": "mobile"}),
    ("Switch to desktop view", {"op": "viewport", "viewport": "desktop"}),
    ("Zoom in", {"op": "zoom", "zoom": "in"}),
    ("Make the preview bigger", {"op": "size", "size": "bigger"}),
    ("Give Codex more room", {"op": "size", "size": "smaller"}),
    ("Reload the page", {"op": "nav", "nav": "reload"}),
    ("Go back", {"op": "nav", "nav": "back"}),
    ("Scroll down", {"op": "nav", "nav": "scroll", "dir": "down"}),
    ("Go to the pricing page", {"op": "nav", "nav": "click", "text": "pricing", "path": "/pricing"}),
    ("Click the sign up button", {"op": "nav", "nav": "click", "text": "sign up"}),
])
def test_preview_controls_while_watching(text, args):
    i = p(text, preview="running", preview_visible=True)
    assert i.name == "preview", (text, i)
    for k, v in args.items():
        assert i.args[k] == v, (text, i.args)


def test_site_edits_go_to_the_agent_while_watching():
    # With a generated screen around, "make X smaller" would normally edit that screen; while the TV shows the
    # site, it's a change to the site for the coding agent.
    i = p("Make the header smaller", preview="running", preview_visible=True)
    assert i.name == "send_agent_task" and i.args["mode"] == "change" and i.args.get("preview")
    assert p("Make the header smaller").name == "ui_edit"  # not watching: unchanged behaviour
    assert p("Go back").name == "ui_back"
    assert p("Zoom in").name == "ui_edit"
    assert p("Build me a modern landing page").name == "send_agent_task"


def test_native_and_unrelated_phrases():
    assert p("Show me the screenshots", preview="native").args.get("screenshots")
    assert p("Run the tests", actions={"test": {"kind": "tests"}}).name == "run_action"
    assert p("Show me what changed").name == "show_changes"
    assert p("What does it look like?", preview="running").name == "preview"
    assert p("What does it look like?").name != "preview"


# --- tickets and sessions --------------------------------------------------------------------------------------

def make_service(tmp_path, preview_port=0, project_preview=None):
    rawcfg = {"server": {"data_dir": str(tmp_path / "data"), "preview_port": preview_port},
              "machines": {"m": {"host": "localhost", "user": "u", "known_hosts": None}},
              "projects": [{"id": "web", "name": "Web", "machine": "m", "path": str(tmp_path), "agent": "shell",
                            **({"preview": project_preview} if project_preview is not None else {})}]}
    devices = DeviceStore(tmp_path / "home")
    return FireDev(cfg.parse(rawcfg), devices), devices


def test_config_preview(tmp_path):
    svc, _ = make_service(tmp_path, project_preview={"command": "npm run dev -- --port {port}", "path": "/app", "auto_open": "offer"})
    assert svc.config.projects["web"].preview["path"] == "/app"
    for bad in ({"auto_open": "always"}, {"dir": "../x"}, {"path": "app"}, {"nope": 1}):
        with pytest.raises(cfg.ConfigError):
            make_service(tmp_path, project_preview=bad)


async def test_tickets_are_single_use_and_device_bound(tmp_path):
    svc, devices = make_service(tmp_path)
    tok, dev = devices.create("TV", "tv")
    tv = devices.authenticate(tok)
    m = svc.previews
    t = m.issue_ticket(tv, "web", "/pricing")
    vs, to = m.redeem(t)
    assert to == "/pricing" and vs.pid == "web"
    assert m.redeem(t) is None, "single use"
    assert m.session_for(vs.id) is vs
    devices.revoke(dev["id"])
    assert m.session_for(vs.id) is None, "a revoked TV loses its preview sessions"
    phone_tok, _ = devices.create("Phone", "voice")
    assert m.redeem(m.issue_ticket(devices.authenticate(phone_tok), "web")) is None, "only TVs"
    old = m.issue_ticket(TV, "web")
    m.tickets[old].expires = time.time() - 1
    assert m.redeem(old) is None


async def test_preview_open_needs_a_tv(tmp_path):
    svc, devices = make_service(tmp_path)
    from firedev.service import CommandError
    with pytest.raises(CommandError):
        await svc.execute("preview_open", {"project": "web"}, Client("p", "Phone", "voice"))


# --- the gateway -------------------------------------------------------------------------------------------------

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeTunnels:
    """Stands in for the SSH forwards: the 'dev server' is a local upstream."""

    def __init__(self, port):
        self.port = port
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=httpx.Timeout(10, read=None))

    async def local_port(self, s, port):
        return self.port, self.client

    async def probe(self, s, port, timeout):
        return 200

    async def close(self):
        await self.client.aclose()


def upstream_app(seen):
    async def index(req):
        seen.append(dict(req.headers))
        return HTMLResponse("<!doctype html><html><head><title>T</title></head><body>hi</body></html>",
                            headers={"x-frame-options": "DENY", "content-security-policy": "default-src 'self'; frame-ancestors 'none'"})

    async def redirect(req):
        return Response(status_code=302, headers={"location": f"http://127.0.0.1:{req.url.port}/next"})

    async def cookie(req):
        r = Response("ok")
        r.raw_headers.append((b"set-cookie", b"a=1; Domain=127.0.0.1; Path=/; SameSite=Lax"))
        r.raw_headers.append((b"set-cookie", b"b=2; Path=/"))
        return r

    async def headers(req):
        return JSONResponse(dict(req.headers))

    async def hmr(ws):
        await ws.accept(subprotocol="vite-hmr" if "vite-hmr" in ws.scope.get("subprotocols", []) else None)
        await ws.send_text(json.dumps({"type": "connected", "origin": ws.headers.get("origin"), "cookie": ws.headers.get("cookie")}))
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                await ws.send_text("echo:" + msg["text"])
            elif msg.get("bytes") is not None:
                await ws.send_bytes(b"echo:" + msg["bytes"])

    return Starlette(routes=[Route("/", index), Route("/redirect", redirect), Route("/cookie", cookie), Route("/headers", headers),
                             WebSocketRoute("/hmr", hmr)])


@pytest.fixture
async def gw(tmp_path):
    import uvicorn
    svc, devices = make_service(tmp_path)
    up_port = free_port()
    seen = []
    up = uvicorn.Server(uvicorn.Config(upstream_app(seen), host="127.0.0.1", port=up_port, log_level="warning", lifespan="off"))
    up_task = asyncio.create_task(up.serve())
    while not up.started:
        await asyncio.sleep(0.02)
    gateway = Gateway(svc.previews)
    gateway.tunnels = svc.previews.tunnels = FakeTunnels(up_port)
    gport = free_port()
    server = await serve_gateway(gateway, "127.0.0.1", gport)
    pv = svc.previews.previews["web"]
    pv.status, pv.port, pv.framework = "running", 5999, "vite"
    tok, _ = devices.create("TV", "tv")
    yield {"svc": svc, "tv": devices.authenticate(tok), "base": f"http://127.0.0.1:{gport}", "seen": seen}
    server.should_exit = True
    up.should_exit = True
    await asyncio.sleep(0.2)
    up_task.cancel()


async def enter(g):
    t = g["svc"].previews.issue_ticket(g["tv"], "web", "/")
    c = httpx.AsyncClient(base_url=g["base"])
    r = await c.get(f"/__firedev/enter?t={t}")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "httponly" in r.headers["set-cookie"].lower()
    # 127.0.0.1 (the TV app's relay) is a secure context: the cookie must work in a third-party frame.
    assert "samesite=none" in r.headers["set-cookie"].lower() and "secure" in r.headers["set-cookie"].lower()
    # Browsers send Secure cookies to 127.0.0.1 over http; httpx doesn't, so carry it by hand.
    sid = r.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    c.cookies.clear()
    c.cookies.set(COOKIE, sid)
    c.headers["cookie"] = f"{COOKIE}={sid}"
    return c


async def test_gateway_refuses_without_a_session(gw):
    async with httpx.AsyncClient(base_url=gw["base"]) as c:
        assert (await c.get("/")).status_code == 403
        assert (await c.get("/", headers={"cookie": f"{COOKIE}=made-up"})).status_code == 403
        assert (await c.get("/__firedev/enter?t=bogus")).status_code == 403
        assert (await c.get("/__firedev/health")).status_code == 200


async def test_gateway_proxies_html_with_helper(gw):
    c = await enter(gw)
    sid = c.cookies.get(COOKIE)
    c.cookies.clear()
    r = await c.get("/", headers={"accept": "text/html", "origin": gw["base"], "referer": gw["base"] + "/x", "cookie": f"app=1; {COOKIE}={sid}"})
    assert r.status_code == 200
    assert '<head><script src="/__firedev/tv.js"></script><title>' in r.text
    assert "x-frame-options" not in r.headers
    assert "frame-ancestors" not in r.headers["content-security-policy"] and "default-src" in r.headers["content-security-policy"]
    sent = gw["seen"][-1]
    assert sent["cookie"] == "app=1"
    assert sent["host"].startswith("127.0.0.1:5999") and sent["origin"] == "http://127.0.0.1:5999"
    assert sent["referer"] == "http://127.0.0.1:5999/x"
    assert COOKIE not in sent.get("cookie", ""), "FireDev's session never reaches the dev server"
    c.cookies.set(COOKIE, sid)
    js = await c.get("/__firedev/tv.js")
    assert js.status_code == 200 and "postMessage" in js.text
    await c.aclose()


async def test_gateway_rewrites_redirects_and_cookies(gw):
    c = await enter(gw)
    r = await c.get("/redirect")
    assert r.status_code == 302 and r.headers["location"] == "/next"
    r = await c.get("/cookie")
    cookies = r.headers.get_list("set-cookie")
    assert len(cookies) == 2 and not any("domain" in x.lower() for x in cookies)
    await c.aclose()


async def test_gateway_stops_when_the_preview_stops(gw):
    c = await enter(gw)
    gw["svc"].previews.previews["web"].status = "idle"
    r = await c.get("/")
    assert r.status_code == 503 and 'fd:"down"' in r.text
    await c.aclose()


async def test_gateway_websockets(gw):
    from websockets.asyncio.client import connect
    c = await enter(gw)
    sid = c.cookies.get(COOKIE)
    uri = gw["base"].replace("http", "ws") + "/hmr?token=abc"
    async with connect(uri, subprotocols=["vite-hmr"], additional_headers={"cookie": f"{COOKIE}={sid}; app=1"}) as ws:
        hello = json.loads(await ws.recv())
        assert ws.subprotocol == "vite-hmr" and hello["type"] == "connected"
        assert hello["origin"] == "http://127.0.0.1:5999" and COOKIE not in (hello["cookie"] or "")
        await ws.send("ping")
        assert await ws.recv() == "echo:ping"
        await ws.send(b"\x00\x01")
        assert await ws.recv() == b"echo:\x00\x01"
    with pytest.raises(Exception):
        async with connect(uri) as ws:  # no session
            await ws.recv()
    await c.aclose()


# --- over real SSH ---------------------------------------------------------------------------------------------

ssh = pytest.mark.skipif(os.environ.get("FIREDEV_TEST_SSH") != "1", reason="FIREDEV_TEST_SSH=1 to run")


def ssh_service(tmp_path, proj: Path, sess: str):
    rawcfg = {"server": {"data_dir": str(tmp_path / "data"), "preview_port": 0},
              "machines": {"local": {"host": "localhost", "user": os.environ.get("USER", "abdul"), "auth": "key",
                                     "key_file": "~/.config/firedev/keys/firedev_ed25519"}},
              "projects": [{"id": "site", "name": "Site", "machine": "local", "path": str(proj), "agent": "shell", "tmux_session": sess}]}
    svc = FireDev(cfg.parse(rawcfg), DeviceStore(tmp_path / "home"))
    Gateway(svc.previews)  # owns the SSH forwards
    return svc


@pytest.mark.ssh
@ssh
async def test_static_site_over_ssh(tmp_path):
    proj = tmp_path / "site"
    proj.mkdir()
    (proj / "index.html").write_text("<!doctype html><title>Over SSH</title><h1>hello</h1>")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    sess = f"firedev-pvtest-{os.getpid()}"
    svc = ssh_service(tmp_path, proj, sess)
    try:
        s = svc.sessions["site"]
        await s.open()
        p = await svc.previews.refresh("site")
        assert p.status == "idle" and p.kind == "static"
        assert svc.previews.start_level(p, "agent") == 0, "FireDev's own static server needs no approval"
        p = await svc.previews.ensure_running("site", "tv")
        assert p.status == "running" and p.managed and p.port
        _, client = await svc.previews.tunnels.local_port(s, p.port)
        r = await client.get("/", headers={"host": f"127.0.0.1:{p.port}"})
        assert r.status_code == 200 and "Over SSH" in r.text
        # The network drops: the dev server keeps running on the machine, the forward is rebuilt after the reconnect.
        s.remote.drop()
        for _ in range(100):
            await asyncio.sleep(0.1)
            if s.conn_state == "connected" and s.remote.connected:
                break
        assert s.conn_state == "connected"
        _, client2 = await svc.previews.tunnels.local_port(s, p.port)
        assert client2 is not client
        r = await client2.get("/", headers={"host": f"127.0.0.1:{p.port}"})
        assert r.status_code == 200 and "Over SSH" in r.text
        # FireDev restarts: a new service finds the server still running in tmux and adopts it.
        await s.close()
        svc2 = ssh_service(tmp_path, proj, sess)
        await svc2.sessions["site"].open()
        p2 = await svc2.previews.refresh("site")
        assert p2.status == "running" and p2.managed and p2.port == p.port
        await svc2.previews.stop_server("site")
        assert svc2.previews.previews["site"].status == "idle"
        await svc2.sessions["site"].close()
    finally:
        subprocess.run(["tmux", "kill-session", "-t", f"={sess}"], capture_output=True)


@pytest.mark.ssh
@ssh
async def test_native_project_over_ssh(tmp_path):
    proj = tmp_path / "droid"
    (proj / "app/src/main").mkdir(parents=True)
    (proj / "build.gradle.kts").write_text("")
    (proj / "app/src/main/AndroidManifest.xml").write_text("<manifest/>")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    sess = f"firedev-pvtest-n{os.getpid()}"
    svc = ssh_service(tmp_path, proj, sess)
    try:
        await svc.sessions["site"].open()
        p = await svc.previews.refresh("site")
        assert p.status == "native" and p.native["platform"] == "Android"
        p = await svc.previews.ensure_running("site", "tv")
        assert p.status == "native", "never pretends to run a native app"
        await svc.sessions["site"].close()
    finally:
        subprocess.run(["tmux", "kill-session", "-t", f"={sess}"], capture_output=True)


def test_who_needs_approval_to_start_a_server(tmp_path):
    svc, _ = make_service(tmp_path)
    m = svc.previews
    vite = Preview("web", framework="vite")
    assert m.start_level(vite, "agent") == 2 and m.start_level(vite, "assistant") == 2
    assert m.start_level(vite, "tv") == 1 and m.start_level(vite, "voice") == 1
    assert m.start_level(Preview("web", framework="static"), "agent") == 0
    assert m.start_level(Preview("web", framework="static", needs_install=True), "tv") == 2


def test_never_adopts_another_installs_server(tmp_path):
    """Two FireDev installs on one machine with the same project id share ~/.firedev/projects/<id>: the preview's
    meta says whose it is."""
    svc, _ = make_service(tmp_path)
    svc.sessions["web"].path = "/home/me/web"
    p = svc.previews.previews["web"]
    theirs = raw({}, {"index.html"}, ports=[])
    theirs.update(meta={"port": 5389, "framework": "static", "project_path": "/tmp/other/web"}, window=True, all_ports={5389})
    svc.previews._apply(p, theirs, plan(theirs, "/home/me/web"))
    assert p.status == "idle" and not p.managed
    ours = dict(theirs, meta={"port": 5389, "framework": "static", "project_path": "/home/me/web"})
    svc.previews._apply(p, ours, plan(ours, "/home/me/web"))
    assert p.status == "running" and p.managed and p.port == 5389


def test_preview_dataclass_snapshot():
    d = Preview("x", status="running", screenshots=["a/b.png"]).to_dict()
    assert d["screenshots"][0] == {"i": 0, "name": "b.png", "path": "a/b.png"} and d["watching"] is False
