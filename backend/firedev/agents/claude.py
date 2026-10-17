"""Claude Code adapter: `claude -p --output-format stream-json` turns, resumed by session id.

Claude Code in print mode can't ask for permission, so anything outside the allow-list is denied. The allow-list
is FireDev's helper plus read-only commands; edits go through Claude Code's own edit tools (acceptEdits).
"""

from __future__ import annotations

import json
import shlex

from .base import AgentAdapter, TurnParser

ALLOWED_TOOLS = [
    "Read", "Grep", "Glob", "LS", "Edit", "MultiEdit", "Write", "TodoWrite",
    "Bash(firedev:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(rg:*)", "Bash(grep:*)", "Bash(find:*)", "Bash(wc:*)",
]


class ClaudeAdapter(AgentAdapter):
    id = "claude"
    display = "Claude Code"
    binary = "claude"

    def turn_command(self, prompt_file: str, thread_id: str | None, cwd: str, home: str) -> str:
        args = [self.binary, "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
                "--allowedTools", ",".join(self.options.get("allowed_tools", ALLOWED_TOOLS))]
        if thread_id:
            args += ["--resume", thread_id]
        if self.options.get("model"):
            args += ["--model", str(self.options["model"])]
        return f"cd {shlex.quote(cwd)} && " + " ".join(shlex.quote(a) for a in args) + f" < {shlex.quote(prompt_file)}"

    def new_parser(self) -> TurnParser:
        return ClaudeParser()


_EDIT_TOOLS = {"Edit": "update", "MultiEdit": "update", "Write": "add", "NotebookEdit": "update"}


class ClaudeParser(TurnParser):
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}
        self.sent_thread = False

    def feed(self, line: str) -> list[dict]:
        line = line.strip()
        if not line.startswith("{"):
            return []
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return []
        out: list[dict] = []
        t = ev.get("type")
        if ev.get("session_id") and not self.sent_thread:
            self.sent_thread = True
            out.append({"kind": "thread", "thread_id": ev["session_id"]})
        if t == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                bt = block.get("type")
                if bt == "text" and block.get("text", "").strip():
                    out.append({"kind": "message", "item_id": None, "text": block["text"]})
                elif bt == "thinking" and block.get("thinking"):
                    out.append({"kind": "thinking", "item_id": None, "text": block["thinking"][:400]})
                elif bt == "tool_use":
                    name, inp, tid = block.get("name"), block.get("input") or {}, block.get("id")
                    if name == "Bash":
                        self.pending[tid] = {"command": inp.get("command", "")}
                        out.append({"kind": "command_started", "item_id": tid, "command": inp.get("command", "")})
                    elif name in _EDIT_TOOLS and inp.get("file_path"):
                        out.append({"kind": "file_changed", "item_id": tid, "path": inp["file_path"], "change": _EDIT_TOOLS[name]})
                    elif name == "TodoWrite":
                        out.append({"kind": "plan", "items": [{"text": x.get("content", ""), "done": x.get("status") == "completed"} for x in inp.get("todos") or []]})
                    elif name in {"Read", "Grep", "Glob", "LS"}:
                        target = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
                        out.append({"kind": "thinking", "item_id": tid, "text": f"{name} {target}".strip()})
        elif t == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in self.pending:
                    cmd = self.pending.pop(block["tool_use_id"])
                    content = block.get("content")
                    if isinstance(content, list):
                        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
                    out.append({"kind": "command_finished", "item_id": block["tool_use_id"], "command": cmd["command"],
                                "exit_code": 1 if block.get("is_error") else 0, "output": content or ""})
        elif t == "result":
            if ev.get("subtype") == "success" and not ev.get("is_error"):
                out.append({"kind": "turn_completed", "usage": ev.get("usage") or {}})
            else:
                out.append({"kind": "error", "message": ev.get("result") or ev.get("subtype") or "Claude Code failed"})
        return out
