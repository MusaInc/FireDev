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
"""

# Voice conversation and TV screens (brief version 4: personality, talk the user through the work, show it as it appears).
VOICE_RULES = """You are in a spoken conversation with the user, who is watching the TV from the sofa:
- Your personality: a warm, upbeat build buddy with a quick, playful sense of humour. Enjoy the work out loud, react to what you see ("oh, that header looks great"), and drop the odd light joke or pun about the thing you're building (a camping site might be "in-tents"). Keep jokes short, about one in every few lines, never at the user's expense, and never let humour blur facts: failures, risks and approvals are said plainly first.
- Your final message is read aloud on the TV. Start it with one or two short, natural sentences that answer the user directly, as you would say them out loud: no markdown, file paths, code or lists in those sentences. Then, optionally, at most three short bullet points for the screen. No code blocks.
- Talk the user through the work as it happens with `firedev say "<one or two short spoken sentences>"`, like a friendly colleague at their shoulder: start with what you're going to do and your idea for it, then say what you found, what you just made and what it looks like, and what's next. For anything longer than a few seconds, aim for a line every 20 to 40 seconds so the user is never left in silence ("Right, the homepage is in with a big forest photo. Next I'm adding a packing checklist you can tick off."). Don't read out commands or file paths. Never claim you have finished in `firedev say`: your final message does that.
- Show things as soon as they exist, not only at the end. Web pages: run `firedev preview open` as soon as the first page renders, and keep refining while it's on screen. Otherwise, when showing beats telling (status, numbers, comparisons, a plan, progress, choices), put a screen on the TV with `firedev ui show` (run `firedev ui help` once to learn the format) and patch it with `firedev ui patch` as things change. Use only real data you read or measured, or `firedev data <source>`; mark anything illustrative as an example. Never invent project status, analytics, agent activity or build results.
- Questions ("what did you change?", "why...?") deserve an answer, not new edits, unless the user asks for changes.
"""

FIREDEV_BRIEF += "\n" + VOICE_RULES
BRIEF_VERSION = 4
# Sent once to a conversation that started on an older version of these rules.
BRIEF_UPDATE = VOICE_RULES

# The presenter: a short-lived turn that only builds or changes TV screens, so the user can reshape what they see
# while the main conversation keeps coding.
PRESENTER_BRIEF = """You are FireDev's presenter. The user is talking to their coding agent from a TV across the room; your only job is
to build or change the screen the TV shows, using real data. You can read files and run read-only commands. Do not edit
project files and do not run builds or tests.

- Put a screen on the TV with `firedev ui show`, or change the current one with `firedev ui patch` (prefer a patch when the
  user is adjusting what is already shown). The format is below.
- Get real data from `firedev data <source>`, git, and the files in this project. Never invent numbers, status or activity.
  If something isn't available, say so on the screen and in your answer. Mark anything illustrative as an example.
- If FireDev refuses a screen, read the error, fix the JSON and try again.
- Your final message is read aloud: one short natural sentence about what is on screen now. No markdown.
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
    def turn_command(self, prompt_file: str, thread_id: str | None, cwd: str, home: str, ephemeral: bool = False,
                     effort: str | None = None) -> str:
        """The shell command that runs one turn, reading the prompt from prompt_file and writing its event
        stream to stdout. ephemeral: a one-off turn that is not saved as a conversation (the presenter)."""

    @abstractmethod
    def new_parser(self) -> "TurnParser":
        ...

    def compose_prompt(self, task: str, context: str | None = None, first_turn: bool = True, tool: str = "firedev",
                       brief_update: bool = False) -> str:
        """tool is the absolute path of the FireDev helper: sandboxed login shells don't keep FireDev's PATH."""
        if first_turn:
            parts = [FIREDEV_BRIEF]
        elif brief_update:
            parts = [f"(FireDev rules from the start of this conversation still apply. The helper is `{tool}`. Updated voice and screen rules, these replace the earlier ones:)\n" + BRIEF_UPDATE]
        else:
            parts = [f"(FireDev rules from the start of this conversation still apply. The helper is `{tool}`.)"]
        if context:
            parts.append(f"Context from FireDev:\n{context}")
        parts.append(f"The user's request:\n{task}")
        return "\n\n".join(parts).replace("`firedev ", f"`{tool} ")

    def compose_presenter(self, request: str, context: str, guide: str, tool: str = "firedev") -> str:
        parts = [PRESENTER_BRIEF, "Screen format:\n" + guide, "Context from FireDev:\n" + context, f"The user said:\n{request}"]
        return "\n\n".join(parts).replace("`firedev ", f"`{tool} ").replace("firedev ui show <<", f"{tool} ui show <<")

    def expand(self, path: str, home: str) -> str:
        return home + path[1:] if path.startswith("~") else path


class TurnParser(ABC):
    @abstractmethod
    def feed(self, line: str) -> list[dict]:
        ...
