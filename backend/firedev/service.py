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

from . import genui, intents, risk, sysagents
from .auth import Client, DeviceStore
from .config import Config
from .events import Bus
from .preview import PreviewManager
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
    "ui_action", "ui_get", "ui_back", "get_data", "ui_show", "ui_patch",
    "preview_status", "preview_open", "preview_start", "preview_stop", "preview_view", "preview_report",
}
READ_ONLY = {"list_projects", "get_project_status", "get_current_task", "get_build_status", "get_changes", "get_file_diff",
             "get_output", "get_history", "ui_get", "get_data", "preview_status"}
DATA_SOURCES = {"status", "projects", "agents", "history", "approvals", "conversation", "preview"}
AGENTS_POLL = 4.0


class FireDev:
    def __init__(self, config: Config, devices: DeviceStore, bus: Bus | None = None) -> None:
        self.config = config
        self.devices = devices
        self.bus = bus or Bus(config.settings.data_dir / "history")
        self.sessions = {pid: ProjectSession(self, p) for pid, p in config.projects.items()}
        self.previews = PreviewManager(self)
        self.approvals: dict[str, Approval] = {}
        self._dirty: set[str] = set()
        self._flush: asyncio.TimerHandle | None = None
        self.focus_project: str | None = None
        self.agents: list[dict] = []
        self.agents_updated = 0.0
        self._agents_task: asyncio.Task | None = None

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
        if kind == "command":
            who = session.adapter.display if source == "agent" else "FireDev"
            session.speak(f"{who} needs your approval to {title[0].lower() + title[1:] if title else 'run a command'}. "
                          f"{'Hold OK' if level >= 3 else 'Press OK'} on the remote to approve, or choose Reject.",
                          "approval", a.task, force=True)
            session.add_turn("firedev", f"Waiting for your approval: {title}", kind="approval", task=a.task)
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

    # --- live data for TV screens ------------------------------------------------------------------------------

    def surface_changed(self, session: ProjectSession) -> None:
        if session.surface and "agents" in genui.binds(session.surface["root"]):
            self._ensure_agents_polling()

    def history_changed(self, session: ProjectSession) -> None:
        self.bus.emit("ui_data", session.project.id, None, source="history", data=self.history_series(session.project.id))

    def history_series(self, pid: str) -> dict:
        tests, builds = [], []
        for ev in self.bus.history(pid, 600):
            d = ev.get("data") or {}
            if ev["type"] == "tests_finished" and d.get("total") is not None:
                tests.append({"ts": ev["ts"], "passed": d.get("passed"), "failed": d.get("failed"), "total": d.get("total"),
                              "duration": d.get("duration")})
            elif ev["type"] == "build_finished" and not d.get("artifact_only") and d.get("state") in ("passed", "failed"):
                builds.append({"ts": ev["ts"], "state": d["state"], "ok": 1 if d["state"] == "passed" else 0,
                               "duration": d.get("duration"), "error_count": d.get("error_count", 0)})
        return {"tests": tests[-40:], "builds": builds[-40:]}

    def firedev_agents(self) -> list[dict]:
        return [{"agent": s.adapter.display, "project": s.project.name, "firedev_project": s.project.id, "source": "firedev",
                 "state": s.agent_state["state"] if s.conn_state == "connected" else "offline",
                 "task": s.current.title if s.current else None, "status_text": s.current.status_text if s.current else None,
                 "presenter": s.ui_current.title if s.ui_current else None, "machine": s.project.machine.display}
                for s in self.sessions.values()]

    async def scan_agents(self, prefer: ProjectSession | None = None) -> list[dict]:
        sessions = ([prefer] if prefer else []) + list(self.sessions.values())
        live = next((s for s in sessions if s.conn_state == "connected" and s.remote.connected), None)
        machine: list[dict] = []
        if live:
            projects = [{"name": s.project.name, "path_abs": live.remote.expand(s.project.path) if s.project.machine is live.project.machine else None}
                        for s in self.sessions.values()]
            try:
                machine = await sysagents.scan(live.remote, projects)
            except Exception as e:  # noqa: BLE001
                log.warning("agent scan failed: %s", e)
        managed = {a["firedev_project"]: a for a in self.firedev_agents()}
        for m in machine:
            f = managed.get(m.get("firedev_project") or "")
            if f:
                m.update(task=f["task"], status_text=f["status_text"], managed_by="FireDev")
        self.agents = self.firedev_agents() + machine
        self.agents_updated = time.time()
        return self.agents

    def _ensure_agents_polling(self) -> None:
        if self._agents_task and not self._agents_task.done():
            return
        self._agents_task = asyncio.get_running_loop().create_task(self._poll_agents())

    async def _poll_agents(self) -> None:
        last = None
        while any(s.surface and "agents" in genui.binds(s.surface["root"]) for s in self.sessions.values()):
            data = await self.scan_agents()
            sig = [(a.get("pid"), a.get("state"), a.get("task"), a.get("status_text")) for a in data]
            if sig != last:
                last = sig
                self.bus.emit("ui_data", None, None, source="agents", data=data, updated=self.agents_updated)
            await asyncio.sleep(AGENTS_POLL)

    def ui_snapshot(self) -> dict:
        return {"surfaces": {pid: {"surface": s.surface, "version": s.surface_version} for pid, s in self.sessions.items() if s.surface},
                "data": {"agents": self.agents, "agents_updated": self.agents_updated,
                         "history": {pid: self.history_series(pid) for pid in self.sessions}}}

    async def data_for(self, session: ProjectSession, source: str) -> dict | list:
        if source not in DATA_SOURCES:
            raise CommandError(f"no data source '{source}' (try: {', '.join(sorted(DATA_SOURCES))})")
        if source == "agents":
            data = await self.scan_agents(session)
            return {"agents": data, "note": "source 'firedev' = FireDev's own project agents; 'machine' = Codex and Claude Code "
                                           "processes found with ps on " + session.project.machine.display, "updated": self.agents_updated}
        if source == "projects":
            return self.home()
        if source == "history":
            return {**self.history_series(session.project.id), "note": "Test and build runs FireDev recorded for this project"}
        if source == "approvals":
            return {"approvals": self.pending_approvals()}
        if source == "conversation":
            return {"conversation": list(session.conversation)}
        if source == "preview":
            p = await self.previews.refresh(session.project.id)
            return {**p.to_dict(), "note": "The project's live preview as FireDev sees it (status, framework, port on the dev machine, "
                                           "errors the TV saw in the page)"}
        snap = session.snapshot()
        return {k: snap.get(k) for k in ("id", "name", "machine", "path", "connection", "agent", "task", "last_task", "queue",
                                         "build", "tests", "deploy", "changes", "approvals", "surface")}

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
        if args.get("conversational"):
            return {"ok": True, "project": s.project.id,
                    "message": f"{s.project.name} is open, and {s.adapter.display} is ready. What would you like to work on?"}
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
                             prompt=text, mode=args.get("mode") or "change", source=args.get("source") or client.role, ui=args.get("ui")))
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

    async def _cmd_ui_get(self, args, client):
        s = self.session(args.get("project"))
        return {"ok": True, "surface": s.surface, "version": s.surface_version}

    async def _cmd_get_data(self, args, client):
        source = args.get("source")
        if source == "agents":
            return {"ok": True, "data": await self.scan_agents(), "updated": self.agents_updated}
        if source == "history":
            s = self.session(args.get("project"))
            return {"ok": True, "data": self.history_series(s.project.id)}
        raise CommandError("Unknown data source")

    async def _cmd_ui_show(self, args, client):
        """The CLI's way to put a screen on the TV (an agent uses the helper's `firedev ui show`)."""
        if client.role != "admin":
            raise CommandError("Only the FireDev CLI can show screens directly")
        s = self.session(args.get("project"))
        try:
            clean = s.show_surface(args.get("surface"), "cli")
        except genui.SpecError as e:
            return {"ok": False, "message": "The screen was refused", "errors": e.errors}
        return {"ok": True, "version": s.surface_version, "title": clean["title"]}

    async def _cmd_ui_patch(self, args, client):
        if client.role != "admin":
            raise CommandError("Only the FireDev CLI can change screens directly")
        s = self.session(args.get("project"))
        try:
            changed = s.patch_surface(args.get("ops"), "cli")
        except genui.SpecError as e:
            return {"ok": False, "message": "The patch was refused", "errors": e.errors}
        return {"ok": True, "version": s.surface_version, "changed": changed}

    async def _cmd_ui_back(self, args, client):
        s = self.session(args.get("project"))
        return {"ok": s.surface_back(), "version": s.surface_version}

    async def _cmd_ui_action(self, args, client):
        """An interaction with a generated screen. The action that runs is the one the server holds for that
        component, never one supplied by the client, and only from the allowlist; risky actions still need approval."""
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV can use what's on screen")
        s = self.session(args.get("project"))
        if not s.surface:
            raise CommandError("Nothing is on screen")
        node, _ = genui.find(s.surface["root"], str(args.get("node", "")))
        if node is None:
            raise CommandError("That part of the screen has changed. Try again.")
        action = node.get("action")
        if args.get("item") is not None:
            items = node.get("items") if isinstance(node.get("items"), list) else []
            i = int(args["item"])
            action = items[i].get("action") if 0 <= i < len(items) and isinstance(items[i], dict) else None
        if node.get("type") == "choice":
            opts = node.get("options") or []
            i = int(args.get("option", -1))
            if not 0 <= i < len(opts):
                raise CommandError("Pick one of the options")
            o = opts[i]
            action = {"type": "ask", "text": (f"{node['prompt']} " if node.get("prompt") else "") + f"My choice: {o.get('value') or o['label']}"}
        elif node.get("type") == "form":
            action = {"type": "ask", "text": _form_text(node, args.get("values") or {})}
        if not action:
            raise CommandError("Nothing happens there")
        t = action["type"]
        pid = s.project.id
        if t == "ask":
            return await self.voice(action["text"], client, source="tv", project=pid, ui={"focused": node.get("id"), "version": s.surface_version})
        if t == "run_action":
            return await self._cmd_run_action({"project": pid, "action": action["action"], "source": "tv"}, client)
        if t == "cancel_task":
            if not args.get("confirmed"):
                raise CommandError("Confirm on the TV first")
            return await self._cmd_cancel_task({"project": pid}, client)
        if t == "open_project":
            return await self._cmd_open_project({"project": action["project"]}, client)
        # Everything else (navigate, open a diff or output, tabs, expand, previews, approvals) happens on the TV itself.
        return {"ok": True, "client_action": action}

    async def _cmd_reset_state(self, args, client):
        if client.role != "admin":
            raise CommandError("Only the FireDev CLI can reset a project")
        s = self.session(args.get("project"))
        await s.reset_state(forget_thread=bool(args.get("forget_thread")))
        return {"ok": True, "message": f"Reset {s.project.name}"}

    async def _cmd_voice_link(self, args, client):
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV can create a phone microphone link")
        st = self.config.settings
        base = (st.public_url or "").rstrip("/")
        if not base and st.lan:
            # Local-first: the phone on the same Wi-Fi reaches this computer's LAN listener directly.
            from .lan import lan_addresses
            addrs = lan_addresses()
            base = f"https://{addrs[0]}:{st.lan_port}" if addrs else ""
        if not base:
            b = args.get("base") or ""
            if b and "127.0.0.1" not in b and "localhost" not in b:
                base = b.rstrip("/")
        if not base:
            return {"ok": False, "message": "The phone page needs this machine on your Wi-Fi (LAN mode) or a public HTTPS address. "
                                            "Over SSH, use the TV's microphone."}
        token, dev = self.devices.create(args.get("name") or "Phone microphone", "voice", ttl=12 * 3600)
        return {"ok": True, "url": f"{base}/voice#t={token}", "device": dev["id"], "expires_in_hours": 12}

    # --- live preview -----------------------------------------------------------------------------------------

    def _preview_session(self, args: dict) -> ProjectSession:
        s = self.session(args.get("project"))
        if s.project.id not in self.previews.previews:
            raise CommandError("No preview for that project")
        return s

    async def _cmd_preview_status(self, args, client):
        s = self._preview_session(args)
        if s.conn_state == "connected" and (args.get("refresh") or not self.previews.previews[s.project.id].detected):
            await self.previews.refresh(s.project.id)
        return {"ok": True, "preview": self.previews.previews[s.project.id].to_dict()}

    async def _cmd_preview_open(self, args, client):
        """A one-time address of the running preview for this TV. With start, the user asked: start it if it isn't running."""
        if client.role not in ("tv", "admin"):
            raise CommandError("Only a paired TV can show previews")
        s = self._preview_session(args)
        await self._ensure_open(s)
        pid = s.project.id
        if args.get("start"):
            p = await self.previews.ensure_running(pid, "tv", install=bool(args.get("install")), dir=args.get("dir"), wait=False)
        else:
            p = await self.previews.refresh(pid, quiet=True) if not self.previews.previews[pid].detected else self.previews.previews[pid]
        shots = bool(args.get("screenshots")) and bool(p.screenshots)
        if p.status != "running" and not shots:
            return {"ok": True, "preview": p.to_dict(), "url": None}
        base = self.previews.gateway_base(args.get("base"), args.get("preview_base"))
        if not base:
            return {"ok": False, "preview": p.to_dict(), "url": None,
                    "message": "The preview gateway has no public address. Set server.preview_url in firedev.yaml."}
        tok = self.previews.issue_ticket(client, pid, "/__firedev/shots" if shots else args.get("path"))
        return {"ok": True, "preview": p.to_dict(), "url": f"{base}/__firedev/enter?t={tok}", "origin": base}

    async def _cmd_preview_start(self, args, client):
        if client.role not in ("tv", "admin", "voice", "assistant"):
            raise CommandError("Not allowed")
        s = self._preview_session(args)
        await self._ensure_open(s)
        p = await self.previews.ensure_running(s.project.id, "tv" if client.role in ("tv", "admin") else client.role,
                                               install=bool(args.get("install")), dir=args.get("dir"), wait=False)
        return {"ok": True, "preview": p.to_dict()}

    async def _cmd_preview_stop(self, args, client):
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV can stop the preview server")
        s = self._preview_session(args)
        p = await self.previews.stop_server(s.project.id)
        return {"ok": True, "preview": p.to_dict()}

    async def _cmd_preview_view(self, args, client):
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV reports what it shows")
        s = self._preview_session(args)
        p = self.previews.set_viewer(client, s.project.id, bool(args.get("visible")), args.get("layout"), args.get("viewport"))
        return {"ok": True, "preview": p.to_dict()}

    async def _cmd_preview_report(self, args, client):
        if client.role not in ("tv", "admin"):
            raise CommandError("Only the TV reports what it shows")
        s = self._preview_session(args)
        self.previews.report(s.project.id, {k: args.get(k) for k in ("page", "title", "error") if k in args})
        return {"ok": True}

    async def _preview_intent(self, a: dict, client: Client, pid: str | None) -> dict:
        s = self.session(pid)
        op = a["op"]
        ui = {"type": "preview", "op": op, **{k: v for k, v in a.items() if k != "op"}}
        quiet = {"ok": True, "message": "", "speak": False, "client_action": ui}
        if op in ("viewport", "zoom", "size", "nav", "close"):
            if op == "close":
                return {**quiet, "navigate": "workspace"}
            return quiet
        await self._ensure_open(s)
        if op == "status":
            p = await self.previews.refresh(s.project.id)
            return {"ok": True, "message": self.previews.spoken(p)}
        if op == "stop":
            p = await self.previews.stop_server(s.project.id)
            return {"ok": True, "message": p.message, "client_action": {"type": "preview", "op": "state"}}
        if op == "restart":
            p = self.previews.previews[s.project.id]
            if p.managed:
                await self.previews.stop_server(s.project.id)
            op = "start"
        p = await self.previews.refresh(s.project.id, quiet=True)
        if op in ("start", "install") or (op == "show" and p.status in ("idle", "failed") and not p.needs_install):
            p = await self.previews.ensure_running(s.project.id, "voice", install=op == "install", wait=False)
        ui["op"] = "show"
        return {"ok": True, "message": self.previews.spoken(p, showing=True), "client_action": ui}

    # --- voice ----------------------------------------------------------------------------------------------

    async def _cmd_voice_command(self, args, client):
        return await self.voice(args.get("text", ""), client, source=args.get("source") or client.role, project=args.get("project"),
                                ui=args.get("ui"))

    def _context(self, pid: str | None) -> dict:
        if not pid or pid not in self.sessions:
            return {"project": None, "pending_approval": bool(self.approvals), "default": self._default_project()}
        s = self.sessions[pid]
        return {"project": pid, "agent": s.adapter.display, "busy": s.busy, "has_surface": bool(s.surface),
                "actions": {a.id: {"kind": a.kind, "title": a.title} for a in s.project.actions.values()},
                "last_build_failed": s.build.get("state") == "failed", "last_tests_failed": s.tests.get("state") == "failed",
                "pending_approval": any(a.project == pid for a in self.approvals.values()),
                **self.previews.context(pid)}

    async def voice(self, text: str, client: Client, source: str = "voice", project: str | None = None, ui: dict | None = None) -> dict:
        pid = project or self.focus_project
        intent = intents.parse(text, [s.summary() for s in self.sessions.values()], self._context(pid))
        if intent.args.get("any_project") and not pid:
            pid = self._default_project()
        self.bus.emit("voice_command", pid, None, text=text, intent=intent.to_dict(), source=source,
                      agent=self.sessions[pid].adapter.display if pid in self.sessions else None)
        if pid in self.sessions and not intent.args.get("then"):  # a routed request is recorded where it goes
            self.sessions[pid].add_turn("user", text, kind="request", source=source)
        try:
            result = await self._run_intent(intent, client, source, pid, ui)
        except CommandError as e:
            result = {"ok": False, "message": str(e)}
        except genui.SpecError as e:
            result = {"ok": False, "message": f"I couldn't change the screen: {e}"}
        reply = result.get("message")
        if result.get("project") in self.sessions:
            pid = result["project"]  # routed to another project, or a screen shown in one
        self.bus.emit("voice_reply", pid, None, text=text, intent=intent.name, reply=reply, ok=result.get("ok", True),
                      navigate=result.get("navigate"), speech=result.get("speech"), listen=result.get("listen"),
                      client_action=result.get("client_action"))
        # FireDev's own short answers are spoken too (as FireDev, not as the agent). Agent tasks are answered by the
        # agent itself when its turn ends; only a queued one gets a word now so the user knows it was heard.
        if reply and result.get("speak", True) and (intent.name != "send_agent_task" or result.get("queued") or not result.get("ok", True)):
            self.say(pid, reply)
        if reply and pid in self.sessions and intent.name != "send_agent_task":
            self.sessions[pid].add_turn("firedev", reply, kind="reply")
        return {"intent": intent.to_dict(), **result}

    async def _agents_dashboard(self, pid: str | None) -> dict:
        """"What are my agents doing?": a live screen at once (no agent turn needed) and a spoken answer."""
        s = self.session(pid or self._default_project())
        agents = await self.scan_agents(s)
        working = [a for a in agents if a.get("state") == "working" or a.get("task")]
        machine = [a for a in agents if a.get("source") != "firedev"]
        spec = {"schema": "firedev.ui/1", "title": "Your agents", "subtitle": "Live · keeps itself up to date",
                "provenance": {"kind": "live", "sources": ["FireDev", "processes on " + s.project.machine.display]},
                "root": {"type": "stack", "gap": "l", "children": [
                    {"type": "grid", "columns": 3, "children": [
                        {"type": "metric", "id": "m_working", "label": "Working now", "value": len(working), "tone": "info" if working else "neutral"},
                        {"type": "metric", "id": "m_projects", "label": "FireDev projects", "value": len(self.sessions)},
                        {"type": "metric", "id": "m_other", "label": "Other agents on the machine", "value": len(machine)}]},
                    {"type": "agent_status", "id": "agents", "source": "agents", "title": "Agents", "size": "xl"}]}}
        s.show_surface(spec, "firedev")
        if working:
            said = "; ".join(f"{a['agent']} on {a['project']}: {(a.get('task') or 'working').rstrip('.')}" for a in working[:3])
            msg = f"{len(working)} agent{' is' if len(working) == 1 else 's are'} busy. {said}."
        else:
            msg = f"All quiet: none of your {len(self.sessions)} FireDev agents are working right now."
            if machine:
                msg += f" There {'is' if len(machine) == 1 else 'are'} {len(machine)} other agent process{'' if len(machine) == 1 else 'es'} on the machine."
        return {"ok": True, "message": msg, "navigate": "surface", "project": s.project.id}

    def say(self, pid: str | None, text: str) -> None:
        if pid in self.sessions:
            s = self.sessions[pid]
            s.bus.emit("agent_speech", pid, None, id=_rid("s"), text=text, kind="firedev", agent="FireDev")
        else:
            self.bus.emit("agent_speech", None, None, id=_rid("s"), text=text, kind="firedev", agent="FireDev")

    def _default_project(self) -> str | None:
        connected = [s for s in self.sessions.values() if s.conn_state == "connected"]
        return (connected or list(self.sessions.values()) or [None])[0].project.id if (connected or self.sessions) else None

    async def _run_intent(self, intent: intents.Intent, client: Client, source: str, pid: str | None, ui: dict | None = None) -> dict:
        n, a = intent.name, intent.args
        if n in ("ui_edit", "ui_back") and pid in self.sessions:
            s = self.sessions[pid]
            if n == "ui_back":
                return {"ok": True, "message": "Here's the previous screen." if s.surface_back() else "There's no earlier screen.",
                        "navigate": "surface"}
            if s.surface and a.get("op"):
                quick = genui.quick_edit(s.surface, a["op"], (ui or {}).get("focused"), a.get("target"), a.get("kind"))
                if quick:
                    ops, msg = quick
                    if ops:
                        s.patch_surface(ops, "voice")
                    return {"ok": True, "message": msg, "navigate": "surface"}
                if a["op"] in ("expand", "collapse"):
                    # Probably a card the TV draws from live data (an agent, an approval): the TV finds it by name.
                    return {"ok": True, "message": "", "navigate": "surface", "speak": False,
                            "client_action": {"type": "expand_text", "text": a.get("target") or "", "op": a["op"]}}
            if not s.surface:
                return {"ok": False, "message": "There's nothing on screen to change yet. Ask me to show you something."}
            await self._ensure_open(s)
            task = s.submit(Task(id=_rid("t"), title=intent.title, kind="agent", text=a.get("task") or intent.title,
                                 prompt=a.get("task") or intent.title, mode="ui_edit", source=source, lane="ui", ui=ui))
            return {"ok": True, "task": task.id, "message": "Changing the screen.", "navigate": "surface", "speak": False}
        if n == "navigate":
            return {"ok": True, "message": "", "navigate": a["to"], "speak": False}
        if n == "preview":
            return await self._preview_intent(a, client, pid)
        if n in ("speech_stop", "conversation_end"):
            return {"ok": True, "message": "Okay." if n == "conversation_end" else "", "speech": "stop",
                    "listen": "off" if n == "conversation_end" else None, "speak": n == "conversation_end"}
        if n == "open_project":
            res = await self._cmd_open_project({"project": a["project"], "conversational": not a.get("then")}, client)
            res["navigate"] = "workspace"
            if res.get("ok") and a.get("then"):
                name = self.sessions[a["project"]].project.name
                follow = await self.voice(a["then"], client, source, a["project"])
                # Moving to another project is worth a word; the follow-up's own answer comes from the agent.
                res["message"] = f"I'll do that in {name}." if a.get("routed") or follow.get("intent", {}).get("name") == "send_agent_task" \
                    else f"{res['message']} {follow.get('message', '')}".strip()
                res["navigate"] = follow.get("navigate") or "workspace"
            return res
        if n == "go_home":
            return {"ok": True, "message": "", "navigate": "home", "speak": False}
        if n == "show_projects":
            names = [s.project.name for s in self.sessions.values()]
            return {"ok": True, "navigate": "projects",
                    "message": f"You have {len(names)} project{'s' if len(names) != 1 else ''}: {', '.join(names)}."}
        if n == "agents_dashboard":
            return await self._agents_dashboard(pid)
        if n == "send_agent_task":
            if a.get("lane") == "ui":
                s = self.session(pid)
                await self._ensure_open(s)
                task = s.submit(Task(id=_rid("t"), title=intent.title, kind="agent", text=a["task"], prompt=a["task"],
                                     mode=a.get("mode") or "present", source=source, lane="ui", ui=ui))
                return {"ok": True, "task": task.id, "message": "Putting it on screen.", "navigate": "surface", "speak": False}
            res = await self._cmd_send_agent_task({"project": pid, "task": a["task"], "title": intent.title, "mode": a.get("mode"),
                                                   "source": source, "text": a.get("text"), "ui": ui}, client)
            if res.get("queued"):
                s = self.session(pid)
                cur = s.current.title.lower() if s.current else "the current task"
                res["message"] = f"I'll do that next, after {cur}."
            res.setdefault("navigate", "workspace")  # the TV follows the work (or keeps the preview it is showing)
            res.setdefault("project", pid)
            return res
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


def _form_text(node: dict, values: dict) -> str:
    parts = []
    for f in node.get("fields") or []:
        v = values.get(f["key"], f.get("value"))
        if f["kind"] == "choice" and f.get("options") and v not in f["options"]:
            raise CommandError(f"{f['label']}: choose one of the options")
        if f["kind"] == "toggle":
            v = "yes" if v in (True, "yes", "on", 1) else "no"
        if f["kind"] == "number":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise CommandError(f"{f['label']} must be a number") from None
            if ("min" in f and v < f["min"]) or ("max" in f and v > f["max"]):
                raise CommandError(f"{f['label']} is out of range")
            v = int(v) if v == int(v) else v
        parts.append(f"{f['label']}: {v}")
    head = node.get("prompt") or node.get("title") or "My answers"
    return f"{head} — " + "; ".join(parts)
