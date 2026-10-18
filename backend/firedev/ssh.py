"""Connections to development machines: SSH (asyncssh) to another machine, or LocalRemote when FireDev runs on the
machine itself (the local-first setup: `firedev serve` next to the code, no SSH to itself)."""

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
        self.generation = 0  # bumps on every new connection, so port forwards know to rebuild

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
            self.generation += 1
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

    async def read_bytes(self, path: str, limit: int = 8_000_000) -> bytes:
        if not self.connected:
            raise RemoteError("not connected")
        res = await self.conn.run(f"head -c {int(limit)} {shlex.quote(path)}", encoding=None, check=False)
        return bytes(res.stdout or b"")

    async def forward(self, port: int) -> tuple[int, object]:
        """A local port that reaches 127.0.0.1:<port> on the machine, and the listener to close it."""
        if not self.connected:
            raise RemoteError("not connected")
        listener = await self.conn.forward_local_port("127.0.0.1", 0, "127.0.0.1", port)
        return listener.get_port(), listener

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


class LocalRemote:
    """The machine FireDev itself runs on: the same interface as Remote, with local processes instead of SSH."""

    def __init__(self, machine: Machine) -> None:
        self.machine = machine
        self.home = str(Path.home())
        self.generation = 0
        self._up = False
        self._lost = asyncio.Event()
        self._procs: set[asyncio.subprocess.Process] = set()

    @property
    def connected(self) -> bool:
        return self._up and not self._lost.is_set()

    @property
    def conn(self):
        return self if self.connected else None

    async def connect(self) -> None:
        if self.connected:
            return
        self._lost = asyncio.Event()
        self._up = True
        self.generation += 1

    async def wait_lost(self) -> None:
        await self._lost.wait()

    def drop(self) -> None:
        """Simulates a lost connection (tests): running streams end, and the session reconnects."""
        self._lost.set()
        self._up = False
        for p in list(self._procs):
            if p.returncode is None:
                p.kill()

    async def close(self) -> None:
        self._up = False
        for p in list(self._procs):
            if p.returncode is None:
                p.kill()

    def expand(self, path: str) -> str:
        if path == "~" or path.startswith("~/"):
            return self.home + path[1:]
        return path

    async def _spawn(self, command: str, stdin: bool) -> asyncio.subprocess.Process:
        if not self.connected:
            raise RemoteError("not connected")
        p = await asyncio.create_subprocess_exec("bash", "-c", command, cwd=self.home, start_new_session=True,
                                                 stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self._procs.add(p)
        return p

    async def run(self, command: str, *, input: str | None = None, timeout: float = 30, check: bool = False) -> tuple[int, str, str]:
        p = await self._spawn(command, input is not None)
        try:
            out, err = await asyncio.wait_for(p.communicate(input.encode() if input is not None else None), timeout)
        except asyncio.TimeoutError as e:
            p.kill()
            raise RemoteError(f"timed out: {command[:80]}") from e
        finally:
            self._procs.discard(p)
        code = p.returncode if p.returncode is not None else -1
        o, e2 = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        if check and code != 0:
            raise RemoteError(f"{command[:80]} failed ({code}): {e2.strip()[:300] or o.strip()[:300]}")
        return code, o, e2

    async def stream(self, command: str) -> AsyncIterator[str]:
        p = await self._spawn(command, False)
        dec = __import__("codecs").getincrementaldecoder("utf-8")("replace")
        try:
            while True:
                chunk = await p.stdout.read(8192)
                if not chunk:
                    break
                text = dec.decode(chunk)
                if text:
                    yield text
        finally:
            self._procs.discard(p)
            if p.returncode is None:
                try:
                    p.kill()
                except ProcessLookupError:
                    pass

    async def read_bytes(self, path: str, limit: int = 8_000_000) -> bytes:
        with open(self.expand(path), "rb") as f:
            return f.read(limit)

    async def forward(self, port: int) -> tuple[int, object]:
        return port, None  # the dev server is on this machine already

    async def write_file(self, path: str, content: str, mode: str | None = None) -> None:
        p = Path(self.expand(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(content)
        if mode:
            os.chmod(tmp, int(mode, 8))
        tmp.replace(p)


def make_remote(machine: Machine):
    return LocalRemote(machine) if machine.local else Remote(machine)
