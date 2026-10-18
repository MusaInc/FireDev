"""The FireDev service: projects, approvals, voice, and the one command API every client uses.

The TV (WebSocket), the phone microphone, the CLI, MCP clients and the Alexa skill all call `execute()`. That
keeps the safety rules in one place: approvals can only come from roles that are allowed to give them.
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
import time
from dataclasses import dataclass, field

from . import intents, risk
from .auth import Client, DeviceStore
from .config import Config
from .events import Bus
from .session import ProjectSession, Task, _rid

log = logging.getLogger("firedev.service")


class CommandError(Exception):
    pass


@dataclass
class Approval:
    id: str
    project: str
    task: str | None
    command: str
    title: str
    reason: str
    risk: str
    level: int
    source: str
    agent: str
    kind: str = "command"  # command | link (a voice assistant asking to be linked)
    created: float = field(default_factory=time.time)
    future: asyncio.Future | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "project": self.project, "task": self.task, "command": self.command, "title": self.title,
                "reason": self.reason, "risk": self.risk, "level": self.level, "level_name": risk.LEVEL_NAMES[self.level],
                "strong": risk.needs_strong_confirmation(self.level), "source": self.source, "agent": self.agent,
                "kind": self.kind, "created": self.created}


COMMANDS = {
    "list_projects", "open_project", "close_project", "send_agent_task", "run_action", "get_project_status",
    "get_current_task", "get_build_status", "approve_action", "reject_action", "cancel_task", "continue_task",
    "undo_changes", "get_changes", "get_file_diff", "get_output", "voice_command", "voice_link", "get_history", "reset_state",
}
READ_ONLY = {"list_projects", "get_project_status", "get_current_task", "get_build_status", "get_changes", "get_file_diff",
             "get_output", "get_history"}


class FireDev:
    def __init__(self, config: Config, devices: DeviceStore, bus: Bus | None = None) -> None:
        self.config = config
        self.devices = devices
        self.bus = bus or Bus(config.settings.data_dir / "history")
        self.sessions = {pid: ProjectSession(self, p) for pid, p in config.projects.items()}
        self.approvals: dict[str, Approval] = {}
        self._dirty: set[str] = set()
        self._flush: asyncio.TimerHandle | None = None
        self.focus_project: str | None = None

    # --- state pushes ---------------------------------------------------------------------------------------

    def state_changed(self, pid: str) -> None:
        """Coalesces state changes into one snapshot push per project every 120 ms."""
        self._dirty.add(pid)
        if self._flush is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._flush = loop.call_later(0.12, self._push_states)

    def _push_states(self) -> None:
        self._flush = None
        dirty, self._dirty = self._dirty, set()
        for pid in dirty:
            if pid in self.sessions:
                self.bus.emit("state", pid, None, snapshot=self.sessions[pid].snapshot(), home=self.home())

    def home(self) -> dict:
        return {"projects": [s.summary() for s in self.sessions.values()], "approvals": len(self.approvals)}

    def session(self, pid: str | None) -> ProjectSession:
        pid = pid or self.focus_project
        if not pid:
            if len(self.sessions) == 1:
                return next(iter(self.sessions.values()))
            raise CommandError("Which project? Open one first.")
        if pid not in self.sessions:
            raise CommandError(f"No project called '{pid}'")
        return self.sessions[pid]

    # --- approvals ------------------------------------------------------------------------------------------

    async def request_approval(self, session: ProjectSession, *, task: Task | None, command: str, title: str, reason: str,
                               risk: str, level: int, source: str, kind: str = "command") -> tuple[bool, str | None]:
        a = Approval(id=_rid("ap"), project=session.project.id, task=task.id if task else None, command=command,
                     title=title, reason=reason, risk=risk, level=level, source=source, agent=session.adapter.display, kind=kind)
        a.future = asyncio.get_running_loop().create_future()
        self.approvals[a.id] = a
        item = session.add_activity("approval", "Approval required", detail=command, level=level, status="waiting", task=a.task)
        session.emit("approval_required", a.task, approval=a.to_dict())
        try:
            ok, note, who = await a.future
        finally:
            self.approvals.pop(a.id, None)
        session.update_activity(item["id"], title="Approved" if ok else "Rejected", status="done" if ok else "failed",
                                detail=f"{command}\n{'Approved' if ok else 'Rejected'} by {who}")
        session.emit("approval_accepted" if ok else "approval_rejected", a.task, approval=a.to_dict(), by=who, note=note)
        return ok, note

    def pending_approvals(self, pid: str | None = None) -> list[dict]:
        return [a.to_dict() for a in self.approvals.values() if pid is None or a.project == pid]

    def _may_approve(self, client: Client, a: Approval, held: bool) -> str | None:
        if client.role == "tv":
            if risk.needs_strong_confirmation(a.level) and not held:
                return "Level 3 actions need the held confirmation on the TV."
            return None
        if client.role == "assistant" and self.config.settings.remote_approval == "level2" and a.level <= 2:
            return None
        return "Approve this on the TV with your remote."

    def decide(self, approval_id: str, approve: bool, client: Client, held: bool = False, note: str | None = None) -> dict:
        a = self.approvals.get(approval_id)
        if not a or not a.future or a.future.done():
            raise CommandError("That approval is no longer waiting")
        if approve:
            why = self._may_approve(client, a, held)
            if why:
                raise CommandError(why)
        a.future.set_result((approve, note, client.name))
        return {"approved": approve, "id": approval_id}

    def cancel_approvals(self, pid: str, task_id: str | None) -> None:
        for a in list(self.approvals.values()):
            if a.project == pid and (task_id is None or a.task == task_id) and a.future and not a.future.done():
                a.future.set_result((False, "The task was stopped", "FireDev"))

    # --- commands -------------------------------------------------------------------------------------------

    async def execute(self, name: str, args: dict, client: Client) -> dict:
        if name not in COMMANDS:
            raise CommandError(f"Unknown command {name}")
        if client.role == "voice" and name not in READ_ONLY | {"voice_command", "reject_action", "cancel_task", "list_projects"}:
            raise CommandError("A phone microphone can only send voice commands")
        args = args or {}
        fn = getattr(self, f"_cmd_{name}")
        return await fn(args, client)

    async def _cmd_list_projects(self, args, client):
        return self.home()

    async def _cmd_open_project(self, args, client):
        s = self.session(args.get("project"))
        self.focus_project = s.project.id
        if s.conn_state not in ("connected",):
            try:
                await s.open()
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "message": str(e), "project": s.project.id}
        if s.agent_state["state"] == "unavailable":
            return {"ok": True, "project": s.project.id,
                    "message": f"Connected to {s.project.machine.display}, but {s.adapter.display} isn't installed there."}
        return {"ok": True, "project": s.project.id, "message": f"Connected to {s.project.machine.display}. {s.adapter.display} is ready."}

    async def _cmd_close_project(self, args, client):
        s = self.session(args.get("project"))
        await s.close()
        return {"ok": True}

    async def _ensure_open(self, s: ProjectSession) -> None:
        if s.conn_state != "connected":
            await s.open()
        self.focus_project = s.project.id

    async def _cmd_send_agent_task(self, args, client):
        s = self.session(args.get("project"))
        text = (args.get("task") or "").strip()
        if not text:
            raise CommandError("What should the agent do?")
        await self._ensure_open(s)
        if s.adapter.id == "shell":
            raise CommandError("This project has no AI agent. Say 'run the tests' or 'build the app'.")
        task = s.submit(Task(id=_rid("t"), title=args.get("title") or intents._title(text), kind="agent", text=args.get("text") or text,
                             prompt=text, mode=args.get("mode", "change"), source=args.get("source") or client.role))
        busy = len(s.pending) > 1 or (s.current is not None and s.current is not task)
        return {"ok": True, "task": task.id, "queued": busy,
                "message": (f"Queued for {s.adapter.display}." if busy else f"Asking {s.adapter.display}.")}

    async def _cmd_run_action(self, args, client):
        s = self.session(args.get("project"))
        aid = args.get("action")
        if aid not in s.project.actions:
            raise CommandError(f"No action called '{aid}'. Available: {', '.join(s.project.actions)}")
        await self._ensure_open(s)
        a = s.project.actions[aid]
        task = s.submit(Task(id=_rid("t"), title=a.title, kind="action", action_id=aid, text=args.get("text") or a.title,
                             source=args.get("source") or client.role))
        level = max(a.level, risk.classify(a.run).level)
        msg = f"{a.title}. This needs your approval on the TV." if risk.needs_approval(level) else f"{a.title}."
        return {"ok": True, "task": task.id, "level": level, "message": msg}

    async def _cmd_get_project_status(self, args, client):
        s = self.session(args.get("project"))
        snap = s.snapshot()
        return {"ok": True, "status": snap, "spoken": spoken_status(snap)}

    async def _cmd_get_current_task(self, args, client):
        s = self.session(args.get("project"))
        t = s.current or (s.tasks[-1] if s.tasks else None)
        return {"ok": True, "task": t.to_dict() if t else None}

    async def _cmd_get_build_status(self, args, client):
        s = self.session(args.get("project"))
        return {"ok": True, "build": s.build, "tests": s.tests, "deploy": s.deploy}

    async def _cmd_approve_action(self, args, client):
        aid = args.get("approval") or (next(iter(self.approvals)) if len(self.approvals) == 1 else None)
        if not aid:
            raise CommandError("Which approval?")
        return {"ok": True, **self.decide(aid, True, client, held=bool(args.get("held")))}

    async def _cmd_reject_action(self, args, client):
        aid = args.get("approval") or (next(iter(self.approvals)) if len(self.approvals) == 1 else None)
        if not aid:
            raise CommandError("There's nothing waiting for approval")
        return {"ok": True, **self.decide(aid, False, client, note=args.get("note") or f"Rejected by {client.name}")}

    async def _cmd_cancel_task(self, args, client):
        s = self.session(args.get("project"))
        stopped = await s.cancel(client.role)
        return {"ok": True, "stopped": stopped, "message": "Stopping." if stopped else "Nothing is running."}

    async def _cmd_continue_task(self, args, client):
        s = self.session(args.get("project"))
        await self._ensure_open(s)
        task = s.continue_last(client.role)
        if not task:
            raise CommandError("There's nothing to continue")
        return {"ok": True, "task": task.id, "message": f"Continuing with {s.adapter.display}."}

    async def _cmd_undo_changes(self, args, client):
        s = self.session(args.get("project"))
        await self._ensure_open(s)
        return await s.undo_last(client.role)

    async def _cmd_get_changes(self, args, client):
        s = self.session(args.get("project"))
        await self._ensure_open(s)
        await s.refresh_changes()
        return {"ok": True, "changes": s.changes}

    async def _cmd_get_file_diff(self, args, client):
        s = self.session(args.get("project"))
        await self._ensure_open(s)
        return {"ok": True, "diff": await s.file_diff(args.get("path", ""))}

    async def _cmd_get_output(self, args, client):
        s = self.session(args.get("project"))
        return {"ok": True, "output": await s.run_output(args.get("ref", ""))}

    async def _cmd_get_history(self, args, client):
        s = self.session(args.get("project"))
        return {"ok": True, "events": self.bus.history(s.project.id, int(args.get("limit", 200)))}

    async def _cmd_reset_state(self, args, client):
        if client.role != "admin":
            raise CommandError("Only the FireDev CLI can reset a project")
        s = self.session(args.get("project"))
        await s.reset_state(forget_thread=bool(args.get("forget_thread")))
        return {"ok": True, "message": f"Reset {s.project.name}"}

    async def _cmd_voice_link(self, args, client):
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV can create a phone microphone link")
        token, dev = self.devices.create(args.get("name") or "Phone microphone", "voice", ttl=12 * 3600)
        base = (self.config.settings.public_url or args.get("base") or "").rstrip("/")
        return {"ok": True, "url": f"{base}/voice#t={token}", "device": dev["id"], "expires_in_hours": 12}

    # --- voice ----------------------------------------------------------------------------------------------

    async def _cmd_voice_command(self, args, client):
        return await self.voice(args.get("text", ""), client, source=args.get("source") or client.role, project=args.get("project"))

    def _context(self, pid: str | None) -> dict:
        if not pid or pid not in self.sessions:
            return {"project": None, "pending_approval": bool(self.approvals)}
        s = self.sessions[pid]
        return {"project": pid, "agent": s.adapter.display,
                "actions": {a.id: {"kind": a.kind, "title": a.title} for a in s.project.actions.values()},
                "last_build_failed": s.build.get("state") == "failed", "last_tests_failed": s.tests.get("state") == "failed",
                "pending_approval": any(a.project == pid for a in self.approvals.values())}

    async def voice(self, text: str, client: Client, source: str = "voice", project: str | None = None) -> dict:
        pid = project or self.focus_project
        intent = intents.parse(text, [s.summary() for s in self.sessions.values()], self._context(pid))
        self.bus.emit("voice_command", pid, None, text=text, intent=intent.to_dict(), source=source,
                      agent=self.sessions[pid].adapter.display if pid in self.sessions else None)
        try:
            result = await self._run_intent(intent, client, source, pid)
        except CommandError as e:
            result = {"ok": False, "message": str(e)}
        self.bus.emit("voice_reply", pid, None, text=text, intent=intent.name, reply=result.get("message"), ok=result.get("ok", True))
        return {"intent": intent.to_dict(), **result}

    async def _run_intent(self, intent: intents.Intent, client: Client, source: str, pid: str | None) -> dict:
        n, a = intent.name, intent.args
        if n == "open_project":
            res = await self._cmd_open_project({"project": a["project"]}, client)
            if res.get("ok") and a.get("then"):
                follow = await self.voice(a["then"], client, source, a["project"])
                res["message"] = f"{res['message']} {follow.get('message', '')}".strip()
            return res
        if n == "send_agent_task":
            return await self._cmd_send_agent_task({"project": pid, "task": a["task"], "title": intent.title, "mode": a.get("mode"),
                                                    "source": source, "text": a.get("text")}, client)
        if n == "run_action":
            return await self._cmd_run_action({"project": pid, "action": a["action"], "source": source}, client)
        if n == "cancel_task":
            return await self._cmd_cancel_task({"project": pid}, client)
        if n == "continue_task":
            return await self._cmd_continue_task({"project": pid}, client)
        if n == "undo_changes":
            return await self._cmd_undo_changes({"project": pid}, client)
        if n == "reject_action":
            return await self._cmd_reject_action({}, client)
        if n == "approve_action":
            # Voice never approves by itself: the TV moves focus to Approve and waits for the remote.
            return {"ok": True, "message": "Press OK on the remote to approve.", "navigate": "approval"}
        if n == "get_status":
            if not pid:
                return {"ok": True, "message": f"You have {len(self.sessions)} project(s). Say open and a project name."}
            st = await self._cmd_get_project_status({"project": pid}, client)
            return {"ok": True, "message": st["spoken"]}
        if n == "show_changes":
            if pid:
                await self._cmd_get_changes({"project": pid}, client)
                c = self.sessions[pid].changes
                msg = f"{len(c['files'])} file(s) changed." if c["files"] else "No changes yet."
            else:
                msg = "Open a project first."
            return {"ok": True, "message": msg, "navigate": "changes"}
        if n == "show_activity":
            return {"ok": True, "message": "Here's the activity.", "navigate": "activity"}
        if n == "go_home":
            return {"ok": True, "message": "Here are your projects.", "navigate": "home"}
        if n == "help":
            return {"ok": intent.confidence > 0, "message": intent.title if intent.confidence < 1 else
                    "Try: why is the build failing, fix it and run the tests, build the app, or deploy the preview."}
        raise CommandError(f"I can't do {n} yet")


def spoken_status(snap: dict) -> str:
    parts = []
    t = snap.get("task")
    if t:
        parts.append(f"{snap['agent']['name']} is working on {t['title'].lower()}. {t.get('status_text', '')}")
    else:
        parts.append(f"{snap['name']} is {'connected' if snap['connection'] == 'connected' else snap['connection']}.")
    b = snap.get("build", {})
    if b.get("state") == "failed":
        parts.append(f"The last build failed with {b.get('error_count', 0)} errors.")
    elif b.get("state") == "passed":
        parts.append("The last build passed.")
    tests = snap.get("tests", {})
    if tests.get("total"):
        parts.append(f"{tests['passed']} of {tests['total']} tests passed.")
    if snap.get("approvals"):
        parts.append("Something is waiting for your approval on the TV.")
    return " ".join(p.strip() for p in parts if p.strip())
