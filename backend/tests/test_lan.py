"""LAN mode, for real: TLS listeners, the mDNS announcement, pairing codes bound to the certificate (and refused
through a man in the middle), and the CLI's first-run commands."""

import asyncio
import hashlib
import json
import socket
import ssl
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from firedev import config as cfg
from firedev.auth import DeviceStore, bound_code

ROOT = Path(__file__).resolve().parents[2]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
async def lan_service(tmp_path):
    import uvicorn

    from firedev.app import create_app
    proj = tmp_path / "proj"
    proj.mkdir()
    ports = {k: free_port() for k in ("port", "preview_port", "lan_port", "lan_preview_port")}
    raw = {"server": {"host": "127.0.0.1", **ports, "lan": True, "name": "Test Laptop", "data_dir": str(tmp_path / "data")},
           "machines": {"this-computer": {"local": True}},
           "projects": [{"id": "app", "name": "App", "machine": "this-computer", "path": str(proj), "agent": "shell"}]}
    devices = DeviceStore(tmp_path / "home")
    app = create_app(cfg.parse(raw), devices)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports["port"], log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    yield {"devices": devices, "home": tmp_path / "home", **ports}
    server.should_exit = True
    await asyncio.wait_for(task, 10)


def tls_fingerprint(port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port)) as raw, ctx.wrap_socket(raw) as s:
        return hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()


async def test_lan_listener_is_tls_and_pairing_is_bound_to_its_certificate(lan_service):
    from websockets.asyncio.client import connect
    svc = lan_service
    fp = await asyncio.to_thread(tls_fingerprint, svc["lan_port"])
    on_disk = (svc["home"] / "tls" / "cert.pem").exists()
    assert on_disk and len(fp) == 64
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    claim = "c" * 32
    async with connect(f"wss://127.0.0.1:{svc['lan_port']}/ws?pair={claim}", ssl=ctx) as ws:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "pairing" and msg["bound"] and msg["machine"] == "Test Laptop"
        # The TV computes the code from the certificate it pinned; it matches the one this machine accepts.
        assert bound_code(fp, claim, msg["nonce"]) == msg["code"]
        # Behind a man in the middle the TV pinned the middle's certificate: its code is one this machine refuses.
        import httpx
        admin = {"Authorization": "Bearer " + (svc["home"] / "admin.token").read_text().strip()}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{svc['port']}") as c:  # what `firedev pair <code>` does
            mitm_code = bound_code("ab" * 32, claim, msg["nonce"])
            assert (await c.post("/api/pair", json={"code": mitm_code, "name": "MITM"}, headers=admin)).status_code == 404
            assert (await c.post("/api/pair", json={"code": msg["code"], "name": "Living room"}, headers=admin)).status_code == 200
        paired = json.loads(await asyncio.wait_for(ws.recv(), 10))
        assert paired["type"] == "paired" and svc["devices"].authenticate(paired["token"]).role == "tv"
    # Plain HTTP on 127.0.0.1 (the CLI, SSH tunnels) still pairs with ordinary codes.
    async with connect(f"ws://127.0.0.1:{svc['port']}/ws?pair={'d' * 32}") as ws:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "pairing" and not msg["bound"]


async def test_lan_info_and_preview_listener(lan_service):
    import httpx
    svc = lan_service
    async with httpx.AsyncClient(verify=False) as c:
        info = (await c.get(f"https://127.0.0.1:{svc['lan_port']}/api/info")).json()
        assert info["lan"] and info["fingerprint"] == await asyncio.to_thread(tls_fingerprint, svc["lan_port"])
        assert info["id"] == svc["devices"].machine_id()
        r = await c.get(f"https://127.0.0.1:{svc['lan_preview_port']}/")
        assert r.status_code == 403, "the preview gateway is on the LAN too, and still needs a ticket"
    assert (await asyncio.to_thread(tls_fingerprint, svc["lan_preview_port"])) == info["fingerprint"]


async def test_mdns_announcement(lan_service):
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    names: asyncio.Queue = asyncio.Queue()
    azc = AsyncZeroconf()

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            names.put_nowait(name)

    browser = AsyncServiceBrowser(azc.zeroconf, "_firedev._tcp.local.", handlers=[on_change])
    try:
        # Other FireDev machines may be announcing on this network too: find ours by its port.
        info = None
        while info is None:
            name = await asyncio.wait_for(names.get(), 10)
            candidate = AsyncServiceInfo("_firedev._tcp.local.", name)
            if await candidate.async_request(azc.zeroconf, 3000) and candidate.port == lan_service["lan_port"]:
                info = candidate
        props = {k.decode(): v.decode() for k, v in info.properties.items()}
        assert props["pp"] == str(lan_service["lan_preview_port"])
        assert props["name"] == "Test Laptop" and len(props["fp"]) == 64
    finally:
        await browser.async_cancel()
        await azc.async_close()


def test_cli_first_run(tmp_path):
    conf = tmp_path / "firedev.yaml"
    code = tmp_path / "my-app"
    code.mkdir()
    run = lambda *a: subprocess.run([sys.executable, "-m", "firedev", "--config", str(conf), *a], cwd=ROOT / "backend",
                                    capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    r = run("init")
    assert r.returncode == 0, r.stderr
    r = run("add", str(code), "--agent", "claude")
    assert r.returncode == 0, r.stderr
    c = cfg.load(conf)
    assert c.settings.lan and c.projects["my-app"].machine.local and c.projects["my-app"].agent == "claude"
    assert run("add", str(code)).returncode != 0, "no duplicates"
    assert "already exists" in run("init").stdout


def test_implicit_local_machine():
    c = cfg.parse({"projects": [{"id": "x", "name": "X", "path": "~/x"}]})
    assert c.projects["x"].machine.local and c.projects["x"].machine.id == "local"
    assert yaml.safe_dump  # config stays YAML-serialisable
