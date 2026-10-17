# Demo video plan (75–90 s)

Record the TV with a capture card (or the browser preview at 1920×1080), the phone in hand on a second
camera, and optionally the dev machine's `tmux attach -t firedev-pulse` for one split-screen moment.
Run `./firedev demo reset` before each take. Agent time is about 70 s; cut the waits, never fake a result.

| Time | Shot | Audio |
|---|---|---|
| 0–6 s | Sofa, remote, TV on FireDev Home. Title: *FireDev: develop from anywhere* | "What if your TV was your dev workstation?" |
| 6–12 s | Say *"Open my Android project."* Words appear on the TV as spoken; workspace connects | (live voice) |
| 12–24 s | *"Why is the build failing?"* Steps tick by; Build tile turns **Failed · 2 errors**; diagnosis appears | VO: "Codex is working on my real repo, over SSH." |
| 24–40 s | *"Fix it and run the tests."* Editing build.gradle.kts → tests **18 / 18** (speed-ramp the waits) | VO: "I watch every step. Nothing hidden." |
| 40–48 s | Remote → Changes; scroll the diff | VO: "Review the change from the sofa." |
| 48–56 s | *"Build the app."* Build complete card | |
| 56–70 s | *"Deploy the preview."* Full-screen approval; hold on the command and the risk; thumb presses Approve | VO: "Anything risky waits for me." |
| 70–78 s | Deployment complete · All checks passed | |
| 78–88 s | Split screen: tmux on the server + architecture diagram | VO: "Your machine, your agent, your approval. FireDev." |

Captions to burn in: the spoken lines, and "real Codex · real SSH · nothing mocked" in the corner during
steps 2–3.
