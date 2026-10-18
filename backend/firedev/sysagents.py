"""Coding agents actually running on a machine: Codex and Claude Code processes, found with ps over SSH.

This is the real answer to "show my active coding agents": FireDev's own agent turns plus any other Codex or
Claude Code session on the machine (an interactive TUI, another tool's `codex exec`, an app server).
"""

from __future__ import annotations

import posixpath
import re

from .ssh import Remote

_SCRIPT = (
    "ps -eo pid=,ppid=,etimes=,pcpu=,rss=,args= --cols 600 2>/dev/null; echo '<<<CWD>>>'; "
    "ps -eo pid=,args= --cols 600 2>/dev/null | while read -r pid args; do case \"$args\" in "
    "*codex*|*claude*) printf '%s\\t%s\\n' \"$pid\" \"$(readlink /proc/$pid/cwd 2>/dev/null)\";; esac; done"
)
_AGENT = re.compile(r"(?:^|/)(codex|claude)(?:\.js)?$")


def _which(args: str) -> str | None:
    toks = args.split()
    if not toks:
        return None
    first = toks[0]
    if _AGENT.search(first):
        return _AGENT.search(first).group(1)
    if posixpath.basename(first) in ("node", "bun") and len(toks) > 1 and _AGENT.search(toks[1]):
        return _AGENT.search(toks[1]).group(1)
    return None


def _mode(kind: str, args: str) -> str:
    a = " " + args + " "
    if " app-server" in a or " exec-server" in a or " mcp-server" in a:
        return "server"
    if kind == "codex" and (" exec " in a or " e " in a):
        return "exec"
    if kind == "claude" and (" -p " in a or " --print " in a):
        return "print"
    return "interactive"


def parse(ps_out: str, projects: list[dict]) -> list[dict]:
    table, _, cwd_part = ps_out.partition("<<<CWD>>>")
    cwds = {}
    for line in cwd_part.strip().splitlines():
        pid, _, cwd = line.partition("\t")
        if pid.strip().isdigit():
            cwds[int(pid)] = cwd.strip()
    procs = {}
    for line in table.strip().splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[0].isdigit():
            continue
        pid, ppid, et, cpu, rss, args = parts
        procs[int(pid)] = {"pid": int(pid), "ppid": int(ppid) if ppid.isdigit() else 0, "etimes": int(et) if et.isdigit() else 0,
                           "cpu": float(cpu) if re.match(r"^\d+(\.\d+)?$", cpu) else 0.0, "rss": int(rss) if rss.isdigit() else 0,
                           "args": args}
    agents = {pid: p for pid, p in procs.items() if _which(p["args"]) and "<<<CWD>>>" not in p["args"] and "readlink /proc" not in p["args"]}
    out = []
    for pid, p in agents.items():
        kind = _which(p["args"])
        parent = agents.get(p["ppid"])
        if parent and _which(parent["args"]) == kind:
            continue  # the native binary under its npm launcher: one agent
        managed = None
        anc, seen = procs.get(p["ppid"]), 0
        while anc and seen < 12:
            m = re.search(r"\.firedev/projects/([a-z0-9-]+)/runs/", anc["args"])
            if m:
                managed = m.group(1)
                break
            anc, seen = procs.get(anc["ppid"]), seen + 1
        cwd = cwds.get(pid, "")
        proj = next((x for x in projects if x.get("path_abs") and (cwd == x["path_abs"] or cwd.startswith(x["path_abs"] + "/"))), None)
        out.append({
            "pid": pid, "agent": "Codex" if kind == "codex" else "Claude Code", "mode": _mode(kind, p["args"]),
            "cwd": cwd, "project": proj["name"] if proj else (posixpath.basename(cwd) or "?"),
            "firedev_project": managed, "uptime_s": p["etimes"], "cpu": round(p["cpu"], 1), "mem_mb": round(p["rss"] / 1024),
            "state": "working" if p["cpu"] >= 1.0 else "idle", "source": "machine",
        })
    out.sort(key=lambda a: (a["mode"] == "server", -a["cpu"], a["uptime_s"]))
    return out[:40]


async def scan(remote: Remote, projects: list[dict]) -> list[dict]:
    _, out, _ = await remote.run(_SCRIPT, timeout=15)
    return parse(out, projects)
