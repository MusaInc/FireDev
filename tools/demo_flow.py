"""Runs the hackathon demo journey against the real config, real SSH and the real coding agent, without a TV:
open → "why is the build failing?" → "fix it and run the tests" → "build the app" → "deploy the preview" →
approve (as the TV) → done. Prints the structured events as they arrive and exits non-zero if a step fails.

  .venv/bin/python tools/demo_flow.py [--project pulse] [--via-ws URL --token TOKEN]
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from firedev import config as cfg  # noqa: E402
from firedev.auth import Client, DeviceStore  # noqa: E402
from firedev.service import FireDev  # noqa: E402

TV = Client("demo-tv", "Demo TV", "tv")
QUIET = {"state", "command_output", "voice_partial"}


async def main(project: str) -> int:
    c = cfg.load()
    fd = FireDev(c, DeviceStore(cfg.DEFAULT_HOME))
    q = fd.bus.subscribe(100000)
    t0 = time.time()

    async def until(type_, pred=lambda e: True, timeout=900):
        async def go():
            while True:
                e = await q.get()
                if e.type not in QUIET:
                    d = e.data
                    brief = d.get("text") or d.get("title") or d.get("command") or d.get("path") or d.get("state") or ""
                    if e.type == "tests_finished":
                        brief = f"{d.get('passed')}/{d.get('total')} passed"
                    if e.type == "build_finished":
                        brief = f"{d.get('state')} errors={d.get('error_count')}"
                    if e.type == "approval_required":
                        brief = f"L{d['approval']['level']} {d['approval']['command']}"
                    if e.type in ("task_completed", "task_failed"):
                        brief = (d["task"].get("summary") or d.get("error") or "")[:300]
                    print(f"{time.time() - t0:7.1f}s  {e.type:<18} {str(brief).splitlines()[0][:150] if brief else ''}", flush=True)
                if e.type == type_ and pred(e):
                    return e
                if e.type == "task_failed" and type_ == "task_completed":
                    raise RuntimeError(f"task failed: {e.data.get('error')}")
        return await asyncio.wait_for(go(), timeout)

    async def say(text):
        print(f"\n🎙  “{text}”", flush=True)
        r = await fd.voice(text, TV)
        print(f"   → {r['intent']['title']}: {r.get('message')}", flush=True)
        return r

    ok = True
    try:
        await say("Open my Android project")
        await say("Why is the build failing?")
        e = await until("task_completed", timeout=900)
        s = fd.sessions[project]
        print(f"   build state: {s.build.get('state')} errors={s.build.get('error_count')}")
        ok &= s.build.get("state") == "failed" and s.build.get("error_count") == 2
        ok &= not s.changes["files"]

        await say("Fix it and run the tests")
        e = await until("task_completed", timeout=1200)
        print(f"   tests: {s.tests}")
        print(f"   changed: {[ (f['path'], f['added'], f['removed']) for f in s.changes['files']]}")
        ok &= s.tests.get("state") == "passed" and s.tests.get("total") == 18

        await say("Build the app")
        e = await until("task_completed", timeout=900)
        print(f"   build: {s.build.get('state')} artifact={s.build.get('artifact')}")
        ok &= s.build.get("state") == "passed"

        await say("Deploy the preview")
        e = await until("approval_required")
        await fd.execute("approve_action", {"approval": e.data["approval"]["id"]}, TV)
        e = await until("deploy_finished")
        print(f"   deploy: {e.data.get('state')} url={e.data.get('url')} checks={e.data.get('checks')}")
        ok &= e.data.get("state") == "passed"
    finally:
        for sess in fd.sessions.values():
            await sess.close()
    print("\nDEMO", "PASSED" if ok else "FAILED", f"in {time.time() - t0:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="pulse")
    sys.exit(asyncio.run(main(ap.parse_args().project)))
