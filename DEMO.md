# Demo

One journey, about 70 seconds of real agent time, understandable in a 60–90 second video. Every step below
was run end to end against the real Codex CLI (`tools/demo_flow.py` headless, `tools/capture-demo.mjs`
through the TV interface). The screenshots in `docs/screens/` come from those runs.

## Before you record

```bash
./firedev demo reset          # broken Pulse app again, fresh agent thread, preview server stopped
./firedev serve               # if it isn't running
```

- TV (or a browser at 1920×1080 on `http://<service>:8130/tv/`) on the Home screen.
- Phone: open the phone-microphone link from the TV (*Home → Phone microphone*), or keep a terminal ready
  with `./firedev say "…"`.
- Optional, for the split-screen shot: `tmux attach -t firedev-pulse` on the dev machine shows the same runs live.

The sample is `~/firedev-demo/pulse`, an Android focus tracker with two compile errors (a missing
`buildFeatures.buildConfig` and a call to a renamed function) and 18 unit tests.

## The journey

| # | Say / press | What the TV shows | Screenshot |
|---|---|---|---|
| 1 | *"Open my Android project."* | Words appear live, then **Understood · Open My Android App**. The workspace connects: dev-server, tmux `firedev-pulse`, **Codex · ready** | `01`, `02` |
| 2 | *"Why is the build failing?"* | **Current task: Find out why the build fails**. Live status (*Building the app…*), Build tile turns **Failed · 2 errors**, then Codex's diagnosis: one headline, three bullets, no files changed | `04`, `05`, `06` |
| 3 | *"Fix it and run the tests."* | *Editing build.gradle.kts…*, *Editing WeeklySummary.kt…*, Edit tags in the steps, Build passing, Tests **18 / 18 passed** | `07`, `08`, `09` |
| 4 | Remote: → ↓ to Changes, OK | **What changed**: 2 files, +5 −1; a readable diff, line numbers, ↓ for the next file | `10`, `11` |
| 5 | *"Build the app."* | **Build complete**: tests 18/18, Android build, APK generated, finished in Ns | `12`, `13` |
| 6 | *"Deploy the preview."* | **Approval required**: the exact command, reason, risk, Level 2 · System change. Reject is focused | `14` |
| 7 | Remote: → then OK on **Approve** | **Deployment complete · All checks passed**: tests, build verified, health check, preview live | `15`, `16` |
| 8 | (optional) OK on the task card | Activity: every step in order; OK on a step shows its raw terminal output | `17`, `18` |

Extra beats if you have time:

- *"Undo that change"* restores the files from before the agent's last task.
- *"Stop"* during a task kills the agent's run (its tmux window) immediately.
- Pull the network or kill the service mid-task: the TV says it is reconnecting, the run keeps going in tmux,
  and FireDev reattaches with nothing lost.
- A level 3 action (try a project action like `wipe: { run: "rm -rf build", level: 3 }`) turns the sheet red and
  needs OK held for two seconds.

## If something goes wrong on camera

- The agent's wording differs between takes. The structure doesn't: the same files, 2 errors, 18 tests.
- `./firedev demo reset` restarts cleanly (it also stops the project's tmux session).
- `./firedev status` speaks the current state; `tmux attach -t firedev-pulse` shows exactly what is running.
