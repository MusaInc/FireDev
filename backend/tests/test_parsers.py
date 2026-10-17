from pathlib import Path

from firedev.agents.codex import CodexParser
from firedev.agents.claude import ClaudeParser
from firedev.parsers import humanize_command, parse_build_output, parse_junit_xml, parse_test_summary

GRADLE_FAIL = """> Task :app:compileDebugKotlin FAILED
e: file:///home/u/pulse/app/src/main/java/dev/firedev/pulse/MainActivity.kt:24:29 Unresolved reference 'BuildConfig'.
e: file:///home/u/pulse/app/src/main/java/dev/firedev/pulse/domain/WeeklySummary.kt:8:35 Unresolved reference 'format'.

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.

BUILD FAILED in 25s
"""


def test_gradle_kotlin_errors():
    b = parse_build_output(GRADLE_FAIL, 1, "/home/u/pulse")
    assert not b["ok"] and b["error_count"] == 2
    assert b["errors"][0]["file"] == "app/src/main/java/dev/firedev/pulse/MainActivity.kt"
    assert b["errors"][0]["line"] == 24 and b["errors"][1]["name"] == "WeeklySummary.kt"
    assert b["tool_duration_ms"] == 25000


def test_gradle_failure_without_compiler_errors_uses_what_went_wrong():
    b = parse_build_output("* What went wrong:\nCould not determine a usable wildcard IP for this machine.\n\nBUILD FAILED in 1s", 1)
    assert b["error_count"] == 0 and "wildcard IP" in b["errors"][0]["message"]


def test_junit_xml():
    doc = """<testsuite name="x" tests="3"><testcase classname="a.B" name="one"/>
    <testcase classname="a.B" name="two"><failure message="expected 2">trace</failure></testcase>
    <testcase classname="a.B" name="three"><skipped/></testcase></testsuite>"""
    r = parse_junit_xml([doc, doc])
    assert r == {"total": 6, "failed": 2, "skipped": 2, "passed": 2, "failures": r["failures"]}
    assert r["failures"][0] == {"name": "B.two", "message": "expected 2"}


def test_summary_lines():
    assert parse_test_summary("==== 2 failed, 16 passed in 1.20s ====")["total"] == 18
    assert parse_test_summary("Tests:       1 failed, 17 passed, 18 total")["failed"] == 1
    assert parse_test_summary("18 tests completed, 2 failed")["passed"] == 16


def test_humanize():
    assert humanize_command('/bin/bash -lc "sed -n 1,80p app/build.gradle.kts"') == "Reading build.gradle.kts"
    assert humanize_command("rg -n BuildConfig app") == "Searching for “BuildConfig”"
    assert humanize_command("firedev run test") == "Running the tests"
    assert humanize_command("firedev run build") == "Building the app"
    assert humanize_command("./gradlew assembleDebug") == "Building the app"
    assert humanize_command("git status --short") == "Checking git status"


def test_codex_stream():
    p = CodexParser()
    events = [e for line in (Path(__file__).parent / "fixtures" / "codex_fix.jsonl").read_text().splitlines() for e in p.feed(line)]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "thread" and kinds[-1] == "turn_completed"
    assert "file_changed" in kinds and kinds.count("command_started") == kinds.count("command_finished")
    sudo = [e for e in events if e["kind"] == "command_finished" and "sudo" in e["command"]]
    assert sudo and sudo[0]["exit_code"] != 0


def test_claude_stream():
    p = ClaudeParser()
    lines = [
        '{"type":"system","subtype":"init","session_id":"s1"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Looking."},{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"git status"}}]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"clean"}]}}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"/p/a.kt"}}]}}',
        '{"type":"result","subtype":"success","result":"done","session_id":"s1"}',
    ]
    kinds = [e["kind"] for line in lines for e in p.feed(line)]
    assert kinds == ["thread", "message", "command_started", "command_finished", "file_changed", "turn_completed"]
