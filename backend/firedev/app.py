"""HTTP + WebSocket front door: the TV's WebSocket, the phone microphone page, REST for the CLI, MCP and Alexa."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import mcp
from .alexa import AlexaSkill
from .alexa_verify import AlexaVerifier, VerificationError
from .auth import PAIRING_TTL, Client, DeviceStore
from .config import Config
from .service import CommandError, FireDev

log = logging.getLogger("firedev.app")
ROOT = Path(__file__).resolve().parents[2]
TV_WEB = ROOT / "tv" / "web"
COMPANION = ROOT / "companion"
CLAIM_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")


def create_app(config: Config, devices: DeviceStore) -> FastAPI:
    service = FireDev(config, devices)
    skill = AlexaSkill(service, devices)
    verifier = AlexaVerifier(config.settings.alexa_skill_id)
    app = FastAPI(title="FireDev", docs_url=None, redoc_url=None)
    app.state.service = service
    pair_listeners: dict[str, set[asyncio.Event]] = {}  # pairing key -> TV connections showing that code

    def client_from(request: Request) -> Client:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token")
        client = devices.authenticate(token)
        if not client:
            raise HTTPException(401, "Pair this client first")
        return client

    @app.get("/")
    async def index():
        return RedirectResponse("tv/")  # relative, so it also works under a path prefix

    @app.get("/api/health")
    async def health():
        return {"ok": True, "projects": len(service.sessions), "version": "0.2.0", "ui_schema": "firedev.ui/1"}

    @app.post("/api/command")
    async def command(request: Request):
        client = client_from(request)
        body = await request.json()
        try:
            return await service.execute(body.get("name", ""), body.get("args") or {}, client)
        except CommandError as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)

    @app.get("/api/projects")
    async def projects(request: Request):
        client_from(request)
        return service.home()

    @app.get("/api/projects/{pid}")
    async def project(pid: str, request: Request):
        client_from(request)
        if pid not in service.sessions:
            raise HTTPException(404)
        return service.sessions[pid].snapshot()

    @app.post("/api/pair")
    async def pair(request: Request):
        client = client_from(request)
        if client.role != "admin":
            raise HTTPException(403, "Pairing needs the admin token")
        body = await request.json()
        approved = devices.approve_code(str(body.get("code", "")), body.get("name") or "Fire TV")
        if not approved:
            raise HTTPException(404, "No TV is showing that code. Codes last 10 minutes; check the TV for the current one.")
        dev, key = approved
        for ev in pair_listeners.get(key, ()):
            ev.set()
        return {"ok": True, "device": dev["id"], "name": dev["name"]}

    @app.get("/api/devices")
    async def list_devices(request: Request):
        if client_from(request).role != "admin":
            raise HTTPException(403)
        return {"devices": devices.list()}

    @app.post("/api/devices")
    async def create_device(request: Request):
        if client_from(request).role != "admin":
            raise HTTPException(403)
        body = await request.json()
        try:
            token, dev = devices.create(body.get("name") or "Device", body.get("role") or "assistant")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"token": token, "device": {k: v for k, v in dev.items() if k != "token_sha256"}}

    @app.delete("/api/devices/{device_id}")
    async def revoke_device(device_id: str, request: Request):
        if client_from(request).role != "admin":
            raise HTTPException(403)
        if not devices.revoke(device_id):
            raise HTTPException(404, "No device with that ID")
        return {"ok": True}

    @app.get("/api/qr.svg")
    async def qr(request: Request, data: str):
        client_from(request)
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), media_type="image/svg+xml")

    @app.post("/mcp")
    async def mcp_http(request: Request):
        client = client_from(request)
        body = await request.json()
        if isinstance(body, list):
            out = [r for r in [await mcp.handle(service, client, m) for m in body] if r is not None]
            return JSONResponse(out) if out else Response(status_code=202)
        res = await mcp.handle(service, client, body)
        return JSONResponse(res) if res is not None else Response(status_code=202)

    @app.post("/alexa")
    async def alexa_endpoint(request: Request):
        body = await request.body()
        if len(body) > 128 * 1024:
            raise HTTPException(400, "Bad Request")
        if request.headers.get("authorization"):
            # The phone page's simulated Alexa, with a FireDev token: the same power as /api/command.
            return await skill.handle_simulated(client_from(request), json.loads(body or b"{}"))
        try:
            req = await verifier.verify(request.headers, body)
        except VerificationError as e:
            log.warning("Refused an Alexa request: %s", e)
            raise HTTPException(400, "Bad Request") from None
        return await skill.handle_signed(req)

    @app.get("/voice")
    async def voice_page():
        return FileResponse(COMPANION / "voice.html", headers={"Cache-Control": "no-store"})

    app.mount("/companion", StaticFiles(directory=COMPANION), name="companion")
    app.mount("/tv", StaticFiles(directory=TV_WEB, html=True), name="tv")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        token = websocket.query_params.get("token")
        client = devices.authenticate(token)
        if client:
            devices.token_delivered(token)
        else:
            client, token = await _pair(websocket, websocket.query_params.get("pair"))
            if not client:
                return
        await _session(websocket, client)

    async def _pair(websocket: WebSocket, claim: str | None):
        # A TV that sends no claim (older builds) gets one for this connection only: its code ends with it.
        ephemeral = not (claim and CLAIM_RE.match(claim))
        if ephemeral:
            claim = secrets.token_urlsafe(24)
        token = devices.collect_token(claim)
        if token:  # approved while this TV was reconnecting
            await websocket.send_json({"type": "paired", "token": token})
            return devices.authenticate(token), token
        entry = devices.pairing(claim)
        if entry is None:
            await websocket.close(code=1013)  # too many TVs pairing at once: the TV retries with backoff
            return None, None
        approved = asyncio.Event()

        def attach(e):
            e["live"] += 1
            pair_listeners.setdefault(e["key"], set()).add(approved)

        def detach(e):
            e["live"] = max(0, e["live"] - 1)
            listeners = pair_listeners.get(e["key"])
            if listeners:
                listeners.discard(approved)
                if not listeners:
                    del pair_listeners[e["key"]]

        async def announce(e):
            await websocket.send_json({"type": "pairing", "code": e["code"], "ttl": PAIRING_TTL,
                                       "expires_in": max(0, round(e["expires"] - time.time()))})

        attach(entry)
        await announce(entry)
        recv = asyncio.create_task(websocket.receive_text())
        waiting = asyncio.create_task(approved.wait())
        expired_sent = False
        try:
            while True:
                timeout = None if expired_sent else max(0.0, entry["expires"] - time.time())
                done, _ = await asyncio.wait({recv, waiting}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if waiting in done:
                    token = devices.collect_token(claim)
                    if token:
                        await websocket.send_json({"type": "paired", "token": token})
                        return devices.authenticate(token), token
                    approved.clear()
                    waiting = asyncio.create_task(approved.wait())
                    continue
                if not done:  # the code ran out while the TV was showing it
                    expired_sent = True
                    await websocket.send_json({"type": "pairing_expired"})
                    continue
                try:
                    msg = json.loads(recv.result())
                except (WebSocketDisconnect, RuntimeError):
                    return None, None
                except ValueError:
                    msg = {}
                recv = asyncio.create_task(websocket.receive_text())
                op = msg.get("op") if isinstance(msg, dict) else None
                if op == "ping":  # the TV's heartbeat: unanswered, it would reconnect (and used to get a new code)
                    await websocket.send_json({"type": "pong"})
                elif op == "new_code":
                    detach(entry)
                    fresh = devices.renew_code(claim)
                    if fresh is None:
                        await websocket.close(code=1013)
                        return None, None
                    entry, expired_sent = fresh, False
                    attach(entry)
                    await announce(entry)
        finally:
            recv.cancel()
            waiting.cancel()
            detach(entry)
            if ephemeral:
                devices.drop_pairing(claim)

    async def _session(websocket: WebSocket, client: Client):
        q = service.bus.subscribe()
        await websocket.send_json({
            "type": "hello", "client": {"id": client.id, "name": client.name, "role": client.role},
            "home": service.home(), "projects": {pid: s.snapshot() for pid, s in service.sessions.items()},
            "approvals": service.pending_approvals(), "focus": service.focus_project, "ui": service.ui_snapshot(),
        })

        async def pump():
            while True:
                ev = await q.get()
                await websocket.send_text(json.dumps(ev.to_dict(), default=str))

        sender = asyncio.create_task(pump())
        try:
            while True:
                msg = json.loads(await websocket.receive_text())
                op = msg.get("op")
                if op == "cmd":
                    asyncio.create_task(_reply(websocket, client, msg))
                elif op == "voice":
                    phase = msg.get("phase", "final")
                    pid = msg.get("project") or service.focus_project
                    if phase == "listening":
                        service.bus.emit("voice_listening", pid, None, source=msg.get("source", client.role), active=True)
                    elif phase == "cancel":
                        service.bus.emit("voice_listening", pid, None, source=msg.get("source", client.role), active=False)
                    elif phase == "partial":
                        service.bus.emit("voice_partial", pid, None, text=msg.get("text", ""), source=msg.get("source", client.role))
                    else:
                        asyncio.create_task(_reply(websocket, client, {"id": msg.get("id"), "name": "voice_command",
                                                                         "args": {"text": msg.get("text", ""), "source": msg.get("source", client.role), "project": msg.get("project"),
                                                                                  "ui": msg.get("ui") if isinstance(msg.get("ui"), dict) else None}}))
                elif op == "ping":
                    await websocket.send_json({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sender.cancel()
            service.bus.unsubscribe(q)

    async def _reply(websocket: WebSocket, client: Client, msg: dict):
        try:
            result = await service.execute(msg.get("name", ""), msg.get("args") or {}, client)
            reply = {"type": "reply", "id": msg.get("id"), "ok": result.get("ok", True), "result": result}
        except CommandError as e:
            reply = {"type": "reply", "id": msg.get("id"), "ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("command %s failed", msg.get("name"))
            reply = {"type": "reply", "id": msg.get("id"), "ok": False, "error": f"FireDev error: {e}"}
        try:
            await websocket.send_text(json.dumps(reply, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app
