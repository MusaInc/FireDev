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
    nothing = parse("Approve", PROJECTS, CTX)
    assert nothing.name == "help" and nothing.title == "Nothing is waiting for your approval"


def test_voice_never_becomes_a_shell_command():
    i = parse("rm -rf /", PROJECTS, CTX)
    assert i.name == "send_agent_task"  # goes to the sandboxed agent, never straight to a shell


def test_outside_a_project_single_project_opens_it():
    i = parse("Why is the build failing?", PROJECTS[:1], {"project": None})
    assert i.name == "open_project" and i.args["then"] == "Why is the build failing?"


WEB = [{"id": "pulse", "name": "My Android App", "aliases": ["android", "pulse", "android project"]},
       {"id": "web", "name": "Web Lab", "aliases": ["web", "web lab", "website", "landing page", "site"]}]


@pytest.mark.parametrize("text,ctx,target", [
    ("Build me a landing page", "pulse", "web"),
    ("build me a website about camping", "pulse", "web"),
    ("Add a settings screen to the Android app", "web", "pulse"),
    ("Build me a landing page", None, "web"),
])
def test_requests_go_to_the_project_they_are_about(text, ctx, target):
    i = parse(text, WEB, {"project": ctx, "actions": {}, "default": "pulse"})
    assert i.name == "open_project" and i.args["project"] == target and i.args["then"] == text


def test_requests_stay_put_when_they_mention_the_current_or_several_projects():
    assert parse("Make the website header smaller", WEB, {"project": "web", "actions": {}}).name == "send_agent_task"
    assert parse("Add a web view to the android app", WEB, {"project": "pulse", "actions": {}}).name == "send_agent_task"


DEMO = WEB + [{"id": "aurora", "name": "Aurora Lodge", "aliases": ["aurora", "aurora lodge", "website", "the website", "site"]}]


@pytest.mark.parametrize("text,ctx,target", [
    ("Build a beautiful modern website for Aurora Lodge, a glass cabin retreat", None, "aurora"),
    ("build a website for aurora", "pulse", "aurora"),
    ("Build me a landing page", "aurora", "web"),
])
def test_the_project_named_wins_over_a_generic_word(text, ctx, target):
    """Two projects answer to "website": the one named (or matched by more of what was said) gets the request."""
    i = parse(text, DEMO, {"project": ctx, "actions": {}, "default": "pulse"})
    assert i.name == "open_project" and i.args["project"] == target


def test_a_generic_word_stays_with_the_open_project_that_answers_to_it():
    assert parse("Make the website feel more dramatic", DEMO, {"project": "aurora", "actions": {}}).name == "send_agent_task"
    # Said from home with nothing to tell them apart: no guess between the two.
    assert parse("build me a website", DEMO, {"project": None, "default": None}).name == "help"


def test_outside_a_project_falls_back_to_the_default_project():
    i = parse("Add a dark mode toggle", WEB, {"project": None, "default": "pulse"})
    assert i.name == "open_project" and i.args == {"project": "pulse", "then": "Add a dark mode toggle"}


@pytest.mark.parametrize("text,name", [
    ("What are my agents doing?", "agents_dashboard"),
    ("Show me my agents", "agents_dashboard"),
    ("What's everyone working on", "agents_dashboard"),
    ("Show me my projects", "show_projects"),
    ("Switch projects", "show_projects"),
    ("Go home", "go_home"),
    ("Back to home", "go_home"),
])
def test_navigation_by_meaning(text, name):
    assert parse(text, WEB, {"project": "web", "actions": {}}).name == name


@pytest.mark.parametrize("text", ["Explain how this works", "How does the pairing flow work?", "Explain the architecture",
                                  "Walk me through how the checklist works"])
def test_explaining_puts_a_diagram_on_screen(text):
    i = parse(text, WEB, {"project": "web", "actions": {}})
    assert i.name == "send_agent_task" and i.args["lane"] == "ui" and "diagram" in i.args["task"]


PROD = WEB + [{"id": "aurora", "name": "Aurora Lodge", "aliases": ["aurora", "aurora lodge", "demo site"]}]


@pytest.mark.parametrize("text,ctx,target", [
    ("Make it more dramatic. Switch the whole site to a dark midnight theme", "aurora", None),
    ("Make the website load faster", "aurora", None),
    ("Build me a landing page", "aurora", "web"),
    ("Build a new website for the bakery", "aurora", "web"),
    ("Open web lab", "aurora", "web"),
])
def test_inside_a_project_generic_words_mean_this_project(text, ctx, target):
    """Web Lab answers to "site" and "website", but inside Aurora Lodge "the whole site" is Aurora Lodge's."""
    i = parse(text, PROD, {"project": ctx, "actions": {}, "default": "pulse"})
    got = i.args.get("project") if i.name == "open_project" else None
    assert got == target, (i.name, i.args)
