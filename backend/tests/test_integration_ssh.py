"""End-to-end over real SSH to localhost: tmux, runs, the agent queue helper, approvals, reconnect, reattach,
cancel, diff and undo. The agent is a scripted stand-in that speaks Codex's JSONL (fixtures/fake-codex).

Run with FIREDEV_TEST_SSH=1 (needs the FireDev key in ~/.ssh/authorized_keys for localhost)."""

import asyncio
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from firedev import config as cfg
from firedev.auth import Client, DeviceStore
from firedev.service import CommandError, FireDev

pytestmark = [pytest.mark.ssh, pytest.mark.skipif(os.environ.get("FIREDEV_TEST_SSH") != "1", reason="FIREDEV_TEST_SSH=1 to run")]

FAKE = str(Path(__file__).parent / "fixtures" / "fake-codex")
TV = Client("tv1", "Living room TV", "tv")
PHONE = Client("ph1", "Phone", "voice")
ADMIN = Client("admin", "CLI", "admin")


def make_fixture(root: Path) -> Path:
    p = root / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "app.txt").write_text("broken\n")
    (p / "test.sh").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        mkdir -p reports
        if grep -q fixed src/app.txt; then f=""; code=0; else f='<failure message="app is broken"/>'; code=1; fi
        printf '<testsuite name="t"><testcase classname="app.AppTest" name="a"/><testcase classname="app.AppTest" name="b"/><testcase classname="app.AppTest" name="c">%s</testcase></testsuite>' "$f" > reports/TEST-app.xml
        echo "ran 3 tests"; exit $code
        """))
    (p / "build.sh").write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        if grep -q fixed src/app.txt; then echo "BUILD SUCCESSFUL in 2s"; mkdir -p out; echo apk > out/app.apk; exit 0; fi
        echo "e: file://$PWD/src/Main.kt:3:5 Unresolved reference 'x'."; echo "BUILD FAILED in 1s"; exit 1
        """))
    (p / "deploy.sh").write_text("#!/usr/bin/env bash\necho Uploading\necho 'Health check passed'\necho 'Preview live at http://127.0.0.1:9/'\n")
    (p / "slow.sh").write_text("#!/usr/bin/env bash\nfor i in $(seq 1 40); do echo line $i; sleep 0.15; done\n")
    (p / ".gitignore").write_text("reports/\nout/\n")
    for f in ("test.sh", "build.sh", "deploy.sh", "slow.sh"):
        (p / f).chmod(0o755)
    g = ["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "init"], check=True)
    return p


@pytest.fixture
async def fd(tmp_path):
    proj = make_fixture(tmp_path)
    sess = f"firedev-test-{os.getpid()}-{tmp_path.name[-6:]}"
    raw = {
        "server": {"data_dir": str(tmp_path / "data")},
        "machines": {"local": {"host": "localhost", "user": os.environ.get("USER", "abdul"), "auth": "key",
                               "key_file": "~/.config/firedev/keys/firedev_ed25519"}},
        "projects": [{"id": "fx", "name": "Fixture App", "machine": "local", "path": str(proj), "agent": "codex",
                      "tmux_session": sess, "agent_options": {"binary": FAKE},
                      "actions": {
                          "test": {"run": "./test.sh", "kind": "tests", "reports": "reports/*.xml", "title": "Run the tests"},
                          "build": {"run": "./build.sh", "kind": "build", "artifact": "out/app.apk", "title": "Build the app"},
                          "deploy_preview": {"run": "./deploy.sh", "kind": "deploy", "level": 2, "title": "Deploy the preview",
                                             "reason": "Ship it", "risk": "Testers get the new build"},
                          "slow": {"run": "./slow.sh", "kind": "command", "title": "Slow"},
                      }}],
    }
    service = FireDev(cfg.parse(raw), DeviceStore(tmp_path / "home"))
    q = service.bus.subscribe(10000)
    service._q = q
    service._proj = proj
    yield service
    for s in service.sessions.values():
        await s.close()
    subprocess.run(["tmux", "kill-session", "-t", f"={sess}"], capture_output=True)


async def next_event(service, type_, pred=lambda e: True, timeout=30):
    async def find():
        while True:
            e = await service._q.get()
            if e.type == type_ and pred(e):
                return e
    return await asyncio.wait_for(find(), timeout)


async def collect_until(service, type_, timeout=30):
    seen = []

    async def run():
        while True:
            e = await service._q.get()
            seen.append(e)
            if e.type == type_:
                return seen
    return await asyncio.wait_for(run(), timeout)


async def test_open_runs_tests_and_build(fd):
    res = await fd.execute("open_project", {"project": "fx"}, TV)
    assert res["ok"], res
    s = fd.sessions["fx"]
    assert s.conn_state == "connected" and s.agent_state["state"] == "ready" and "fake" in s.agent_state["version"]
    assert subprocess.run(["tmux", "has-session", "-t", f"={s.project.tmux_session}"]).returncode == 0

    await fd.execute("run_action", {"project": "fx", "action": "test"}, TV)
    e = await next_event(fd, "tests_finished")
    assert e.data["total"] == 3 and e.data["failed"] == 1 and e.data["failures"][0]["name"] == "AppTest.c"

    await fd.execute("run_action", {"project": "fx", "action": "build"}, TV)
    e = await next_event(fd, "build_finished")
    assert e.data["state"] == "failed" and e.data["error_count"] == 1 and e.data["errors"][0]["file"] == "src/Main.kt"
    await next_event(fd, "task_failed")
    assert any(a["kind"] == "build" for a in s.activity)


async def test_level2_action_needs_tv_approval(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    await fd.execute("run_action", {"project": "fx", "action": "deploy_preview"}, PHONE.__class__("x", "Alexa", "assistant"))
    e = await next_event(fd, "approval_required")
    ap = e.data["approval"]
    assert ap["level"] == 2 and ap["command"] == "./deploy.sh" and ap["reason"] == "Ship it"
    # The phone, the CLI and assistants can't approve; only the TV can.
    for who in (PHONE, ADMIN, Client("a", "Alexa", "assistant")):
        with pytest.raises(CommandError):
            await fd.execute("approve_action", {"approval": ap["id"]}, who)
    assert not any(r.kind == "action" for r in fd.sessions["fx"].runs.values())
    await fd.execute("approve_action", {"approval": ap["id"]}, TV)
    e = await next_event(fd, "deploy_finished")
    assert e.data["state"] == "passed" and e.data["url"] == "http://127.0.0.1:9/" and e.data["checks"]


async def test_rejected_action_never_runs(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    await fd.execute("run_action", {"project": "fx", "action": "deploy_preview"}, TV)
    e = await next_event(fd, "approval_required")
    await fd.execute("reject_action", {"approval": e.data["approval"]["id"]}, PHONE)
    await next_event(fd, "task_cancelled")
    assert not fd.sessions["fx"].runs


async def test_agent_turn_with_firedev_run_and_file_change(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    await fd.execute("send_agent_task", {"project": "fx", "task": "Fix the app"}, TV)
    seen = await collect_until(fd, "task_completed", timeout=40)
    e = seen[-1]
    tests = [x.data for x in seen if x.type == "tests_finished"]
    # The agent's two `firedev run test` calls: failing before the fix, passing after.
    assert [(t["failed"], t["passed"]) for t in tests] == [(1, 2), (0, 3)]
    assert [x.data["path"] for x in seen if x.type == "file_changed"] == ["src/app.txt"]
    s = fd.sessions["fx"]
    assert e.data["task"]["summary"] == "Fixed the app file. Tests pass."
    assert s.agent_state["thread_id"] == "fake-thread-1"
    # The agent saw the real test output through the helper.
    test_runs = [r for r in s.runs.values() if r.action and r.action.kind == "tests"]
    assert len(test_runs) == 2 and all(r.source == "agent" and "ran 3 tests" in r.output() for r in test_runs)
    # Changes and diff
    assert [f["path"] for f in s.changes["files"]] == ["src/app.txt"]
    diff = await s.file_diff("src/app.txt")
    lines = [ln for h in diff["hunks"] for ln in h["lines"]]
    assert {"t": "-", "text": "broken", "old": 1} in lines and any(ln["t"] == "+" and ln["text"] == "fixed" for ln in lines)
    # Undo puts the file back.
    res = await fd.execute("undo_changes", {"project": "fx"}, TV)
    assert res["ok"], res
    assert (fd._proj / "src" / "app.txt").read_text() == "broken\n"
    assert s.changes["files"] == []


async def test_agent_request_is_gated_and_rejection_reaches_agent(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    await fd.execute("send_agent_task", {"project": "fx", "task": "PUSH it"}, TV)
    e = await next_event(fd, "approval_required")
    ap = e.data["approval"]
    assert ap["source"] == "agent" and ap["level"] == 2 and ap["command"] == "git push origin main" and ap["reason"] == "Publish the fix"
    assert fd.sessions["fx"].current.status == "waiting_approval"
    await fd.execute("reject_action", {"approval": ap["id"]}, TV)
    e = await next_event(fd, "agent_message", lambda e: e.data["text"].startswith("Push exit"))
    assert e.data["text"].startswith("Push exit 3: Rejected on the TV")
    await next_event(fd, "task_completed")


async def test_reconnect_resumes_output_without_loss(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    s = fd.sessions["fx"]
    await fd.execute("run_action", {"project": "fx", "action": "slow"}, TV)
    await next_event(fd, "command_output", lambda e: "line 5" in e.data["text"])
    s.remote.drop()
    await next_event(fd, "session_disconnected")
    e = await next_event(fd, "session_connected", timeout=20)
    assert e.data["reattached"]
    await next_event(fd, "command_finished", lambda e: e.data.get("title") == "Slow", timeout=30)
    run = next(r for r in s.runs.values() if r.title == "Slow")
    out = run.output()
    assert all(f"line {i}\n" in out for i in range(1, 41)) and out.count("line 7\n") == 1


async def test_restarted_service_reattaches_to_running_agent(fd, tmp_path):
    await fd.execute("open_project", {"project": "fx"}, TV)
    await fd.execute("send_agent_task", {"project": "fx", "task": "SLOW work"}, TV)
    await next_event(fd, "agent_message", lambda e: e.data["text"] == "Working slowly.")
    old = fd.sessions["fx"]
    old._closed = True
    for t in list(old._bg):
        t.cancel()
    await old.remote.close()
    # A fresh FireDev process (same config) picks the running turn back up from tmux.
    fresh = FireDev(fd.config, DeviceStore(tmp_path / "home2"))
    q = fresh.bus.subscribe(10000)
    fresh._q = q
    try:
        await fresh.execute("open_project", {"project": "fx"}, TV)
        s = fresh.sessions["fx"]
        assert s.current is not None and s.current.title == "SLOW work"
        assert any(a["title"].startswith("Reattached") for a in s.activity)
        # A new task waits for the reattached turn instead of starting a second agent on the same thread.
        await fresh.execute("send_agent_task", {"project": "fx", "task": "Fix the app"}, TV)
        await asyncio.sleep(1.5)
        assert sum(1 for r in s.runs.values() if r.kind == "agent" and not r.done.is_set()) == 1
        await fresh.execute("cancel_task", {"project": "fx"}, TV)
        await next_event(fresh, "task_cancelled", timeout=20)
    finally:
        await fresh.sessions["fx"].close()


async def test_cancel_stops_the_agent(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    s = fd.sessions["fx"]
    await fd.execute("send_agent_task", {"project": "fx", "task": "SLOW"}, TV)
    await next_event(fd, "agent_message", lambda e: e.data["text"] == "Working slowly.")
    run = next(r for r in s.runs.values() if r.kind == "agent")
    await fd.execute("cancel_task", {"project": "fx"}, PHONE)
    await next_event(fd, "task_cancelled", timeout=15)
    assert run.exit_code == 137 or run.exit_code is not None
    windows = subprocess.run(["tmux", "list-windows", "-t", f"={s.project.tmux_session}", "-F", "#{window_name}"], capture_output=True, text=True).stdout
    assert run.id not in windows


async def test_voice_flow(fd):
    r = await fd.voice("Open the fixture app", TV)
    assert r["intent"]["name"] == "open_project" and r["ok"]
    r = await fd.voice("Run the tests", PHONE)
    assert r["intent"]["args"]["action"] == "test"
    await next_event(fd, "tests_finished")
    r = await fd.voice("Deploy the preview", PHONE)
    assert "approval" in r["message"]
    await next_event(fd, "approval_required")
    r = await fd.voice("Approve", PHONE)
    assert r["navigate"] == "approval" and fd.approvals  # voice never approves by itself


def _helpers_for(path: str) -> list[str]:
    out = subprocess.run(["pgrep", "-fa", "^bash .*/.firedev/bin/firedev-(watch|follow)"], capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if path in ln]


async def test_no_remote_helpers_survive_a_dropped_connection(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    s = fd.sessions["fx"]
    queue = s.queue_dir
    for _ in range(20):
        if _helpers_for(queue):
            break
        await asyncio.sleep(0.2)
    assert _helpers_for(queue), "watcher should be running"
    s.remote.drop()
    await next_event(fd, "session_connected", timeout=20)
    await asyncio.sleep(1.5)
    assert len(_helpers_for(queue)) == 1, _helpers_for(queue)  # the new connection's watcher only
    await s.close()
    await asyncio.sleep(1.5)
    assert _helpers_for(queue) == []


async def test_request_claimed_by_a_dead_watcher_is_picked_up_again(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    s = fd.sessions["fx"]
    await s.close()
    # The agent's request was claimed (renamed to .taken) but never answered, e.g. FireDev went down mid-claim.
    q = Path(s.queue_dir)
    (q / "r123abc.taken").write_text('{"id":"r123abc","type":"action","action":"test","command":"","reason":"","cwd":"."}')
    await fd.execute("open_project", {"project": "fx"}, TV)
    e = await next_event(fd, "tests_finished", timeout=20)
    assert e.data["total"] == 3
    assert (q / "r123abc.state").read_text().startswith("running ")


async def test_runs_recreate_a_killed_tmux_session(fd):
    await fd.execute("open_project", {"project": "fx"}, TV)
    s = fd.sessions["fx"]
    subprocess.run(["tmux", "kill-session", "-t", f"={s.project.tmux_session}"], check=True)
    await fd.execute("run_action", {"project": "fx", "action": "test"}, TV)
    e = await next_event(fd, "tests_finished", timeout=20)
    assert e.data["total"] == 3
