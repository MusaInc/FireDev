"""The agent adapter interface. An adapter knows how to start one turn of its agent as a shell command on the
remote machine, and how to turn that agent's output stream into FireDev's normalised agent events.

Normalised events (dicts with a "kind"):
  thread            thread_id                      the agent's persistent conversation id
  message           text                           something the agent said
  thinking          text                           a short reasoning summary
  plan              items=[{text, done}]           the agent's own checklist
  command_started   item_id, command
  command_finished  item_id, command, exit_code, output
  file_changed      item_id, path, change (add|update|delete)
  turn_completed    usage
  error             message
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod

# Added to every task so the agent knows how FireDev works. Kept short: it is sent with each turn.
FIREDEV_BRIEF = """You are working through FireDev: the user is speaking to you from a TV across the room and watches your progress there.

Rules for this environment:
- Your shell is sandboxed: no network, no sudo, and you can only write inside the project.
- Run the project's tests and builds with `firedev run <action>` (for example `firedev run test`, `firedev run build`). FireDev runs them for you and prints the output. `firedev actions` lists the actions. Don't call gradle, npm or similar build tools directly; they can't run in the sandbox.
- Anything outside the project (deploying, git push, installing packages, restarting services, sudo) must go through `firedev request --reason "<why, one sentence>" -- <command>`. The user approves or rejects it on the TV; the command blocks until then and prints the result. Never try to work around a rejection.
- Keep edits focused on what was asked.
- Finish with a short summary for the TV: one headline sentence (under 80 characters), then at most three short bullet points. No code blocks in the summary.
"""


class AgentAdapter(ABC):
    id: str = ""
    display: str = ""
    binary: str = ""

    def __init__(self, options: dict | None = None) -> None:
        self.options = dict(options or {})
        self.binary = self.options.get("binary") or self.binary

    def check_command(self) -> str:
        """A shell command that prints the agent's version, or fails if the agent isn't installed."""
        return f"{shlex.quote(self.binary)} --version"

    @abstractmethod
    def turn_command(self, prompt_file: str, thread_id: str | None, cwd: str, home: str) -> str:
        """The shell command that runs one turn, reading the prompt from prompt_file and writing its event
        stream to stdout."""

    @abstractmethod
    def new_parser(self) -> "TurnParser":
        ...

    def compose_prompt(self, task: str, context: str | None = None, first_turn: bool = True, tool: str = "firedev") -> str:
        """tool is the absolute path of the FireDev helper: sandboxed login shells don't keep FireDev's PATH."""
        parts = [FIREDEV_BRIEF] if first_turn else [f"(FireDev rules from the start of this conversation still apply. The helper is `{tool}`.)"]
        if context:
            parts.append(f"Context from FireDev:\n{context}")
        parts.append(f"The user's request:\n{task}")
        return "\n\n".join(parts).replace("`firedev ", f"`{tool} ")

    def expand(self, path: str, home: str) -> str:
        return home + path[1:] if path.startswith("~") else path


class TurnParser(ABC):
    @abstractmethod
    def feed(self, line: str) -> list[dict]:
        ...
