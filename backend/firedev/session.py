"""A project session: one SSH connection, one tmux session on the remote machine, and everything that runs in it.

Every process FireDev starts (an agent turn, a test run, a build, an approved command) is a *run*: a tmux
window in the project's session whose output goes to ~/.firedev/projects/<id>/runs/<run>/out.log and whose
exit code lands next to it. Runs keep going when the TV sleeps, the network drops or FireDev restarts;
FireDev follows the log from where it left off and turns it into structured events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import random
import re
import shlex
import string
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import parsers, risk
from .agents import make_adapter
from .config import Action, Project
from .ssh import Remote, RemoteError

if TYPE_CHECKING:
    from .service import FireDev

log = logging.getLogger("firedev.session")

HELPERS = Path(__file__).resolve().parents[2] / "remote" / "bin"
TAIL_BYTES = 48_000
ACTIVITY_LIMIT = 160


def _rid(prefix: str) -> str:
    return prefix + time.strftime("%H%M%S") + "".join(random.choices(string.ascii_lowercase + string.digits, k=4))


@dataclass
class Run:
    id: str
    kind: str  # agent | action | command
    title: str
    command: str
    level: int
    source: str  # voice | tv | agent | assistant
    task_id: str | None
    dir: str
    action: Action | None = None
    request_id: str | None = None
    offset: int = 0
    exit_code: int | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    tail: deque = field(default_factory=lambda: deque(maxlen=400))
    line_buf: str = ""
    parser: object | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    pending_output: str = ""
    last_output_emit: float = 0.0
    activity_id: str | None = None

    def output(self) -> str:
        return "".join(self.tail)[-TAIL_BYTES:]


@dataclass
class Task:
    id: str
    title: str
    kind: str  # agent | action | command
    text: str = ""
    prompt: str = ""
    mode: str = "change"
    action_id: str | None = None
    command: str | None = None
    source: str = "voice"
    status: str = "queued"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    summary: str | None = None
    error: str | None = None
    status_text: str = "Waiting to start"
    plan: list = field(default_factory=list)
    run_ids: list = field(default_factory=list)
    base_ref: str | None = None
    files: dict = field(default_factory=dict)
    agent_error: str | None = None
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "text": self.text, "mode": self.mode, "source": self.source,
            "status": self.status, "created": self.created, "started": self.started, "finished": self.finished,
            "summary": self.summary, "error": self.error, "status_text": self.status_text, "plan": self.plan,
            "files": [{"path": p, "change": c} for p, c in self.files.items()], "action": self.action_id,
            "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else 0,
        }


class ProjectSession:
    def __init__(self, service: "FireDev", project: Project) -> None:
        self.service = service
        self.bus = service.bus
        self.project = project
        self.remote = Remote(project.machine)
        self.adapter = make_adapter(project.agent, project.agent_options)
        self.conn_state = "disconnected"
        self.conn_message = ""
        self.conn_since = time.time()
        self.connected_evt = asyncio.Event()
        self.path = project.path
        self.state_dir = ""
        self.queue_dir = ""
        self.agent_state = {"id": self.adapter.id, "name": self.adapter.display, "state": "offline", "version": None, "thread_id": None}
        self.tasks: deque[Task] = deque(maxlen=30)
        self.pending: deque[Task] = deque()
        self.current: Task | None = None
        self.runs: dict[str, Run] = {}
        self.build: dict = {"state": "unknown"}
        self.tests: dict = {"state": "unknown"}
        self.deploy: dict = {"state": "unknown"}
        self.last_command: dict | None = None
        self.changes: dict = {"files": [], "added": 0, "removed": 0}
        self.activity: deque[dict] = deque(maxlen=ACTIVITY_LIMIT)
        self.raw: dict[str, str] = {}
        self.last_agent_task: Task | None = None
        self._wake = asyncio.Event()
        self._bg: set[asyncio.Task] = set()
        self._opening: asyncio.Task | None = None
        self._closed = False
        self._watching = False

    # --- helpers ------------------------------------------------------------------------------------------

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        t.add_done_callback(_log_failure)
        return t

    def emit(self, type_: str, task_id: str | None = None, /, **data) -> None:
        self.bus.emit(type_, self.project.id, task_id, **data)
        self.service.state_changed(self.project.id)

    def add_activity(self, kind: str, title: str, *, detail: str = "", level: int | None = None, status: str = "done",
                     ref: str | None = None, task: str | None = None) -> dict:
        item = {"id": _rid("a"), "ts": time.time(), "kind": kind, "title": title, "detail": detail, "level": level,
                "status": status, "ref": ref, "task": task, "has_output": bool(ref)}
        self.activity.append(item)
        self.service.state_changed(self.project.id)
        return item

    def update_activity(self, item_id: str | None, **changes) -> None:
        for item in reversed(self.activity):
            if item["id"] == item_id:
                item.update(changes)
                self.service.state_changed(self.project.id)
                return

    def set_status(self, text: str) -> None:
        if self.current and self.current.status_text != text:
            self.current.status_text = text
            self.emit("agent_status", self.current.id, text=text)

    def _set_conn(self, state: str, message: str = "") -> None:
        self.conn_state, self.conn_message, self.conn_since = state, message, time.time()
        if state == "connected":
            self.connected_evt.set()
        else:
            self.connected_evt.clear()
        self.service.state_changed(self.project.id)

    @property
    def busy(self) -> bool:
        return self.current is not None

    # --- connecting ---------------------------------------------------------------------------------------

    async def open(self) -> None:
        """Connects and prepares the project, or returns at once if that is already done or underway."""
        if self.conn_state == "connected":
            return
        if self._opening and not self._opening.done():
            await asyncio.shield(self._opening)
            return
        self._closed = False
        self._opening = asyncio.get_running_loop().create_task(self._open())
        await asyncio.shield(self._opening)

    async def _open(self) -> None:
        self._set_conn("connecting", f"Connecting to {self.project.machine.display}")
        self.emit("session_connecting", machine=self.project.machine.display)
        try:
            await self._connect_and_prepare(first=True)
        except RemoteError as e:
            self._set_conn("error", str(e))
            self.agent_state["state"] = "offline"
            self.emit("session_error", message=str(e))
            raise
        self._spawn(self._supervise())
        self._spawn(self._worker())

    async def _connect_and_prepare(self, first: bool) -> None:
        r = self.remote
        await r.connect()
        self.path = r.expand(self.project.path)
        self.state_dir = f"{r.home}/.firedev/projects/{self.project.id}"
        self.queue_dir = f"{self.path}/.firedev/queue"
        self._set_conn("checking", "Checking the project")
        code, out, _ = await r.run(f"cd {shlex.quote(self.path)} && pwd && (git rev-parse --is-inside-work-tree 2>/dev/null || echo no-git)")
        if code != 0:
            raise RemoteError(f"Project folder {self.project.path} doesn't exist on {self.project.machine.display}")
        self.is_git = "true" in out
        await self._install_helpers()
        created = await self._ensure_tmux()
        if created and self.project.startup:
            await r.run(f"tmux send-keys -t {self._t('shell')} {shlex.quote(self.project.startup)} Enter")
        self._set_conn("starting_agent", f"Starting {self.adapter.display}")
        await self._check_agent()
        self._set_conn("connected", f"{self.project.machine.display}")
        self.emit("session_connected", machine=self.project.machine.display, tmux=self.project.tmux_session,
                  path=self.path, agent=self.agent_state, reattached=not first, created_session=created)
        await self._reattach_runs()
        await self.refresh_changes()
        self._spawn(self._watch_queue())

    def _t(self, window: str) -> str:
        return shlex.quote(f"={self.project.tmux_session}:{window}")

    async def _install_helpers(self) -> None:
        r = self.remote
        for f in sorted(HELPERS.iterdir()):
            await r.write_file(f"{r.home}/.firedev/bin/{f.name}", f.read_text(), mode="755")
        lines = [f"{a.id:<16} {a.title}  (level {a.level}{', needs approval on the TV' if a.level >= 2 else ''})"
                 for a in self.project.actions.values()]
        await r.write_file(f"{self.state_dir}/actions.txt", "\n".join(lines) + "\n")
        await r.run(f"mkdir -p {shlex.quote(self.queue_dir)} {shlex.quote(self.state_dir)}/runs")
        if self.is_git:
            # Keep FireDev's queue out of git without touching tracked files.
            await r.run(f"cd {shlex.quote(self.path)} && ex=$(git rev-parse --git-path info/exclude) && mkdir -p \"$(dirname \"$ex\")\" "
                        f"&& (grep -qxF '.firedev/' \"$ex\" 2>/dev/null || echo '.firedev/' >> \"$ex\")")
        await self._load_agent_state()

    async def _ensure_tmux(self) -> bool:
        s = shlex.quote(self.project.tmux_session)
        code, _, _ = await self.remote.run(f"tmux has-session -t ={s} 2>/dev/null")
        if code == 0:
            return False
        await self.remote.run(f"tmux new-session -d -s {s} -n shell -c {shlex.quote(self.path)}", check=True)
        await self.remote.run(f"tmux set-option -t ={s} remain-on-exit off", check=False)
        return True

    async def _check_agent(self) -> None:
        code, out, err = await self.remote.run(f"bash -lc {shlex.quote(self.adapter.check_command())}", timeout=20)
        if code == 0:
            self.agent_state.update(state="ready" if not self.busy else "working", version=(out.strip().splitlines() or [""])[-1][:60])
        else:
            self.agent_state.update(state="unavailable", version=None)
            self.add_activity("warning", f"{self.adapter.display} isn't installed on {self.project.machine.display}",
                              detail=(err or out).strip()[:200], level=None)

    async def _load_agent_state(self) -> None:
        code, out, _ = await self.remote.run(f"cat {shlex.quote(self.state_dir)}/agent.json 2>/dev/null")
        if code == 0:
            try:
                data = json.loads(out)
                if data.get("agent") == self.adapter.id:
                    self.agent_state["thread_id"] = data.get("thread_id")
            except json.JSONDecodeError:
                pass

    async def _save_agent_state(self) -> None:
        await self.remote.write_file(f"{self.state_dir}/agent.json", json.dumps({"agent": self.adapter.id, "thread_id": self.agent_state["thread_id"]}))

    async def _supervise(self) -> None:
        """Reconnects after the SSH connection drops, then resumes following every live run."""
        while not self._closed:
            await self.remote.wait_lost()
            if self._closed:
                return
            self._set_conn("reconnecting", "Connection lost. Reconnecting…")
            self.emit("session_disconnected", reason="connection lost", reconnecting=True)
            delay = 1.0
            while not self._closed:
                await asyncio.sleep(delay)
                try:
                    await self._connect_and_prepare(first=False)
                    break
                except RemoteError as e:
                    self._set_conn("reconnecting", f"Reconnecting… ({e})")
                    delay = min(delay * 2, 10)

    async def close(self) -> None:
        self._closed = True
        for t in list(self._bg):
            t.cancel()
        await self.remote.close()
        self._set_conn("disconnected")
        self.emit("session_disconnected", reason="closed", reconnecting=False)

    # --- runs ---------------------------------------------------------------------------------------------

    async def start_run(self, *, kind: str, title: str, command: str, level: int, source: str, task: Task | None,
                        action: Action | None = None, request_id: str | None = None, prompt: str | None = None) -> Run:
        await self.connected_evt.wait()
        run_id = _rid(kind[0])
        d = f"{self.state_dir}/runs/{run_id}"
        run = Run(id=run_id, kind=kind, title=title, command=command, level=level, source=source,
                  task_id=task.id if task else None, dir=d, action=action, request_id=request_id)
        if kind == "agent":
            run.parser = self.adapter.new_parser()
        r = self.remote
        if prompt is not None:
            await r.write_file(f"{d}/prompt.txt", prompt)
        meta = {"id": run_id, "kind": kind, "title": title, "command": command, "level": level, "source": source,
                "task": task.to_dict() if task else None, "action": action.id if action else None, "request_id": request_id}
        await r.write_file(f"{d}/meta.json", json.dumps(meta))
        q = shlex.quote
        script = "\n".join([
            "#!/usr/bin/env bash",
            f"cd {q(self.path)} || {{ echo 'project folder is missing' > {q(d)}/out.log; echo 127 > {q(d)}/exit_code; exit; }}",
            f"export PATH=\"$HOME/.firedev/bin:$PATH\" FIREDEV_QUEUE={q(self.queue_dir)} FIREDEV_PROJECT={q(self.project.id)} FIREDEV_STATE={q(self.state_dir)}",
            f"date +%s > {q(d)}/started",
            f"( {command}\n) 2>&1 | tee -a {q(d)}/out.log",
            "code=${PIPESTATUS[0]}",
            f"echo $code > {q(d)}/exit_code.tmp && mv {q(d)}/exit_code.tmp {q(d)}/exit_code",
            "",
        ])
        await r.write_file(f"{d}/cmd.sh", script, mode="755")
        # The session may have been killed by hand (or by `firedev demo reset`) since we connected.
        await self._ensure_tmux()
        await r.run(f"tmux new-window -d -t ={q(self.project.tmux_session)}: -n {q(run_id)} -c {q(self.path)} 'bash -l {d}/cmd.sh'", check=True)
        await r.write_file(f"{d}/window", f"{self.project.tmux_session} {run_id}\n")
        self.runs[run_id] = run
        if task:
            task.run_ids.append(run_id)
        self._begin_run_events(run)
        self._spawn(self._follow(run))
        return run

    def _begin_run_events(self, run: Run) -> None:
        tid = run.task_id
        if run.kind == "agent":
            self.agent_state["state"] = "working"
            self.emit("agent_started", tid, agent=self.adapter.display, thread_id=self.agent_state["thread_id"], run=run.id)
            return
        lvl = risk.LEVEL_NAMES[run.level]
        kind = run.action.kind if run.action else "command"
        item = self.add_activity("command", run.title, detail=run.command, level=run.level, status="running", ref=run.id, task=tid)
        run.activity_id = item["id"]
        self.last_command = {"command": run.command, "title": run.title, "level": run.level, "level_name": lvl, "running": True,
                             "exit_code": None, "source": run.source, "run": run.id, "started": run.started}
        self.emit("command_started", tid, run=run.id, command=run.command, title=run.title, level=run.level, source=run.source)
        if kind == "tests":
            self.tests = {"state": "running", "started": run.started}
            self.emit("tests_started", tid, run=run.id)
        elif kind == "build":
            self.build = {"state": "running", "started": run.started, "title": run.title}
            self.emit("build_started", tid, run=run.id)
        elif kind == "deploy":
            self.deploy = {"state": "running", "started": run.started, "title": run.title}
            self.emit("deploy_started", tid, run=run.id)

    async def _follow(self, run: Run) -> None:
        q = shlex.quote
        while True:
            try:
                await self.connected_evt.wait()
                async for chunk in self.remote.stream(f"{q(self.remote.home)}/.firedev/bin/firedev-follow {q(run.dir)} {run.offset}"):
                    run.offset += len(chunk.encode("utf-8", "replace"))
                    self._on_output(run, chunk)
                code, out, _ = await self.remote.run(f"cat {q(run.dir)}/exit_code 2>/dev/null")
                if code == 0 and out.strip():
                    run.exit_code = int(out.strip()) if out.strip().lstrip("-").isdigit() else 1
                    break
                if not self.remote.connected:
                    continue
                await asyncio.sleep(0.5)
            except RemoteError:
                if self._closed:
                    return
                await asyncio.sleep(0.5)
        if run.line_buf:
            self._on_line(run, run.line_buf)
            run.line_buf = ""
        self._flush_output(run, force=True)
        run.finished = time.time()
        await self._end_run(run)
        run.done.set()

    def _on_output(self, run: Run, chunk: str) -> None:
        run.tail.append(chunk)
        if run.kind == "agent":
            data = run.line_buf + chunk
            lines = data.split("\n")
            run.line_buf = lines.pop()
            for line in lines:
                self._on_line(run, line)
        else:
            run.pending_output += chunk
            self._flush_output(run)

    def _flush_output(self, run: Run, force: bool = False) -> None:
        now = time.time()
        if run.pending_output and (force or now - run.last_output_emit > 0.25):
            text = parsers.strip_ansi(run.pending_output)[-4000:]
            run.pending_output = ""
            run.last_output_emit = now
            if self.last_command and self.last_command.get("run") == run.id:
                lines = [ln for ln in text.splitlines() if ln.strip()]
                if lines:
                    self.last_command["latest"] = lines[-1][:200]
            self.bus.emit("command_output", self.project.id, run.task_id, run=run.id, text=text)
            self.service.state_changed(self.project.id)

    # --- agent output -------------------------------------------------------------------------------------

    def _on_line(self, run: Run, line: str) -> None:
        if not run.parser:
            return
        task = self._task(run.task_id)
        for ev in run.parser.feed(line):
            self._on_agent_event(run, task, ev)

    def _on_agent_event(self, run: Run, task: Task | None, ev: dict) -> None:
        k = ev["kind"]
        tid = task.id if task else None
        if k == "thread":
            if ev.get("thread_id") and ev["thread_id"] != self.agent_state["thread_id"]:
                self.agent_state["thread_id"] = ev["thread_id"]
                self._spawn(self._save_agent_state())
        elif k == "message":
            text = ev["text"].strip()
            if task:
                task.summary = text
            self.add_activity("agent", text.split("\n")[0][:160], detail=text, task=tid)
            self.emit("agent_message", tid, text=text, agent=self.adapter.display)
        elif k == "thinking":
            self.set_status("Thinking…")
        elif k == "plan":
            if task:
                task.plan = ev["items"]
            self.emit("agent_plan", tid, items=ev["items"])
        elif k == "command_started":
            cmd = risk.unwrap(ev["command"])
            rk = risk.classify(cmd)
            human = parsers.humanize_command(cmd)
            self.set_status(human + "…")
            if re.match(r"^(\S*/)?firedev\s+(run|request)\b", cmd):
                return  # FireDev shows the run itself, with structured results.
            item = self.add_activity("command", human, detail=cmd, level=rk.level, status="running", ref=f"{run.id}:{ev['item_id']}", task=tid)
            self.raw[f"{run.id}:{ev['item_id']}:activity"] = item["id"]
            self.last_command = {"command": cmd, "title": human, "level": rk.level, "level_name": risk.LEVEL_NAMES[rk.level],
                                 "running": True, "exit_code": None, "source": "agent", "run": None, "started": time.time()}
            self.emit("command_started", tid, command=cmd, title=human, level=rk.level, source="agent", item=ev["item_id"])
        elif k == "command_finished":
            cmd = risk.unwrap(ev["command"])
            if re.match(r"^(\S*/)?firedev\s+(run|request)\b", cmd):
                return
            key = f"{run.id}:{ev['item_id']}"
            self.raw[key] = (ev.get("output") or "")[-TAIL_BYTES:]
            code = ev.get("exit_code")
            self.update_activity(self.raw.pop(f"{key}:activity", None), status="done" if code == 0 else "failed", exit_code=code)
            if self.last_command and self.last_command.get("command") == cmd:
                self.last_command.update(running=False, exit_code=code, latest=_last_line(ev.get("output") or ""))
            self.emit("command_finished", tid, command=cmd, exit_code=code, source="agent", item=ev["item_id"])
            # A build or test the agent ran directly still gets structured results.
            human = parsers.humanize_command(cmd)
            if human == "Running the tests":
                self._record_tests(tid, parsers.parse_test_summary(ev.get("output") or ""), code, ev.get("output") or "", None)
            elif human == "Building the app":
                self._record_build(tid, ev.get("output") or "", code, None)
        elif k == "file_changed":
            path = ev["path"]
            rel = path[len(self.path) + 1:] if path.startswith(self.path + "/") else path
            if task:
                task.files[rel] = ev.get("change", "update")
            label = parsers.file_kind_label(ev.get("change", "update"))
            self.set_status(f"Editing {posixpath.basename(rel)}…")
            self.add_activity("file", f"{label} {posixpath.basename(rel)}", detail=rel, level=1, task=tid)
            self.emit("file_changed", tid, path=rel, change=ev.get("change", "update"))
            self._spawn(self.refresh_changes())
        elif k == "error":
            if ev.get("transient"):
                self.set_status("Reconnecting to the model…")
            elif task:
                task.agent_error = _unwrap_error(ev["message"])
        elif k == "turn_completed":
            pass

    # --- run completion -----------------------------------------------------------------------------------

    async def _end_run(self, run: Run) -> None:
        tid = run.task_id
        if run.kind == "agent":
            self.agent_state["state"] = "ready"
            self.service.state_changed(self.project.id)
            return
        out = run.output()
        code = run.exit_code
        if run.activity_id:
            self.update_activity(run.activity_id, status="done" if code == 0 else "failed", exit_code=code,
                                 duration=round(run.finished - run.started, 1))
        if self.last_command and self.last_command.get("run") == run.id:
            self.last_command.update(running=False, exit_code=code, duration=round(run.finished - run.started, 1),
                                     latest=_last_line(out) or self.last_command.get("latest"))
        self.emit("command_finished", tid, run=run.id, command=run.command, exit_code=code, title=run.title,
                  duration=round(run.finished - run.started, 1), source=run.source)
        kind = run.action.kind if run.action else "command"
        if kind == "tests":
            reports = await self._read_reports(run)
            self._record_tests(tid, reports or parsers.parse_test_summary(out), code, out, run)
        elif kind == "build":
            self._record_build(tid, out, code, run)
            if code == 0 and run.action and run.action.artifact:
                await self._record_artifact(run.action.artifact)
        elif kind == "deploy":
            self._record_deploy(tid, out, code, run)
        await self.refresh_changes()

    async def _read_reports(self, run: Run) -> dict | None:
        if not (run.action and run.action.reports):
            return None
        newer = "" if run.exit_code == 0 else f"-newer {shlex.quote(run.dir)}/started"
        # Report paths are globs relative to the project.
        script = (f"cd {shlex.quote(self.path)} && for f in {run.action.reports}; do [ -f \"$f\" ] || continue; "
                  f"if [ -z '{newer}' ] || [ -n \"$(find \"$f\" {newer} 2>/dev/null)\" ]; then cat \"$f\"; printf '\\n<<<FIREDEV-SPLIT>>>\\n'; fi; done")
        _, out, _ = await self.remote.run(script)
        docs = [d for d in out.split("<<<FIREDEV-SPLIT>>>") if d.strip()]
        return parsers.parse_junit_xml(docs) if docs else None

    def _record_tests(self, tid, counts: dict | None, code, out: str, run: Run | None) -> None:
        duration = round(run.finished - run.started, 1) if run else None
        if counts:
            state = "passed" if counts["failed"] == 0 and code in (0, None) else "failed"
            self.tests = {"state": state, **counts, "duration": duration, "finished": time.time()}
        else:
            build = parsers.parse_build_output(out, code, self.path)
            if code == 0:
                self.tests = {"state": "passed", "total": None, "passed": None, "failed": 0, "duration": duration, "finished": time.time()}
            else:
                self.tests = {"state": "error", "total": None, "passed": None, "failed": None, "duration": duration,
                              "finished": time.time(), "message": "The tests didn't compile" if build["errors"] else "The test run failed",
                              "errors": build["errors"]}
                if build["errors"]:
                    self.build = {"state": "failed", **build, "finished": time.time(), "duration": duration}
        t = self.tests
        label = (f"Tests: {t['passed']} / {t['total']} passed" if t.get("total") is not None
                 else "Tests passed" if t["state"] == "passed" else t.get("message", "Tests failed"))
        self.add_activity("tests", label, level=0, status="done" if t["state"] == "passed" else "failed", ref=run.id if run else None, task=tid)
        self.emit("tests_finished", tid, **{k: v for k, v in t.items() if k != "errors"}, errors=t.get("errors", []),
                  source=run.source if run else "agent")

    def _record_build(self, tid, out: str, code, run: Run | None) -> None:
        b = parsers.parse_build_output(out, code, self.path)
        duration = round(run.finished - run.started, 1) if run else None
        self.build = {"state": "passed" if b["ok"] else "failed", **b, "duration": duration, "finished": time.time(),
                      "title": run.title if run else "Build"}
        n = b["error_count"]
        label = "Build succeeded" if b["ok"] else f"Build failed · {n} error{'s' if n != 1 else ''}" if n else "Build failed"
        self.add_activity("build", label, level=0, status="done" if b["ok"] else "failed", ref=run.id if run else None, task=tid)
        self.emit("build_finished", tid, **self.build, source=run.source if run else "agent")

    async def _record_artifact(self, path: str) -> None:
        code, out, _ = await self.remote.run(f"cd {shlex.quote(self.path)} && stat -c '%s %Y' {shlex.quote(path)}")
        if code == 0 and out.strip():
            size, mtime = out.split()
            self.build["artifact"] = {"path": path, "name": posixpath.basename(path), "size": int(size), "mtime": int(mtime)}
            self.emit("build_finished", None, **self.build, artifact_only=True)

    def _record_deploy(self, tid, out: str, code, run: Run) -> None:
        text = parsers.strip_ansi(out)
        url = None
        m = re.search(r"https?://\S+", text.split("Preview live at")[-1]) if "Preview live at" in text else None
        if m:
            url = m.group(0)
        checks = [ln.strip() for ln in text.splitlines() if re.search(r"(?i)health check|check(s)? passed|verified", ln)]
        self.deploy = {"state": "passed" if code == 0 else "failed", "url": url, "checks": checks[-4:],
                       "duration": round(run.finished - run.started, 1), "finished": time.time(), "title": run.title,
                       "message": _last_line(text)}
        self.add_activity("deploy", "Deployment complete" if code == 0 else "Deployment failed", detail=_last_line(text),
                          level=run.level, status="done" if code == 0 else "failed", ref=run.id, task=tid)
        self.emit("deploy_finished", tid, **self.deploy, tests=self.tests, build=self.build, source=run.source)

    # --- tasks --------------------------------------------------------------------------------------------

    def _task(self, tid: str | None) -> Task | None:
        if not tid:
            return None
        if self.current and self.current.id == tid:
            return self.current
        return next((t for t in self.tasks if t.id == tid), None)

    def submit(self, task: Task) -> Task:
        self.pending.append(task)
        self.tasks.append(task)
        self.emit("task_queued", task.id, task=task.to_dict(), position=len(self.pending) - (0 if self.current else 1))
        self._wake.set()
        return task

    async def _worker(self) -> None:
        while not self._closed:
            # A turn reattached after a restart is still running: new tasks wait for it.
            if not self.pending or self.current is not None:
                self._wake.clear()
                await self._wake.wait()
                continue
            task = self.pending.popleft()
            if task.cancelled:
                continue
            self.current = task
            task.status, task.started = "running", time.time()
            self.add_activity("task", task.title, detail=task.text, task=task.id, status="running")
            self.emit("task_started", task.id, task=task.to_dict())
            try:
                if task.kind == "agent":
                    await self._run_agent_task(task)
                else:
                    await self._run_action_task(task)
            except Exception as e:  # noqa: BLE001 - a task failure must never stop the worker
                log.exception("task %s crashed", task.id)
                task.status, task.error = "failed", str(e)
            task.finished = time.time()
            if task.cancelled:
                task.status = "cancelled"
            self.current = None
            if task.status == "completed":
                self.emit("task_completed", task.id, task=task.to_dict())
            elif task.status == "cancelled":
                self.emit("task_cancelled", task.id, task=task.to_dict())
            else:
                task.status = "failed"
                self.emit("task_failed", task.id, task=task.to_dict(), error=task.error)
            self.add_activity("task-end", {"completed": "Task complete", "cancelled": "Task stopped"}.get(task.status, "Task failed"),
                              detail=task.summary or task.error or "", task=task.id, status="done" if task.status == "completed" else "failed")

    async def _run_agent_task(self, task: Task) -> None:
        if self.agent_state["state"] == "unavailable":
            task.status, task.error = "failed", f"{self.adapter.display} isn't installed on {self.project.machine.display}"
            return
        task.base_ref = await self._snapshot_ref(task.id)
        first = not self.agent_state["thread_id"]
        prompt = self.adapter.compose_prompt(task.prompt or task.text, self._context_for_agent(), first_turn=first,
                                             tool=f"{self.remote.home}/.firedev/bin/firedev")
        task.status_text = f"Asking {self.adapter.display}…"
        d = f"{self.state_dir}/runs"
        run_id_hint = _rid("p")
        prompt_file = f"{d}/{run_id_hint}.prompt.txt"
        await self.remote.write_file(prompt_file, prompt)
        command = self.adapter.turn_command(prompt_file, self.agent_state["thread_id"], self.path, self.remote.home)
        self.last_agent_task = task
        run = await self.start_run(kind="agent", title=task.title, command=command, level=1, source=task.source, task=task)
        await run.done.wait()
        if task.cancelled:
            return
        if run.exit_code == 0 and not task.agent_error:
            task.status = "completed"
        else:
            task.status = "failed"
            task.error = task.agent_error or _agent_failure(run.output()) or f"{self.adapter.display} stopped with exit code {run.exit_code}"
            if self.agent_state["thread_id"] and "thread" in (task.error or "").lower():
                self.agent_state["thread_id"] = None
        await self.refresh_changes()

    def _context_for_agent(self) -> str | None:
        lines = []
        if self.build.get("state") == "failed" and self.build.get("errors"):
            lines.append("The last build failed:")
            lines += [f"- {e.get('file') or ''}:{e.get('line') or ''} {e['message']}" for e in self.build["errors"][:6]]
        if self.tests.get("state") == "failed":
            lines.append(f"The last test run had {self.tests.get('failed')} failing test(s).")
        return "\n".join(lines) or None

    async def _run_action_task(self, task: Task) -> None:
        if task.kind == "command":
            cmd = task.command or ""
            rk = risk.classify(cmd)
            title, level, action, why, explanation = task.title, rk.level, None, task.text, rk.explanation
        else:
            action = self.project.actions[task.action_id]
            rk = risk.classify(action.run)
            level = max(action.level, rk.level)
            cmd, title, why = action.run, action.title, action.reason or task.text
            explanation = action.risk or rk.explanation
        if risk.needs_approval(level):
            task.status = "waiting_approval"
            task.status_text = "Waiting for approval on the TV"
            self.service.state_changed(self.project.id)
            ok, note = await self.service.request_approval(self, task=task, command=cmd, title=title, reason=why,
                                                           risk=explanation, level=level, source="voice" if task.source != "agent" else "agent")
            if not ok:
                task.status, task.error, task.summary = "cancelled", note or "Rejected on the TV", "Rejected on the TV"
                task.cancelled = True
                return
            task.status = "running"
        task.status_text = title + "…"
        run = await self.start_run(kind="action" if action else "command", title=title, command=cmd, level=level,
                                   source=task.source, task=task, action=action)
        await run.done.wait()
        if task.cancelled:
            return
        task.status = "completed" if run.exit_code == 0 else "failed"
        kind = action.kind if action else "command"
        if kind == "tests":
            t = self.tests
            task.summary = (f"{t['passed']} of {t['total']} tests passed" if t.get("total") is not None else
                            "Tests passed" if t.get("state") == "passed" else t.get("message", "Tests failed"))
        elif kind == "build":
            n = self.build.get("error_count", 0)
            task.summary = "Build succeeded" if run.exit_code == 0 else f"Build failed with {n} error{'s' if n != 1 else ''}"
        elif kind == "deploy":
            task.summary = "Deployment complete" if run.exit_code == 0 else "Deployment failed"
        else:
            task.summary = _last_line(run.output()) or ("Done" if run.exit_code == 0 else "Failed")
        if run.exit_code != 0:
            task.error = task.summary

    async def cancel(self, source: str = "tv") -> bool:
        stopped = False
        for t in list(self.pending):
            t.cancelled = True
            self.pending.remove(t)
            stopped = True
        task = self.current
        if task:
            task.cancelled = True
            self.service.cancel_approvals(self.project.id, task.id)
            for rid in task.run_ids:
                run = self.runs.get(rid)
                if run and not run.done.is_set():
                    await self.remote.run(f"tmux kill-window -t {self._t(rid)} 2>/dev/null")
            stopped = True
            self.add_activity("task-end", "Stopping…", task=task.id, status="failed")
        return stopped

    def continue_last(self, source: str) -> Task | None:
        last = self.last_agent_task
        if not last or self.adapter.id == "shell":
            return None
        return self.submit(Task(id=_rid("t"), title=f"Continue: {last.title}"[:70], kind="agent", text="Continue",
                                prompt="Continue where you left off. If you were stopped, pick up from the last step.", source=source))

    # --- agent requests (firedev run / firedev request) -------------------------------------------------

    async def _watch_queue(self) -> None:
        if self._watching:
            return
        self._watching = True
        try:
            q = shlex.quote
            async for chunk in self.remote.stream(f"{q(self.remote.home)}/.firedev/bin/firedev-watch {q(self.queue_dir)}"):
                for rid in chunk.split():
                    if re.fullmatch(r"r[0-9a-zA-Z]+", rid):
                        self._spawn(self._handle_request(rid))
        except RemoteError:
            pass
        finally:
            self._watching = False

    async def _set_request_state(self, rid: str, state: str) -> None:
        await self.remote.run(f"printf '%s\\n' {shlex.quote(state)} > {shlex.quote(self.queue_dir)}/{rid}.state")

    async def _handle_request(self, rid: str) -> None:
        code, out, _ = await self.remote.run(f"cat {shlex.quote(self.queue_dir)}/{rid}.taken")
        try:
            req = json.loads(out)
        except json.JSONDecodeError:
            await self._set_request_state(rid, "error unreadable request")
            return
        task = self.current
        action: Action | None = None
        if req.get("type") == "action":
            aid = req.get("action", "")
            action = self.project.actions.get(aid) or next((a for a in self.project.actions.values() if a.kind == aid or (aid in ("test", "tests") and a.kind == "tests")), None)
            if not action:
                await self._set_request_state(rid, f"error no action called '{aid}'. Available: {', '.join(self.project.actions)}")
                return
            command, title = action.run, action.title
            level = max(action.level, risk.classify(action.run).level)
            reason = action.reason or f"{self.adapter.display} wants to {action.title.lower()}"
            explanation = action.risk or risk.classify(action.run).explanation
        else:
            command = req.get("command", "").strip()
            if not command:
                await self._set_request_state(rid, "error empty command")
                return
            rk = risk.classify(command)
            # It runs outside the agent's sandbox, so it is never below level 2.
            level = max(2, rk.level)
            title = parsers.humanize_command(command)
            reason = req.get("reason") or "No reason given"
            explanation = rk.explanation if rk.level >= 2 else "Runs outside the agent's sandbox, with your normal permissions."
        if risk.needs_approval(level):
            await self._set_request_state(rid, "waiting")
            if task:
                task.status = "waiting_approval"
                task.status_text = "Waiting for your approval"
            ok, note = await self.service.request_approval(self, task=task, command=command, title=title, reason=reason,
                                                           risk=explanation, level=level, source="agent")
            if task and task.status == "waiting_approval":
                task.status = "running"
            if not ok:
                await self._set_request_state(rid, f"rejected {note or 'the user said no'}")
                return
        run = await self.start_run(kind="action" if action else "command", title=title, command=command, level=level,
                                   source="agent", task=task, action=action, request_id=rid)
        await self._set_request_state(rid, f"running {run.dir}")
        if task:
            self.set_status(title + "…")

    # --- reattach after FireDev restarts ----------------------------------------------------------------

    async def _reattach_runs(self) -> None:
        q = shlex.quote
        script = (f"cd {q(self.state_dir)}/runs 2>/dev/null || exit 0; for d in */; do d=${{d%/}}; [ -f \"$d/exit_code\" ] && continue; "
                  f"[ -f \"$d/meta.json\" ] || continue; tmux list-windows -t ={q(self.project.tmux_session)} -F '#{{window_name}}' | grep -qxF \"$d\" || continue; "
                  f"cat \"$d/meta.json\"; echo; done")
        _, out, _ = await self.remote.run(script)
        for line in out.splitlines():
            try:
                meta = json.loads(line)
            except json.JSONDecodeError:
                continue
            if meta["id"] in self.runs:
                continue  # still followed by this process; _follow resumes on its own
            td = meta.get("task") or {}
            task = self._task(td.get("id"))
            if task is None and td:
                task = Task(id=td["id"], title=td.get("title", "Task"), kind=td.get("kind", "agent"), text=td.get("text", ""),
                            source=td.get("source", "voice"), status="running", started=td.get("started") or time.time())
                self.tasks.append(task)
            action = self.project.actions.get(meta.get("action") or "")
            run = Run(id=meta["id"], kind=meta["kind"], title=meta["title"], command=meta["command"], level=meta["level"],
                      source=meta["source"], task_id=task.id if task else None, dir=f"{self.state_dir}/runs/{meta['id']}",
                      action=action, request_id=meta.get("request_id"))
            if run.kind == "agent":
                run.parser = self.adapter.new_parser()
            self.runs[run.id] = run
            if task and run.id not in task.run_ids:
                task.run_ids.append(run.id)
            self.add_activity("notice", f"Reattached to {run.title}", task=run.task_id)
            self._begin_run_events(run)
            self._spawn(self._follow(run))
            if task and run.kind == "agent" and self.current is None:
                self.current = task
                self.last_agent_task = task
                self._spawn(self._finish_reattached(task, run))

    async def _finish_reattached(self, task: Task, run: Run) -> None:
        await run.done.wait()
        task.finished = time.time()
        if task.cancelled:
            task.status = "cancelled"
        else:
            task.status = "completed" if run.exit_code == 0 and not task.agent_error else "failed"
        self.current = None
        self.emit({"completed": "task_completed", "cancelled": "task_cancelled"}.get(task.status, "task_failed"), task.id, task=task.to_dict())
        self._wake.set()

    # --- changes, diff and undo -----------------------------------------------------------------------------

    async def _snapshot_ref(self, task_id: str) -> str | None:
        if not self.is_git:
            return None
        q = shlex.quote(self.path)
        code, out, _ = await self.remote.run(f"cd {q} && (git stash create 2>/dev/null; true) | head -1")
        sha = out.strip()
        if not sha:
            code, out, _ = await self.remote.run(f"cd {q} && git rev-parse HEAD 2>/dev/null")
            sha = out.strip()
        if sha:
            await self.remote.run(f"cd {q} && git update-ref refs/firedev/{task_id} {sha}")
        return sha or None

    async def refresh_changes(self) -> None:
        if not getattr(self, "is_git", False) or not self.remote.connected:
            return
        q = shlex.quote(self.path)
        script = (f"cd {q} && git diff --numstat HEAD 2>/dev/null; echo '<<<U>>>'; "
                  f"git ls-files --others --exclude-standard | head -50 | while read -r f; do printf '%s\\t%s\\n' \"$(wc -l < \"$f\" 2>/dev/null || echo 0)\" \"$f\"; done")
        try:
            _, out, _ = await self.remote.run(script)
        except RemoteError:
            return
        tracked, _, untracked = out.partition("<<<U>>>")
        files = []
        for line in tracked.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                a, r, p = parts
                files.append({"path": p, "name": posixpath.basename(p), "added": int(a) if a.isdigit() else 0,
                              "removed": int(r) if r.isdigit() else 0, "status": "modified"})
        for line in untracked.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                n, p = parts
                files.append({"path": p, "name": posixpath.basename(p), "added": int(n.strip() or 0), "removed": 0, "status": "added"})
        self.changes = {"files": files, "added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
                        "updated": time.time()}
        self.service.state_changed(self.project.id)

    async def file_diff(self, path: str) -> dict:
        if not any(f["path"] == path for f in self.changes["files"]):
            await self.refresh_changes()
        entry = next((f for f in self.changes["files"] if f["path"] == path), None)
        if not entry:
            return {"path": path, "hunks": [], "message": "No changes in this file"}
        q = shlex.quote
        if entry["status"] == "added":
            cmd = f"cd {q(self.path)} && git diff --no-index -- /dev/null {q(path)}"
        else:
            cmd = f"cd {q(self.path)} && git diff -U3 HEAD -- {q(path)}"
        _, out, _ = await self.remote.run(cmd)
        return {"path": path, "name": posixpath.basename(path), "added": entry["added"], "removed": entry["removed"],
                "hunks": parse_unified_diff(out)}

    async def undo_last(self, source: str) -> dict:
        task = self.last_agent_task
        if self.busy:
            return {"ok": False, "message": "Wait for the current task to finish, or say Stop."}
        if not task or not task.base_ref or not task.files:
            return {"ok": False, "message": "There's no agent change to undo."}
        q = shlex.quote
        files = list(task.files)
        parts = [f"cd {q(self.path)} || exit 1", "after=$( (git stash create; true) | head -1)",
                 f"if [ -n \"$after\" ]; then git update-ref refs/firedev/undo-{task.id} \"$after\"; fi"]
        for f in files:
            parts.append(f"if git cat-file -e {task.base_ref}:{q(f)} 2>/dev/null; then git restore --source={task.base_ref} --worktree -- {q(f)}; else rm -f -- {q(f)}; fi")
        code, out, err = await self.remote.run("; ".join(parts))
        if code != 0:
            return {"ok": False, "message": f"Undo failed: {(err or out).strip()[:200]}"}
        await self.refresh_changes()
        names = ", ".join(posixpath.basename(f) for f in files[:4]) + ("…" if len(files) > 4 else "")
        self.add_activity("undo", f"Undid changes to {names}", detail=f"Restored {len(files)} file(s) from before “{task.title}”. "
                          f"The undone version is saved as refs/firedev/undo-{task.id}.", level=1, task=task.id)
        self.emit("changes_undone", task.id, files=files, title=task.title)
        task.files = {}
        return {"ok": True, "message": f"Undid {len(files)} file change{'s' if len(files) != 1 else ''}", "files": files}

    async def run_output(self, ref: str) -> str:
        if ref in self.raw:
            return self.raw[ref]
        run = self.runs.get(ref)
        if run:
            return parsers.strip_ansi(run.output())
        if re.fullmatch(r"[a-z]\d{6}[a-z0-9]{4}", ref or ""):
            _, out, _ = await self.remote.run(f"tail -c {TAIL_BYTES} {shlex.quote(self.state_dir)}/runs/{ref}/out.log 2>/dev/null")
            return parsers.strip_ansi(out)
        return ""

    async def reset_state(self, forget_thread: bool) -> None:
        """Clears FireDev's view of the project (used between demo takes). Never touches the repository."""
        await self.cancel("admin")
        self.tasks.clear()
        self.activity.clear()
        self.raw.clear()
        self.build, self.tests, self.deploy = {"state": "unknown"}, {"state": "unknown"}, {"state": "unknown"}
        self.last_command = None
        self.last_agent_task = None
        if forget_thread:
            self.agent_state["thread_id"] = None
            if self.remote.connected:
                await self._save_agent_state()
        await self.refresh_changes()
        self.service.state_changed(self.project.id)

    # --- snapshot -------------------------------------------------------------------------------------------

    def summary(self) -> dict:
        p = self.project
        return {
            "id": p.id, "name": p.name, "description": p.description, "machine": p.machine.display,
            "host": f"{p.machine.user}@{p.machine.host}", "path": p.path, "agent": self.adapter.display, "agent_id": self.adapter.id,
            "connection": self.conn_state, "connection_message": self.conn_message,
            "agent_state": self.agent_state["state"], "busy": self.busy,
            "task": self.current.title if self.current else None,
            "build": self.build.get("state"), "tests": {k: self.tests.get(k) for k in ("state", "passed", "total", "failed")},
            "changes": len(self.changes["files"]), "aliases": p.aliases,
        }

    def snapshot(self) -> dict:
        p = self.project
        recent = [t.to_dict() for t in list(self.tasks)[-8:]]
        last = self.current or (self.tasks[-1] if self.tasks else None)
        return {
            **self.summary(),
            "tmux": p.tmux_session,
            "agent": dict(self.agent_state),
            "actions": {a.id: {"title": a.title, "kind": a.kind, "level": a.level} for a in p.actions.values()},
            "task": self.current.to_dict() if self.current else None,
            "last_task": last.to_dict() if last else None,
            "queue": [t.title for t in self.pending],
            "tasks": recent,
            "build": self.build, "tests": self.tests, "deploy": self.deploy,
            "last_command": self.last_command,
            "changes": self.changes,
            "activity": list(self.activity)[-80:],
            "approvals": self.service.pending_approvals(p.id),
        }


def parse_unified_diff(text: str, max_lines: int = 600) -> list[dict]:
    hunks = []
    cur = None
    old = new = 0
    count = 0
    for line in text.splitlines():
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
            if not m:
                continue
            old, new = int(m.group(1)), int(m.group(2))
            cur = {"header": m.group(3).strip(), "old_start": old, "new_start": new, "lines": []}
            hunks.append(cur)
            continue
        if cur is None or line.startswith(("+++", "---", "\\ No newline")):
            continue
        if count >= max_lines:
            cur["truncated"] = True
            break
        count += 1
        if line.startswith("+"):
            cur["lines"].append({"t": "+", "text": line[1:], "new": new})
            new += 1
        elif line.startswith("-"):
            cur["lines"].append({"t": "-", "text": line[1:], "old": old})
            old += 1
        else:
            cur["lines"].append({"t": " ", "text": line[1:], "old": old, "new": new})
            old += 1
            new += 1
    return hunks


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in parsers.strip_ansi(text).splitlines() if ln.strip()]
    return lines[-1][:200] if lines else ""


def _agent_failure(out: str) -> str | None:
    for line in reversed(parsers.strip_ansi(out).splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("message") or (ev.get("error") or {}).get("message")
            if msg:
                return _unwrap_error(str(msg))[:300]
            continue
        if re.search(r"(?i)error|not found|denied|limit", line):
            return line[:300]
    return None


def _unwrap_error(msg: str) -> str:
    """Agents sometimes wrap an API error as JSON inside the message; show the human sentence."""
    for _ in range(3):
        try:
            data = json.loads(msg)
        except (json.JSONDecodeError, TypeError):
            return msg
        inner = data.get("error") if isinstance(data, dict) else None
        msg = (inner.get("message") if isinstance(inner, dict) else None) or data.get("message") or msg
        if not msg.strip().startswith("{"):
            return msg
    return msg


def _log_failure(t: asyncio.Task) -> None:
    if not t.cancelled() and t.exception():
        log.error("background task failed", exc_info=t.exception())
