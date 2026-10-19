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
