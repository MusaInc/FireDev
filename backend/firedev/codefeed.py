"""Live code: the actual code an agent writes, as it lands, for the TV's code panel.

Agents report finished edits (a patch applied, a file written), never keystrokes. After each one this reads the
file back from the project's machine and diffs it against the version seen before (the start of the task: git's
HEAD, or empty for a new file), and emits a `code_change` event with the changed regions and a little context. The TV
replays those real lines at speed and says that it is a replay; nothing here is ever invented.

A file seen for the first time without a baseline (no git, edited before FireDev looked) has no known "before":
it is shown as it now is, with nothing typed.
"""

from __future__ import annotations

import asyncio
import difflib
import posixpath
import shlex
from typing import TYPE_CHECKING

from .ssh import RemoteError

if TYPE_CHECKING:
    from .session import ProjectSession

LANGS = {"html": "html", "htm": "html", "css": "css", "scss": "css", "less": "css", "js": "js", "mjs": "js", "cjs": "js",
         "jsx": "js", "ts": "js", "tsx": "js", "vue": "html", "svelte": "html", "astro": "html", "json": "json", "py": "py",
         "md": "md", "kt": "js", "java": "js", "go": "js", "rs": "js", "sh": "sh", "yaml": "yaml", "yml": "yaml", "toml": "yaml",
         "txt": "text", "svg": "html", "xml": "html", "sql": "sql", "rb": "py", "php": "js", "c": "js", "h": "js", "swift": "js"}
MAX_BYTES = 200_000
MAX_HUNKS = 4
MAX_ADDED = 160  # lines replayed per region; the rest of a large region appears at once
CONTEXT_BEFORE, CONTEXT_AFTER = 12, 6
PRIME_FILES = 120
MARK = "@@firedev-file "  # separates files in the snapshot at the start of a task


def lang_of(path: str) -> str | None:
    ext = posixpath.basename(path).rsplit(".", 1)[-1].lower() if "." in posixpath.basename(path) else ""
    return LANGS.get(ext)


def regions(before: list[str], after: list[str]) -> list[dict]:
    """The changed regions of `after`, each with its context: {at, before, removed, added, after, more}."""
    out = []
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        added = after[j1:j2]
        out.append({"at": j1, "before": after[max(0, j1 - CONTEXT_BEFORE):j1], "removed": before[i1:i2][:40],
                    "added": added[:MAX_ADDED], "more": max(0, len(added) - MAX_ADDED),
                    "after": after[j2:j2 + CONTEXT_AFTER]})
    # The biggest changes first if there are many small ones; then back in file order.
    if len(out) > MAX_HUNKS:
        out = sorted(sorted(out, key=lambda r: -(len(r["added"]) + len(r["removed"])))[:MAX_HUNKS], key=lambda r: r["at"])
    return out


class CodeFeed:
    def __init__(self) -> None:
        self.seen: dict[tuple[str, str], list[str] | None] = {}  # (project, path) -> the lines last seen
        self.locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.priming: dict[str, asyncio.Task] = {}

    def prime(self, s: "ProjectSession") -> None:
        """At the start of an agent task: remember the project's source files as they are now, so each replay shows
        only what this task changed (not everything since the last commit)."""
        t = self.priming.get(s.project.id)
        if t and not t.done():
            return
        self.priming[s.project.id] = asyncio.get_running_loop().create_task(self._prime(s))

    async def _prime(self, s: "ProjectSession") -> None:
        exts = sorted({e for e in LANGS if LANGS[e] not in ("text",)})
        names = " -o ".join(f"-name '*.{e}'" for e in exts)
        prune = " -o ".join(f"-name {n}" for n in ("node_modules", ".git", ".firedev", "dist", "build", ".next", ".cache", "__pycache__", ".venv"))
        script = (f"cd {shlex.quote(s.path)} 2>/dev/null && find . \\( {prune} \\) -prune -o -type f \\( {names} \\) -size -{MAX_BYTES // 1024}k -print 2>/dev/null "
                  f"| head -{PRIME_FILES} | while IFS= read -r f; do printf '\\n{MARK}%s\\n' \"$f\"; head -c {MAX_BYTES} \"$f\"; done")
        try:
            _, out, _ = await s.remote.run(script, timeout=20)
        except (RemoteError, OSError):
            return
        for chunk in out.split("\n" + MARK)[1:]:
            name, _, body = chunk.partition("\n")
            rel = name[2:] if name.startswith("./") else name
            self.seen[(s.project.id, rel)] = body.splitlines()

    async def capture(self, s: "ProjectSession", task_id: str | None, rel: str, change: str) -> None:
        # One file's edits are read back in order, so each diff is against the version before it.
        async with self.locks.setdefault((s.project.id, rel), asyncio.Lock()):
            await self._capture(s, task_id, rel, change)

    async def _capture(self, s: "ProjectSession", task_id: str | None, rel: str, change: str) -> None:
        lang = lang_of(rel)
        if not lang or not s.remote.connected:
            return
        key = (s.project.id, rel)
        full = posixpath.join(s.path, rel)
        if change == "delete":
            self.seen.pop(key, None)
            s.emit("code_change", task_id, path=rel, lang=lang, change="delete", regions=[], lines=0, known=True)
            return
        try:
            data = await s.remote.read_bytes(full, limit=MAX_BYTES)
        except (RemoteError, OSError):
            return
        if b"\0" in data[:4000]:
            return  # binary
        text = data.decode("utf-8", "replace")
        after = text.splitlines()
        before = self.seen.get(key)
        known = True
        pending = self.priming.get(s.project.id)
        if before is None and pending and not pending.done():
            await asyncio.shield(pending)
            before = self.seen.get(key)
        if before is None:
            if change == "add":
                before = []
            else:
                before = await self._baseline(s, rel)
                known = before is not None
        self.seen[key] = after
        if known:
            regs = [r for r in regions(before, after) if r["added"] or r["removed"] or r["more"]]
            if not regs:
                return  # saved without a change
        else:
            # No "before" to compare with: show the file as it is now, nothing replayed.
            regs = [{"at": 0, "before": [], "removed": [], "added": [], "more": 0, "after": after[:40]}]
        s.emit("code_change", task_id, path=rel, lang=lang, change=change, regions=regs, lines=len(after), known=known,
               added=sum(len(r["added"]) + r["more"] for r in regs), removed=sum(len(r["removed"]) for r in regs))

    async def _baseline(self, s: "ProjectSession", rel: str) -> list[str] | None:
        if not getattr(s, "is_git", False):
            return None
        try:
            code, out, _ = await s.remote.run(f"cd {shlex.quote(s.path)} && git show HEAD:{shlex.quote(rel)} 2>/dev/null | head -c {MAX_BYTES}",
                                              timeout=10)
        except RemoteError:
            return None
        return out.splitlines() if code == 0 and out else []
