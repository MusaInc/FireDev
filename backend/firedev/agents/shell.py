"""Plain shell "agent": no AI. A task is a single command, which FireDev has already classified (and, at level 2+,
had approved on the TV) before it gets here."""

from __future__ import annotations

import shlex

from .base import AgentAdapter, TurnParser


class ShellAdapter(AgentAdapter):
    id = "shell"
    display = "Shell"
    binary = "bash"

    def check_command(self) -> str:
        return "bash --version | head -1"

    def compose_prompt(self, task: str, context: str | None = None, first_turn: bool = True, tool: str = "firedev") -> str:
        return task

    def turn_command(self, prompt_file: str, thread_id: str | None, cwd: str, home: str, ephemeral: bool = False,
                     effort: str | None = None) -> str:
        return f"cd {shlex.quote(cwd)} && bash {shlex.quote(prompt_file)}"

    def new_parser(self) -> TurnParser:
        return ShellParser()


class ShellParser(TurnParser):
    def feed(self, line: str) -> list[dict]:
        return [{"kind": "output", "text": line}]
