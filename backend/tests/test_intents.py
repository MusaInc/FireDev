import pytest

from firedev.intents import parse

PROJECTS = [{"id": "pulse", "name": "My Android App", "aliases": ["android", "pulse"]},
            {"id": "site", "name": "Marketing Site", "aliases": []}]
ACTIONS = {"test": {"kind": "tests", "title": "Run the tests"}, "build": {"kind": "build", "title": "Build the Android app"},
           "deploy_preview": {"kind": "deploy", "title": "Deploy the preview"}, "logs": {"kind": "logs", "title": "Check the logs"}}
CTX = {"project": "pulse", "agent": "Codex", "actions": ACTIONS}


@pytest.mark.parametrize("text,name,arg", [
    ("Open my Android project.", "open_project", ("project", "pulse")),
    ("Alexa, open marketing site", "open_project", ("project", "site")),
    ("Run the tests", "run_action", ("action", "test")),
    ("Build the Android app.", "run_action", ("action", "build")),
    ("Build the app", "run_action", ("action", "build")),
    ("Deploy the preview.", "run_action", ("action", "deploy_preview")),
    ("Check the logs", "run_action", ("action", "logs")),
    ("Show me what changed", "show_changes", None),
    ("Stop.", "cancel_task", None),
    ("Continue", "continue_task", None),
    ("Undo that change", "undo_changes", None),
    ("What are you doing?", "get_status", None),
    ("Go home", "go_home", None),
])
def test_commands(text, name, arg):
    i = parse(text, PROJECTS, CTX)
    assert i.name == name, i
    if arg:
        assert i.args[arg[0]] == arg[1]


def test_why_build_fails_is_a_read_only_agent_task():
    i = parse("Why is the build failing?", PROJECTS, CTX)
    assert i.name == "send_agent_task" and i.args["mode"] == "investigate"
    assert "Don't change any files" in i.args["task"] and "firedev run build" in i.args["task"]


def test_fix_it_uses_context_and_runs_tests():
    i = parse("Fix it and run the tests.", PROJECTS, {**CTX, "last_build_failed": True})
    assert i.name == "send_agent_task" and i.title == "Fix the failing build and run tests"
    assert "firedev run test" in i.args["task"] and "failing build" in i.args["task"]


def test_ask_codex_prefix_and_free_text():
    i = parse("Ask Codex to add a dark mode toggle", PROJECTS, CTX)
    assert i.name == "send_agent_task" and i.title == "Add a dark mode toggle"


def test_approve_only_when_something_is_pending():
    assert parse("Approve", PROJECTS, {**CTX, "pending_approval": True}).name == "approve_action"
    assert parse("Approve", PROJECTS, CTX).name == "send_agent_task"


def test_voice_never_becomes_a_shell_command():
    i = parse("rm -rf /", PROJECTS, CTX)
    assert i.name == "send_agent_task"  # goes to the sandboxed agent, never straight to a shell


def test_outside_a_project_single_project_opens_it():
    i = parse("Why is the build failing?", PROJECTS[:1], {"project": None})
    assert i.name == "open_project" and i.args["then"] == "Why is the build failing?"
