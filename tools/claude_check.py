"""One real Claude Code turn through FireDev against a throwaway fixture: checks the adapter end to end
(stream-json parsing, the firedev helper on its allow-list, structured test results)."""
import asyncio, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from firedev import config as cfg
from firedev.auth import Client, DeviceStore
from firedev.service import FireDev

async def main():
    tmp = Path(tempfile.mkdtemp(prefix="firedev-claude-"))
    p = tmp / "proj"; p.mkdir()
    (p / "test.sh").write_text('#!/usr/bin/env bash\nmkdir -p reports\nprintf \'<testsuite><testcase classname="A" name="a"/><testcase classname="A" name="b"/></testsuite>\' > reports/T.xml\necho "2 tests ok"\n')
    (p / "test.sh").chmod(0o755)
    subprocess.run(["git", "init", "-q", str(p)]); subprocess.run(["git", "-C", str(p), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i", "--allow-empty"])
    raw = {"server": {"data_dir": str(tmp / "data")},
           "machines": {"l": {"host": "localhost", "user": os.environ["USER"], "auth": "key", "key_file": "~/.config/firedev/keys/firedev_ed25519"}},
           "projects": [{"id": "claude-check", "name": "Claude check", "machine": "l", "path": str(p), "agent": "claude", "agent_options": {"binary": os.environ.get("CLAUDE_BIN", "claude")},
                         "actions": {"test": {"run": "./test.sh", "kind": "tests", "reports": "reports/*.xml"}}}]}
    fd = FireDev(cfg.parse(raw), DeviceStore(tmp / "home"))
    q = fd.bus.subscribe(10000)
    tv = Client("tv", "TV", "tv")
    print(await fd.execute("open_project", {"project": "claude-check"}, tv))
    await fd.execute("send_agent_task", {"project": "claude-check", "task": "Run the tests with `firedev run test` and tell me how many passed. Don't change files."}, tv)
    seen = []
    while True:
        e = await asyncio.wait_for(q.get(), 300)
        if e.type in ("agent_message", "tests_finished", "command_started", "task_completed", "task_failed"):
            print(e.type, str(e.data.get("text") or e.data.get("command") or e.data.get("total") or e.data.get("error") or "")[:140])
        seen.append(e.type)
        if e.type in ("task_completed", "task_failed"):
            break
    await fd.sessions["claude-check"].close()
    subprocess.run(["tmux", "kill-session", "-t", "=firedev-claude-check"])
    ok = "tests_finished" in seen and seen[-1] == "task_completed"
    print("CLAUDE ADAPTER", "PASSED" if ok else "FAILED")

asyncio.run(main())
