"""SSH connections to development machines (asyncssh), shared by every project on the same machine."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from collections.abc import AsyncIterator
from pathlib import Path

import asyncssh

from .config import Machine

log = logging.getLogger("firedev.ssh")


class RemoteError(RuntimeError):
    pass


class Remote:
    def __init__(self, machine: Machine) -> None:
        self.machine = machine
        self.conn: asyncssh.SSHClientConnection | None = None
        self.home: str = ""
        self._lock = asyncio.Lock()
        self._lost = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self.conn is not None and not self._lost.is_set()

    def _options(self) -> dict:
        m = self.machine
        opts: dict = {
            "host": m.host, "port": m.port, "username": m.user,
            "keepalive_interval": 10, "keepalive_count_max": 3, "connect_timeout": 12,
        }
        if m.known_hosts:
            kh = Path(m.known_hosts).expanduser()
            if not kh.exists():
                raise RemoteError(f"known_hosts file {kh} not found; connect once with ssh to record the host key")
            opts["known_hosts"] = str(kh)
        else:
            opts["known_hosts"] = None
        if m.auth == "key":
            if not m.key_file:
                raise RemoteError(f"machine {m.id}: key auth needs key_file")
            opts["client_keys"] = [str(Path(m.key_file).expanduser())]
            opts["agent_path"] = None
        elif m.auth == "agent":
            opts["client_keys"] = None
        elif m.auth == "password":
            pw = os.environ.get(m.password_env or "")
            if not pw:
                raise RemoteError(f"machine {m.id}: environment variable {m.password_env} is not set")
            opts["password"] = pw
            opts["client_keys"] = None
            opts["agent_path"] = None
        return opts

    async def connect(self) -> None:
        async with self._lock:
            if self.connected:
                return
            try:
                conn = await asyncssh.connect(**self._options())
            except (OSError, asyncssh.Error) as e:
                raise RemoteError(_friendly(e)) from e
            self.conn = conn
            self._lost = asyncio.Event()
            asyncio.get_running_loop().create_task(self._watch(conn))
            res = await conn.run("printf %s \"$HOME\"", check=False)
            self.home = str(res.stdout or "").strip()

    async def _watch(self, conn: asyncssh.SSHClientConnection) -> None:
        try:
            await conn.wait_closed()
        finally:
            if self.conn is conn:
                self._lost.set()
                self.conn = None

    async def wait_lost(self) -> None:
        await self._lost.wait()

    def drop(self) -> None:
        """Closes the connection (used by tests to simulate a network drop)."""
        if self.conn:
            self.conn.abort()

    async def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def expand(self, path: str) -> str:
        if path == "~" or path.startswith("~/"):
            return self.home + path[1:]
        return path

    async def run(self, command: str, *, input: str | None = None, timeout: float = 30, check: bool = False) -> tuple[int, str, str]:
        if not self.connected:
            raise RemoteError("not connected")
        try:
            res = await asyncio.wait_for(self.conn.run(command, input=input, check=False), timeout)
        except asyncio.TimeoutError as e:
            raise RemoteError(f"timed out: {command[:80]}") from e
        except (OSError, asyncssh.Error) as e:
            raise RemoteError(_friendly(e)) from e
        code = res.exit_status if res.exit_status is not None else -1
        out, err = str(res.stdout or ""), str(res.stderr or "")
        if check and code != 0:
            raise RemoteError(f"{command[:80]} failed ({code}): {err.strip()[:300] or out.strip()[:300]}")
        return code, out, err

    async def stream(self, command: str) -> AsyncIterator[str]:
        """Yields stdout chunks of a long-running remote command until it exits or the connection drops."""
        if not self.connected:
            raise RemoteError("not connected")
        try:
            proc = await self.conn.create_process(command, encoding="utf-8", errors="replace")
        except (OSError, asyncssh.Error) as e:
            raise RemoteError(_friendly(e)) from e
        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        except (OSError, asyncssh.Error) as e:
            raise RemoteError(_friendly(e)) from e
        finally:
            if not proc.is_closing():
                proc.close()

    async def write_file(self, path: str, content: str, mode: str | None = None) -> None:
        cmd = f"mkdir -p {shlex.quote(os.path.dirname(path))} && cat > {shlex.quote(path)}.tmp && mv {shlex.quote(path)}.tmp {shlex.quote(path)}"
        if mode:
            cmd += f" && chmod {mode} {shlex.quote(path)}"
        await self.run(cmd, input=content, check=True)


def _friendly(e: BaseException) -> str:
    if isinstance(e, asyncssh.PermissionDenied):
        return "SSH login was refused. Check the user name and that the key is in authorized_keys."
    if isinstance(e, asyncssh.HostKeyNotVerifiable):
        return "The machine's host key isn't trusted. Connect once with ssh to record it in known_hosts."
    if isinstance(e, ConnectionRefusedError):
        return "Connection refused. Is SSH running on that port?"
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        return "The machine didn't answer in time."
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(e) or e.__class__.__name__
