"""Turns raw tool output into the structured facts the TV shows: build errors, test counts, readable steps."""

from __future__ import annotations

import posixpath
import re
import shlex
import xml.etree.ElementTree as ET

from .risk import unwrap

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# --- builds ---------------------------------------------------------------------------------------------------

_KOTLIN_ERR = re.compile(r"^e: (?:file://)?(?P<file>[^\s:]+\.\w+):(?P<line>\d+):(?P<col>\d+):? (?P<msg>.+)$", re.M)
_JAVAC_ERR = re.compile(r"^(?P<file>[^\s:]+\.(?:java|kt)):(?P<line>\d+): error: (?P<msg>.+)$", re.M)
_TSC_ERR = re.compile(r"^(?P<file>[^\s(]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\): error \w+: (?P<msg>.+)$", re.M)
_GCC_ERR = re.compile(r"^(?P<file>[^\s:]+\.(?:c|cc|cpp|h|rs|go|py)):(?P<line>\d+):(?:(?P<col>\d+):)? (?:fatal )?error:? (?P<msg>.+)$", re.M)
_WENT_WRONG = re.compile(r"\* What went wrong:\n(?P<msg>(?:.+\n?){1,6})")
_GRADLE_TIME = re.compile(r"BUILD (?:SUCCESSFUL|FAILED) in (?:(\d+)m )?(?:(\d+)s|(\d+)ms)")


def _rel(path: str, root: str | None) -> str:
    if root and path.startswith(root.rstrip("/") + "/"):
        return path[len(root.rstrip("/")) + 1:]
    return path


def parse_build_output(text: str, exit_code: int | None, root: str | None = None) -> dict:
    text = strip_ansi(text)
    errors = []
    seen = set()
    for rx in (_KOTLIN_ERR, _JAVAC_ERR, _TSC_ERR, _GCC_ERR):
        for m in rx.finditer(text):
            d = m.groupdict()
            key = (d["file"], d["line"], d["msg"])
            if key in seen:
                continue
            seen.add(key)
            errors.append({
                "file": _rel(d["file"], root),
                "name": posixpath.basename(d["file"]),
                "line": int(d["line"]),
                "col": int(d["col"]) if d.get("col") else None,
                "message": d["msg"].strip(),
            })
    ok = exit_code == 0 if exit_code is not None else "BUILD SUCCESSFUL" in text
    compiler_errors = len(errors)
    if not ok and not errors:
        m = _WENT_WRONG.search(text)
        if m:
            msg = " ".join(line.strip("> ").strip() for line in m.group("msg").splitlines() if line.strip())
            errors.append({"file": None, "name": None, "line": None, "col": None, "message": msg[:300]})
    m = _GRADLE_TIME.search(text)
    duration = None
    if m:
        mins, secs, ms = m.groups()
        duration = (int(mins or 0) * 60 + int(secs or 0)) * 1000 + int(ms or 0)
    return {"ok": ok, "errors": errors[:20], "error_count": compiler_errors, "tool_duration_ms": duration}


# --- tests ----------------------------------------------------------------------------------------------------

def parse_junit_xml(documents: list[str]) -> dict | None:
    """Sums JUnit XML reports (Gradle, Maven, pytest --junitxml, jest-junit)."""
    total = failed = skipped = 0
    failures = []
    found = False
    for doc in documents:
        doc = doc.strip()
        if not doc:
            continue
        try:
            root = ET.fromstring(doc)
        except ET.ParseError:
            continue
        suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
        for suite in suites:
            found = True
            for case in suite.iter("testcase"):
                total += 1
                bad = case.find("failure")
                if bad is None:
                    bad = case.find("error")
                if bad is not None:
                    failed += 1
                    msg = (bad.get("message") or (bad.text or "").strip().splitlines()[0:1] or [""])
                    msg = msg if isinstance(msg, str) else (msg[0] if msg else "")
                    failures.append({"name": f"{case.get('classname', '').split('.')[-1]}.{case.get('name')}".strip("."), "message": msg[:240]})
                elif case.find("skipped") is not None:
                    skipped += 1
    if not found:
        return None
    return {"total": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped, "failures": failures[:12]}


_PYTEST = re.compile(r"=+ (?P<body>.*?(?:passed|failed|error).*?) in [\d.]+s")
_JEST = re.compile(r"Tests:\s+(?P<body>.*?\d+ total)")
_GRADLE_TESTS = re.compile(r"(?P<total>\d+) tests completed, (?P<failed>\d+) failed(?:, (?P<skipped>\d+) skipped)?")


def parse_test_summary(text: str) -> dict | None:
    """Fallback when there are no XML reports: reads the summary line of common test runners."""
    text = strip_ansi(text)
    m = _GRADLE_TESTS.search(text)
    if m:
        total, failed, skipped = int(m["total"]), int(m["failed"]), int(m["skipped"] or 0)
        return {"total": total, "failed": failed, "skipped": skipped, "passed": total - failed - skipped, "failures": []}
    for rx in (_PYTEST, _JEST):
        m = None
        for m in rx.finditer(text):
            pass
        if m:
            counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|errors?|skipped|total)", m["body"])}
            failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
            passed = counts.get("passed", 0)
            skipped = counts.get("skipped", 0)
            total = counts.get("total", passed + failed + skipped)
            return {"total": total, "failed": failed, "skipped": skipped, "passed": passed, "failures": []}
    return None


# --- readable steps -------------------------------------------------------------------------------------------

def _base(p: str) -> str:
    return posixpath.basename(p.rstrip("/")) or p


def _first_path(args: list[str]) -> str | None:
    for a in reversed(args):
        if not a.startswith("-") and ("/" in a or "." in a) and not re.match(r"^\d+(,\d+)?p?$", a):
            return a
    return None


def humanize_command(command: str) -> str:
    """'/bin/bash -lc "sed -n 1,80p app/build.gradle.kts"' → 'Reading build.gradle.kts'."""
    c = unwrap(command)
    first = re.split(r"\s*(?:&&|;|\|\|)\s*", c)[0]
    try:
        args = shlex.split(first)
    except ValueError:
        args = first.split()
    if not args:
        return "Running a command"
    tool = _base(args[0])
    rest = args[1:]
    if tool == "firedev" and rest:
        sub = rest[0]
        if sub == "run" and len(rest) > 1:
            return {"test": "Running the tests", "tests": "Running the tests", "build": "Building the app"}.get(rest[1], f"Running {rest[1].replace('_', ' ')}")
        if sub == "request":
            return "Asking for approval"
        return "Checking FireDev actions"
    if tool in {"cat", "sed", "head", "tail", "less", "nl", "bat"}:
        p = _first_path(rest)
        return f"Reading {_base(p)}" if p else "Reading a file"
    if tool in {"rg", "grep", "ag", "egrep"}:
        if "--files" in rest:
            return "Looking at the project files"
        terms = [a for a in rest if not a.startswith("-")]
        return f"Searching for “{terms[0][:32]}”" if terms else "Searching the code"
    if tool in {"ls", "tree", "find", "fd"}:
        return "Looking at the project files"
    if tool == "git":
        sub = rest[0] if rest else ""
        return {"status": "Checking git status", "diff": "Reviewing changes", "log": "Reading git history", "show": "Reading a commit"}.get(sub, f"Running git {sub}".strip())
    if tool in {"gradlew", "gradle"}:
        tasks = " ".join(rest).lower()
        if "test" in tasks:
            return "Running the tests"
        if "assemble" in tasks or "build" in tasks or "bundle" in tasks:
            return "Building the app"
        return "Running Gradle"
    if tool in {"npm", "yarn", "pnpm"}:
        if "test" in rest:
            return "Running the tests"
        if "build" in rest:
            return "Building the app"
        return f"Running {tool} {' '.join(rest[:2])}".strip()
    if tool in {"pytest", "jest", "vitest"} or (tool.startswith("python") and "pytest" in rest):
        return "Running the tests"
    if tool in {"python", "python3", "node"}:
        p = _first_path(rest)
        return f"Running {_base(p)}" if p else f"Running {tool}"
    if tool == "apply_patch":
        return "Editing files"
    return f"Running {tool}"


def file_kind_label(kind: str) -> str:
    return {"add": "Created", "delete": "Deleted", "update": "Edited"}.get(kind, "Edited")
