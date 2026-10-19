"""The TV talks the user through long agent work: what was done since the agent last spoke, and what it's on now."""

import asyncio
import random
import time
from types import SimpleNamespace

from firedev import session as session_mod
from firedev.speech import narration


def act(kind, title, detail=""):
    return {"kind": kind, "title": title, "detail": detail, "ts": time.time(), "task": "t1"}


def test_narration_says_what_was_made_and_whats_next():
    line = narration([act("command", "Reading README.md"), act("file", "Created index.html", "site/index.html"),
                      act("file", "Created script.js", "script.js"), act("file", "Edited styles.css", "styles.css")],
                     "Editing script.js…", playful=False)
    assert line == "I've created index.html and script.js. I've also updated styles.css. Now I'm editing script.js."


def test_narration_reading_only_and_results():
    assert narration([act("command", "Reading README.md"), act("command", "Reading local.md")], "", playful=False) == \
        "I've been reading README.md and local.md."
    assert narration([act("tests", "Tests passed: 12 of 12")], "", playful=False) == "Tests passed: 12 of 12."
    assert narration([], "") == ""
    assert narration([], "Waiting to start") == ""


def test_playful_narration_varies_but_keeps_the_facts_and_bad_news_plain():
    rng = random.Random(1)
    lines = {narration([act("file", "Created index.html", "index.html")], "Editing app.js…", rng=rng) for _ in range(30)}
    assert len(lines) > 3 and all("index.html" in ln and "editing app.js" in ln for ln in lines)
    failed = dict(act("tests", "Tests failed: 2 of 12"), status="failed")
    assert {narration([failed], "", rng=rng) for _ in range(20)} == {"Tests failed: 2 of 12."}


def test_narrator_speaks_after_a_quiet_spell_and_stops_with_the_run(monkeypatch):
    monkeypatch.setattr(session_mod, "NARRATE_CHECK", 0.02)
    monkeypatch.setattr(session_mod, "NARRATE_QUIET", 0.05)
    said, turns = [], []

    async def go():
        run = SimpleNamespace(done=asyncio.Event())
        task = SimpleNamespace(id="t1", status="running", status_text="Editing script.js…")
        s = SimpleNamespace(activity=[], _last_speech=(time.time(), ""))

        def speak(text, kind, tid):
            said.append(text)
            s._last_speech = (time.time(), text)
            return True
        s.speak = speak
        s.add_turn = lambda role, text, **kw: turns.append(text)
        narrator = asyncio.ensure_future(session_mod.ProjectSession._narrate(s, run, task))
        await asyncio.sleep(0.01)
        s.activity.append(act("file", "Created index.html", "index.html"))
        await asyncio.sleep(0.15)
        # Nothing new and the same step: no repeats.
        n = len(said)
        await asyncio.sleep(0.15)
        assert len(said) == n
        run.done.set()
        await asyncio.wait_for(narrator, 1)

    asyncio.run(go())
    assert "index.html" in said[0] and "editing script.js" in said[0]
    assert turns == said


def test_written_shorthand_is_read_as_words():
    from firedev.speech import clean_for_speech
    said = clean_for_speech("Moved the nav, e.g. the header & footer -> layout.tsx, w/ new tests.")
    assert said == "Moved the nav, for example, the header and footer to layout.tsx, with new tests."


def test_housekeeping_commands_are_not_narrated():
    assert narration([], "Running pwd", playful=False) == ""
    assert narration([], "Running git status --short", playful=False) == ""
    assert narration([], "Running ./test.sh", playful=False) == "Now I'm running test.sh."


def test_closing_reply_is_not_said_twice(tmp_path):
    """The agent spoke its summary with `firedev say`; the end-of-task reply repeating it stays quiet."""
    from test_preview import make_service
    svc, _ = make_service(tmp_path)
    s = svc.sessions["web"]
    q = svc.bus.subscribe()
    s.speak("All fourteen checks passed. Up top, the full-screen Arctic sky has slowly moving northern lights and a lit "
            "cabin; below it, the page ends with a stay enquiry.", "progress", "t1", force=True)
    again = ("All 14 checks passed, and the live preview is running. Let me walk you through it: the full-screen night sky "
             "opens with slowly moving northern lights, a glowing cabin, and the larger headline.")
    assert s._just_said(again, "t1")
    assert not s._just_said(again, "t2"), "another task's reply still speaks"
    assert not s._just_said("I fixed the build: a missing import in MainActivity.", "t1")
    assert q.qsize() == 1


def test_a_reply_right_after_the_agents_own_wrap_up_stays_quiet(tmp_path):
    import time
    from test_preview import make_service
    svc, _ = make_service(tmp_path)
    s = svc.sessions["web"]
    s._agent_spoke = (time.time(), "t1")
    assert s._just_said("Here's the tour, from sky to snowy ground.", "t1")
    assert not s._just_said("Here's the tour, from sky to snowy ground.", "t2")
    s._agent_spoke = (time.time() - 60, "t1")
    assert not s._just_said("Here's the tour, from sky to snowy ground.", "t1")


def test_search_patterns_are_not_read_aloud():
    assert narration([], "Searching for “^(:root|\\.hero|”", playful=False) == "Now I'm searching the code."
    assert narration([], "Searching for “hero”", playful=False) == "Now I'm searching for “hero”."


def test_reasoning_headlines_about_the_work_only():
    from firedev.session import _thought_headline as head
    assert head("**Designing animated aurora overlay**\n\nI'll add…") == "Designing animated aurora overlay"
    assert head("**Considering progress update**") == ""
    assert head("**Confirming speech step completion**") == ""
    assert head("no headline here") == ""


def test_task_timings_start_from_when_the_request_was_heard():
    import time
    from firedev.session import HEARD, Task
    tok = HEARD.set({"t": time.time() - 0.5, "rec_ms": 420})
    try:
        t = Task(id="t1", title="Build", kind="agent")
    finally:
        HEARD.reset(tok)
    t.mark("dispatched")
    t.mark("dispatched", time.time() + 99)  # only the first time counts
    tm = t.timings()
    assert tm["recognition"] == 420 and tm["heard"] == 0 and 400 <= tm["dispatched"] <= 2000
    assert list(tm)[:2] == ["recognition", "heard"]
    assert Task(id="t2", title="x", kind="agent").timings()["queued"] == 0  # not spoken: from queuing


async def test_an_agent_request_is_acknowledged_at_once(tmp_path):
    from test_preview import make_service
    from firedev.service import Client
    svc, _ = make_service(tmp_path)
    q = svc.bus.subscribe()
    s = svc.sessions["web"]

    async def ensure_open(sess):
        return None
    svc._ensure_open = ensure_open
    s.adapter.id = "codex"
    s.submit = lambda task: task
    await svc.execute("voice_command", {"text": "Make the header smaller", "project": "web", "rec_ms": 300}, Client("tv", "TV", "tv"))
    said = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "agent_speech":
            said.append(ev.data["text"])
    assert said and said[0].startswith("On it.")


def test_words_before_the_last_step_are_not_a_wrap_up(tmp_path):
    """Codex says what it's about to do, then runs the checks: its closing answer (the tour) must still be spoken."""
    import time
    from test_preview import make_service
    svc, _ = make_service(tmp_path)
    s = svc.sessions["web"]
    s._agent_spoke = (time.time() - 5, "t1")
    s.add_activity("tests", "Tests: 14 / 14 passed", task="t1")
    assert not s._just_said("All 14 checks passed. Here's the tour: the night sky up top, the cabins below.", "t1")
