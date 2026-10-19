"""Structured events and the in-process bus that fans them out to TVs, phones, MCP clients and history.

Every event has the same envelope: {type, id, ts, project, task, data}. Clients never scrape terminal
text for state: the session turns agent and command output into these events, and a full project
snapshot ("state") is pushed after changes, so a client that reconnects is correct immediately.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

EVENT_TYPES = {
    "session_connecting", "session_connected", "session_disconnected", "session_error",
    "voice_listening", "voice_partial", "voice_command", "voice_reply",
    "agent_started", "agent_message", "agent_status", "agent_plan",
    "command_started", "command_output", "command_finished",
    "file_changed",
    "tests_started", "tests_finished", "build_started", "build_finished", "deploy_started", "deploy_finished",
    "approval_required", "approval_accepted", "approval_rejected",
    "task_queued", "task_started", "task_completed", "task_failed", "task_cancelled",
    "changes_undone", "state", "notice",
    "ui_surface", "ui_patch", "ui_data", "agent_speech",
    "preview_state", "preview_offer", "preview_reload", "preview_control", "voice_link", "code_change",
}

_ids = itertools.count(1)


@dataclass
class Event:
    type: str
    project: str | None = None
    task: str | None = None
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_ids))

    def __post_init__(self) -> None:
        if self.type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {self.type}")

    def to_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "ts": round(self.ts, 3), "project": self.project, "task": self.task, "data": self.data}


# Too chatty or too large to keep in history files.
_NOT_PERSISTED = {"command_output", "state", "voice_partial", "voice_listening", "ui_surface", "ui_patch", "ui_data",
                  "preview_state", "preview_reload", "preview_control"}


class Bus:
    """Fan-out of events to subscribers (each an asyncio.Queue), plus JSONL history per project."""

    def __init__(self, history_dir: Path | None = None) -> None:
        self._subs: set[asyncio.Queue] = set()
        self.history_dir = history_dir
        if history_dir:
            history_dir.mkdir(parents=True, exist_ok=True)

    def subscribe(self, maxsize: int = 2000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: Event) -> Event:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client loses old output rather than stalling everyone; the next state push resyncs it.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        if self.history_dir and event.project and event.type not in _NOT_PERSISTED:
            with (self.history_dir / f"{event.project}.jsonl").open("a") as f:
                f.write(json.dumps(event.to_dict()) + "\n")
        return event

    def emit(self, type_: str, project_id: str | None = None, task_id: str | None = None, /, **data) -> Event:
        return self.publish(Event(type=type_, project=project_id, task=task_id, data=data))

    def history(self, project: str, limit: int = 300) -> list[dict]:
        if not self.history_dir:
            return []
        p = self.history_dir / f"{project}.jsonl"
        if not p.exists():
            return []
        lines = p.read_text().splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
