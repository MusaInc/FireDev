"""LAN mode: a TV on the same Wi-Fi finds this machine and connects to it directly, with nothing hosted anywhere.

- The machine announces itself with mDNS (`_firedev._tcp.local.`), with the TLS certificate's fingerprint and its ports
  in the TXT record.
- FireDev listens on the LAN with TLS only, using a self-signed certificate made on first start
  (~/.config/firedev/tls). The TV app pins the certificate the first time it connects.
- The pinned certificate is checked by pairing itself: the code the TV shows is derived from the fingerprint it saw
  (auth.bound_code), and `firedev pair` only accepts the code derived from this machine's real certificate.
- The TV app relays the interface's traffic from 127.0.0.1 inside the TV to these ports (see the Fire TV shell), so the
  same API serves LAN TVs, SSH-tunnelled TVs and the CLI.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import socket
from pathlib import Path

log = logging.getLogger("firedev.lan")
SERVICE = "_firedev._tcp.local."


def ensure_cert(home: Path) -> tuple[Path, Path, str]:
    """The machine's TLS certificate and key (made once), and the certificate's SHA-256 fingerprint (hex)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    d = home / "tls"
    cert, key = d / "cert.pem", d / "key.pem"
    if not (cert.exists() and key.exists()):
        d.mkdir(parents=True, exist_ok=True)
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"FireDev on {socket.gethostname()}"[:64])])
        now = dt.datetime.now(dt.timezone.utc)
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
             .not_valid_after(now + dt.timedelta(days=3650)).sign(k, hashes.SHA256()))
        key.touch(mode=0o600)
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key, fingerprint(cert)


def fingerprint(cert_pem: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    der = x509.load_pem_x509_certificate(cert_pem.read_bytes()).public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine other than loopback (the ones a TV on the LAN could reach)."""
    out: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for the default route, which getaddrinfo often misses
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET: nothing is sent
        out.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in out if not a.startswith("127."))


class Announcer:
    """mDNS announcement of this FireDev machine while the service runs."""

    def __init__(self, name: str, port: int, preview_port: int, fp: str, machine_id: str) -> None:
        self.name, self.port, self.preview_port, self.fp, self.machine_id = name, port, preview_port, fp, machine_id
        self.zc = None
        self.info = None

    async def start(self) -> None:
        try:
            from zeroconf import IPVersion
            from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
        except ImportError:
            log.warning("zeroconf isn't installed: TVs won't find this machine by themselves (pip install zeroconf)")
            return
        addrs = lan_addresses()
        if not addrs:
            log.warning("No LAN address: TVs can't find this machine")
            return
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "-" for ch in self.name)[:50] or "FireDev"
        self.info = AsyncServiceInfo(
            SERVICE, f"{safe}.{SERVICE}", port=self.port, addresses=[socket.inet_aton(a) for a in addrs],
            properties={"v": "1", "name": self.name[:60], "id": self.machine_id, "fp": self.fp, "pp": str(self.preview_port)},
            server=f"{socket.gethostname().split('.')[0]}-firedev.local.")
        self.zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        try:
            await self.zc.async_register_service(self.info, allow_name_change=True)
            log.info("Announced on the LAN as %r (%s, port %s)", self.name, ", ".join(addrs), self.port)
        except Exception as e:  # noqa: BLE001 - discovery is a convenience; the address still works
            log.warning("mDNS announcement failed: %s", e)

    async def stop(self) -> None:
        if self.zc:
            try:
                if self.info:
                    await asyncio.wait_for(self.zc.async_unregister_service(self.info), 3)
                await self.zc.async_close()
            except Exception:  # noqa: BLE001
                pass


async def serve_tls(app, host: str, port: int, cert: Path, key: Path, label: str):
    """Runs an ASGI app on its own TLS port inside FireDev's event loop (lifespan off: the main server owns it)."""
    import uvicorn

    class _Server(uvicorn.Server):
        def capture_signals(self):  # noqa: D401 - the main server owns the signals
            import contextlib
            return contextlib.nullcontext()

        def install_signal_handlers(self) -> None:
            pass

    server = _Server(uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off", ssl_certfile=str(cert),
                                    ssl_keyfile=str(key), ws_ping_interval=20, ws_ping_timeout=20, server_header=False))

    async def run():
        try:
            await server.serve()
        except SystemExit:
            pass

    task = asyncio.get_running_loop().create_task(run())
    for _ in range(100):
        if server.started or task.done():
            break
        await asyncio.sleep(0.05)
    if not server.started:
        log.warning("LAN %s couldn't listen on %s:%s", label, host, port)
        return None
    log.info("LAN %s on https://%s:%s", label, host, port)
    return server
