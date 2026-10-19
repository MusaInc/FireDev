"""The preview gateway: a separate origin (its own port) that shows a project's dev server to a paired TV.

    TV ── its connection to the user machine ── gateway :8132 ── SSH direct-tcpip ──▶ 127.0.0.1:<port> on the dev machine

- Only paired TVs get in: FireDev hands the TV a one-time ticket (90 s), the gateway trades it for an HttpOnly session
  cookie, and every request is checked against that session and the device that asked for it.
- It proxies to exactly one place: the port of the preview FireDev knows for that session's project. There is no way
  to name a host or URL, so it can't be used as an open proxy.
- It is its own origin, so the page (code the agent just wrote) can't read FireDev's token or drive FireDev's UI. The TV
  also frames it with a sandbox that blocks top-level navigation.
- HTML gets one script tag (/__firedev/tv.js) so the remote can move around the page and Back always returns to FireDev.
- HTTP streams both ways (SSE and long responses work), and WebSockets pass through, so hot reload (Vite, Next.js,
  webpack) works as it does on localhost. Host and Origin are rewritten to 127.0.0.1:<port>, which dev servers accept.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import posixpath
import re
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from .preview import PreviewManager
    from .session import ProjectSession

log = logging.getLogger("firedev.gateway")

COOKIE = "fdpv"
HELPER = Path(__file__).with_name("preview_tv.js")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers", "transfer-encoding",
       "upgrade", "host", "content-length"}
DROP_REQUEST = HOP | {"cookie", "origin", "referer", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip",
                      "forwarded", "via", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
                      "sec-websocket-protocol"}
MAX_HTML = 8 * 1024 * 1024


class Tunnels:
    """SSH port forwards from this machine to dev-server ports, one per (project connection, port), rebuilt after reconnects."""

    def __init__(self) -> None:
        self._t: dict[tuple[str, int], tuple[object, object, int, httpx.AsyncClient]] = {}
        self._lock = asyncio.Lock()

    async def local_port(self, s: "ProjectSession", port: int) -> tuple[int, httpx.AsyncClient]:
        r = s.remote
        if not r.connected:
            raise ConnectionError("The machine is reconnecting")
        key = (s.project.id, port)
        async with self._lock:
            cur = self._t.get(key)
            if cur and cur[0] == r.generation:
                return cur[2], cur[3]
            if cur:
                with contextlib.suppress(Exception):
                    if cur[1]:
                        cur[1].close()
                with contextlib.suppress(Exception):
                    await cur[3].aclose()
            lport, listener = await r.forward(port)
            client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{lport}", timeout=httpx.Timeout(15.0, read=None),
                                       follow_redirects=False, limits=httpx.Limits(max_connections=64, max_keepalive_connections=16))
            self._t[key] = (r.generation, listener, lport, client)
            return lport, client

    async def probe(self, s: "ProjectSession", port: int, timeout: float) -> int | None:
        try:
            _, client = await self.local_port(s, port)
            r = await asyncio.wait_for(client.get("/", headers={"host": f"127.0.0.1:{port}", "accept": "text/html,*/*"}), timeout)
            return r.status_code
        except Exception:  # noqa: BLE001 - anything but an answer means "not up"
            return None

    async def missing_styles(self, s: "ProjectSession", port: int, timeout: float) -> list[str] | None:
        """The page's own stylesheets that don't load yet (a page written before its CSS shows up as bare HTML).
        None if the page itself doesn't answer."""
        try:
            _, client = await self.local_port(s, port)
            h = {"host": f"127.0.0.1:{port}"}
            r = await asyncio.wait_for(client.get("/", headers={**h, "accept": "text/html,*/*"}), timeout)
            if r.status_code >= 400:
                return None
            if "html" not in r.headers.get("content-type", ""):
                return []
            missing = []
            for tag in re.findall(r"<link\b[^>]*>", r.text, re.I)[:12]:
                href = re.search(r"""href\s*=\s*["']?([^"' >]+)""", tag, re.I)
                if not re.search(r"""rel\s*=\s*["']?stylesheet""", tag, re.I) or not href:
                    continue
                url = html.unescape(href.group(1))
                if re.match(r"(?i)^([a-z][a-z0-9+.-]*:|//)", url):
                    continue  # fonts and other sites: not the agent's to write
                c = await asyncio.wait_for(client.get("/" + url.lstrip("./").split("#")[0], headers=h), timeout)
                if c.status_code >= 400:
                    missing.append(url)
            return missing
        except Exception:  # noqa: BLE001
            return None

    async def close(self) -> None:
        for _, listener, _, client in self._t.values():
            with contextlib.suppress(Exception):
                if listener:
                    listener.close()
            with contextlib.suppress(Exception):
                await client.aclose()
        self._t.clear()


def _page(title: str, body: str, signal: str, status: int, retry: bool = False) -> HTMLResponse:
    # Tells the FireDev TV around it what happened (it re-opens with a new ticket or shows its own status).
    html = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{_esc(title)}</title>{'<meta http-equiv="refresh" content="3">' if retry else ''}
<style>body{{margin:0;height:100vh;display:grid;place-items:center;background:#111114;color:#e8e8ea;font:28px system-ui,sans-serif;text-align:center}}
p{{color:#9a9aa3;font-size:22px;max-width:30em}}</style>
<div><div>{_esc(title)}</div><p>{_esc(body)}</p></div>
<script>try{{parent.postMessage({{fd:"{signal}"}},"*")}}catch(e){{}}</script>"""
    return HTMLResponse(html, status_code=status, headers={"cache-control": "no-store", "x-robots-tag": "noindex"})


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _cookie_sid(header: str | None) -> str | None:
    if not header:
        return None
    try:
        c = SimpleCookie()
        c.load(header)
    except Exception:  # noqa: BLE001
        m = re.search(rf"(?:^|;\s*){COOKIE}=([A-Za-z0-9_-]+)", header)
        return m.group(1) if m else None
    return c[COOKIE].value if COOKIE in c else None


def _strip_cookie(header: str | None) -> str | None:
    if not header:
        return None
    parts = [p for p in (x.strip() for x in header.split(";")) if p and not p.startswith(COOKIE + "=")]
    return "; ".join(parts) or None


def _inject(html: bytes) -> bytes:
    tag = b'<script src="/__firedev/tv.js"></script>'
    m = re.search(rb"<head[^>]*>", html[:4096], re.I)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    m = re.search(rb"<html[^>]*>", html[:4096], re.I)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    return tag + html


class Gateway:
    def __init__(self, manager: "PreviewManager") -> None:
        self.m = manager
        self.tunnels = Tunnels()
        manager.tunnels = self.tunnels

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            await self.http(Request(scope, receive), send)
        elif scope["type"] == "websocket":
            await self.ws(WebSocket(scope, receive, send))
        elif scope["type"] == "lifespan":
            while True:
                try:
                    msg = await receive()
                except asyncio.CancelledError:
                    return
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await self.tunnels.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    # --- HTTP -----------------------------------------------------------------------------------------------

    def _secure(self, req: Request) -> bool:
        """HTTPS, or a loopback address: the Fire TV app's own relay (http://127.0.0.1 inside the TV), which browsers
        treat as a secure context. Either way the cookie can be SameSite=None; Secure, which a frame next to the app's
        local-file interface needs."""
        if (req.headers.get("x-forwarded-proto") or req.url.scheme) == "https":
            return True
        host = (req.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _origin(self, req: Request) -> str:
        proto = req.headers.get("x-forwarded-proto") or req.url.scheme
        return f"{proto}://{req.headers.get('host', req.url.netloc)}"

    async def http(self, req: Request, send) -> None:
        resp = await self._http(req)
        await resp(req.scope, req.receive, send)

    async def _http(self, req: Request) -> Response:
        path = req.url.path
        if path == "/__firedev/enter":
            got = self.m.redeem(req.query_params.get("t", ""))
            if not got:
                return _page("This preview link has expired", "Open the preview again from FireDev on your TV.", "expired", 403)
            vs, to = got
            r = Response(status_code=303, headers={"location": to, "cache-control": "no-store"})
            secure = self._secure(req)
            # SameSite=None: the TV app's interface is a local file, so the preview frame is a third-party context there.
            r.set_cookie(COOKIE, vs.id, httponly=True, secure=secure, samesite="none" if secure else "lax", path="/", max_age=12 * 3600)
            return r
        if path == "/__firedev/tv.js":
            return Response(HELPER.read_text(), media_type="application/javascript", headers={"cache-control": "no-cache"})
        if path == "/__firedev/health":
            return Response("ok", media_type="text/plain")
        vs = self.m.session_for(_cookie_sid(req.headers.get("cookie")))
        if not vs:
            return _page("Open this preview from FireDev", "Previews are only shown to a paired FireDev TV.", "expired", 403)
        if path == "/__firedev/shots":
            p = self.m.previews[vs.pid]
            items = "".join(f'<a href="/__firedev/shot/{i}"><img src="/__firedev/shot/{i}" alt=""><span>{_esc(posixpath.basename(f))}</span></a>'
                            for i, f in enumerate(p.screenshots))
            return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>Screenshots</title><script src="/__firedev/tv.js"></script>
<style>body{{margin:0;padding:40px;background:#111114;color:#e8e8ea;font:22px system-ui,sans-serif}}h1{{font-size:30px;margin:0 0 8px}}
p{{color:#9a9aa3;margin:0 0 28px}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:24px}}
a{{color:inherit;text-decoration:none;background:#1b1b20;border-radius:14px;padding:12px;display:flex;flex-direction:column;gap:10px}}
img{{width:100%;height:420px;object-fit:contain;background:#000;border-radius:8px}}span{{font-size:18px;color:#bdbdc4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}</style>
<h1>Screenshots in the project</h1><p>Images the project's screenshot tests or store listing left in the repository. They are files, not the running app.</p>
<main>{items or '<p>No screenshots found.</p>'}</main>""", headers={"cache-control": "no-store"})
        if path.startswith("/__firedev/shot/"):
            try:
                got = await self.m.screenshot(vs.pid, int(path.rsplit("/", 1)[-1]))
            except (ValueError, Exception):  # noqa: BLE001
                got = None
            if not got:
                return Response(status_code=404)
            data, ctype = got
            return Response(data, media_type=ctype, headers={"cache-control": "no-store"})
        tgt = self.m.target(vs)
        if not tgt:
            return _page("The preview isn't running", "FireDev shows its status on the TV.", "down", 503, retry=True)
        s, port = tgt
        try:
            _, client = await self.tunnels.local_port(s, port)
        except Exception as e:  # noqa: BLE001
            return _page("Reconnecting to your machine…", str(e) or "The SSH connection dropped.", "down", 502, retry=True)
        origin = self._origin(req)
        up = f"http://127.0.0.1:{port}"
        headers = [(k, v) for k, v in req.headers.items() if k.lower() not in DROP_REQUEST]
        headers.append(("host", f"127.0.0.1:{port}"))
        cookie = _strip_cookie(req.headers.get("cookie"))
        if cookie:
            headers.append(("cookie", cookie))
        if req.headers.get("origin"):
            headers.append(("origin", up))
        ref = req.headers.get("referer")
        if ref and ref.startswith(origin):
            headers.append(("referer", up + ref[len(origin):]))
        document = req.headers.get("sec-fetch-dest") in ("document", "iframe") or (
            req.method == "GET" and "text/html" in (req.headers.get("accept") or ""))
        if document:
            headers = [(k, v) for k, v in headers if k.lower() != "accept-encoding"] + [("accept-encoding", "identity")]
        body = req.stream() if req.method not in ("GET", "HEAD") else None
        try:
            upstream = await client.send(client.build_request(req.method, req.url.path, params=req.url.query or None, headers=headers,
                                                             content=body), stream=True)
        except (httpx.HTTPError, OSError) as e:
            self.m.suspect(vs.pid)
            return _page("The preview server isn't answering", f"{type(e).__name__}: it may be restarting.", "down", 502, retry=True)
        out = self._response_headers(upstream, req, origin, up)
        ctype = upstream.headers.get("content-type", "")
        if self.m.previews[vs.pid].framework == "static":
            # A plain file server: always fresh, and UTF-8 unless it says otherwise (python's http.server never does).
            out = [(k, v) for k, v in out if k.lower() != "cache-control"] + [("cache-control", "no-store")]
            if ctype.startswith(("text/", "application/javascript")) and "charset" not in ctype:
                out = [(k, v) for k, v in out if k.lower() != "content-type"] + [("content-type", ctype + "; charset=utf-8")]
        if req.method != "HEAD" and "text/html" in ctype and upstream.status_code == 200 and \
                upstream.headers.get("content-encoding", "identity") in ("identity", ""):
            data = b""
            async for chunk in upstream.aiter_raw():
                data += chunk
                if len(data) > MAX_HTML:
                    break
            if len(data) <= MAX_HTML:
                await upstream.aclose()
                return _raw(Response(_inject(data), status_code=upstream.status_code), out)
            rest = upstream.aiter_raw()

            async def tail():
                yield data
                async for c in rest:
                    yield c
            return _raw(StreamingResponse(tail(), status_code=upstream.status_code, background=_Close(upstream)), out)
        return _raw(StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, background=_Close(upstream)), out)

    def _response_headers(self, upstream: httpx.Response, req: Request, origin: str, up: str) -> list[tuple[str, str]]:
        secure = self._secure(req)
        out = []
        for k, v in upstream.headers.multi_items():
            lk = k.lower()
            if lk in HOP or lk in ("x-frame-options",):
                continue
            if lk == "content-security-policy":
                v = "; ".join(d for d in v.split(";") if not d.strip().lower().startswith("frame-ancestors"))
                if not v.strip():
                    continue
            elif lk == "location":
                for base in (up, up.replace("127.0.0.1", "localhost"), f"http://localhost:{up.rsplit(':', 1)[1]}"):
                    if v.startswith(base):
                        v = v[len(base):] or "/"
                        break
            elif lk == "set-cookie":
                v = re.sub(r";\s*domain=[^;]*", "", v, flags=re.I)
                if secure:
                    v = re.sub(r";\s*samesite=[^;]*", "", v, flags=re.I) + "; SameSite=None"
                    if not re.search(r";\s*secure\b", v, re.I):
                        v += "; Secure"
            out.append((k, v))
        out.append(("x-robots-tag", "noindex"))
        return out

    # --- WebSockets (hot reload) ------------------------------------------------------------------------------

    async def ws(self, ws: WebSocket) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        vs = self.m.session_for(_cookie_sid(ws.headers.get("cookie")))
        tgt = self.m.target(vs) if vs else None
        if not tgt:
            await ws.close(code=4403)
            return
        s, port = tgt
        try:
            lport, _ = await self.tunnels.local_port(s, port)
        except Exception:  # noqa: BLE001
            await ws.close(code=1013)
            return
        qs = ws.url.query
        uri = f"ws://127.0.0.1:{lport}{ws.url.path}" + (f"?{qs}" if qs else "")
        extra = [(k, v) for k, v in ws.headers.items() if k.lower() not in DROP_REQUEST and not k.lower().startswith("sec-websocket")]
        cookie = _strip_cookie(ws.headers.get("cookie"))
        if cookie:
            extra.append(("cookie", cookie))
        protos = ws.scope.get("subprotocols") or None
        try:
            upstream = await connect(uri, subprotocols=protos, additional_headers=extra, origin=f"http://127.0.0.1:{port}",
                                     max_size=None, ping_interval=None, open_timeout=10, compression=None)
        except Exception as e:  # noqa: BLE001
            log.info("preview websocket to %s failed: %s", uri, e)
            await ws.close(code=1011)
            return
        await ws.accept(subprotocol=upstream.subprotocol)

        async def down():
            try:
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    else:
                        await ws.send_text(msg)
            except (ConnectionClosed, RuntimeError, WebSocketDisconnect):
                pass

        async def up_():
            try:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send(msg["text"])
            except (ConnectionClosed, RuntimeError, WebSocketDisconnect):
                pass

        tasks = [asyncio.create_task(down()), asyncio.create_task(up_())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        code = upstream.close_code or 1000
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close(code=code if 1000 <= code < 5000 and code not in (1005, 1006, 1015) else 1000)


async def serve_gateway(gateway: Gateway, host: str, port: int):
    """Runs the gateway on its own port inside FireDev's event loop. A port that's taken only disables previews."""
    import uvicorn

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # the main server owns the signals
            yield

        def install_signal_handlers(self) -> None:
            pass

    server = _Server(uvicorn.Config(gateway, host=host, port=port, log_level="warning", lifespan="on", ws_ping_interval=20,
                                    ws_ping_timeout=20, proxy_headers=False, server_header=False, ws_max_size=64 * 1024 * 1024))
    async def run():
        try:
            await server.serve()
        except SystemExit:  # uvicorn exits when it can't bind; that must not stop FireDev itself
            pass

    task = asyncio.get_running_loop().create_task(run())
    for _ in range(100):
        if server.started or task.done():
            break
        await asyncio.sleep(0.05)
    if task.done() or not server.started:
        log.warning("The preview gateway couldn't listen on %s:%s; live previews are off", host, port)
        return None
    log.info("Preview gateway on http://%s:%s", host, port)
    return server


def _raw(resp: Response, headers: list[tuple[str, str]]) -> Response:
    """Sets the upstream's headers as they are (repeated Set-Cookie included), keeping only the length Starlette computed."""
    keep = [h for h in resp.raw_headers if h[0] == b"content-length"]
    resp.raw_headers = keep + [(k.lower().encode("latin-1"), v.encode("latin-1", "replace")) for k, v in headers]
    return resp


class _Close:
    def __init__(self, r: httpx.Response) -> None:
        self.r = r

    async def __call__(self) -> None:
        await self.r.aclose()
