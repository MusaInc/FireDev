"""Codex CLI adapter: `codex exec --json` turns inside Codex's workspace-write sandbox, resumed by thread id so
the conversation persists across tasks, reconnects and FireDev restarts."""

from __future__ import annotations

import json
import shlex

from .base import AgentAdapter, TurnParser


class CodexAdapter(AgentAdapter):
    id = "codex"
    display = "Codex"
    binary = "codex"

    def turn_command(self, prompt_file: str, thread_id: str | None, cwd: str, home: str) -> str:
        roots = [self.expand(p, home) for p in self.options.get("writable_roots", [])]
        cfg = [
            "-c", 'sandbox_mode="workspace-write"',
            "-c", "sandbox_workspace_write.network_access=false",
            "-c", 'approval_policy="never"',
        ]
        if roots:
            cfg += ["-c", "sandbox_workspace_write.writable_roots=" + json.dumps(roots)]
        if self.options.get("model"):
            cfg += ["-m", str(self.options["model"])]
        if self.options.get("reasoning_effort"):
            cfg += ["-c", f'model_reasoning_effort="{self.options["reasoning_effort"]}"']
        args = [self.binary, "exec"]
        if thread_id:
            args += ["resume", thread_id]
        args += ["--json", "--skip-git-repo-check", *cfg, "-"]
        return f"cd {shlex.quote(cwd)} && " + " ".join(shlex.quote(a) for a in args) + f" < {shlex.quote(prompt_file)}"

    def new_parser(self) -> TurnParser:
        return CodexParser()


class CodexParser(TurnParser):
    def feed(self, line: str) -> list[dict]:
        line = line.strip()
        if not line.startswith("{"):
            return []
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return []
        t = ev.get("type")
        if t == "thread.started":
            return [{"kind": "thread", "thread_id": ev.get("thread_id")}]
        if t == "turn.completed":
            return [{"kind": "turn_completed", "usage": ev.get("usage") or {}}]
        if t == "turn.failed":
            return [{"kind": "error", "message": (ev.get("error") or {}).get("message", "Codex failed")}]
        if t == "error":
            return [{"kind": "error", "message": ev.get("message", "Codex error"), "transient": "reconnect" in str(ev.get("message", "")).lower()}]
        if t not in ("item.started", "item.updated", "item.completed"):
            return []
        item = ev.get("item") or {}
        it = item.get("type")
        iid = item.get("id")
        done = t == "item.completed"
        if it == "agent_message" and done:
            return [{"kind": "message", "item_id": iid, "text": item.get("text", "")}]
        if it == "reasoning" and done and item.get("text"):
            return [{"kind": "thinking", "item_id": iid, "text": item["text"]}]
        if it == "command_execution":
            if t == "item.started":
                return [{"kind": "command_started", "item_id": iid, "command": item.get("command", "")}]
            if done:
                return [{"kind": "command_finished", "item_id": iid, "command": item.get("command", ""),
                         "exit_code": item.get("exit_code"), "output": item.get("aggregated_output", "")}]
        if it == "file_change" and done:
            return [{"kind": "file_changed", "item_id": iid, "path": c.get("path", ""), "change": c.get("kind", "update")}
                    for c in item.get("changes") or []]
        if it == "todo_list":
            return [{"kind": "plan", "items": [{"text": x.get("text", ""), "done": bool(x.get("completed"))} for x in item.get("items") or []]}]
        if it == "error" and done:
            return [{"kind": "error", "message": item.get("message", "")}]
        return []
