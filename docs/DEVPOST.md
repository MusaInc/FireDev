# FireDev: a voice-first development workstation for Fire TV

## Inspiration

AI coding agents changed what "writing code" means: you describe, they do, you review. Most of that loop
doesn't need a keyboard. It needs a big screen you can read from across the room, a way to say what you
want, and a trustworthy way to say "yes, do that risky thing". Fire TV already has the big screen, a remote,
and a voice culture through Alexa. We wanted to see what development feels like from the sofa.

## What it does

FireDev turns Fire TV into a voice-first development workstation, letting developers control AI coding
agents running on remote machines through SSH.

1. Pick a project on the TV. FireDev connects to your machine over SSH, opens a persistent tmux session and
   starts Codex.
2. Speak: *"Why is the build failing?"* Codex investigates your real repository. The TV shows each step in plain
   words (*Building the app…*, *Reading MainActivity.kt…*), the build tile turns red with **2 errors**, and you
   get a three-line diagnosis.
3. *"Fix it and run the tests."* You watch the edits and the test count climb to **18 / 18**.
4. Review the diff with the remote.
5. *"Deploy the preview."* FireDev stops and shows a full-screen approval: the exact command, why, and what
   could go wrong. Reject is focused first. Nothing runs until you press Approve.

## How we built it

- **Fire TV app**: a native Kotlin shell hosting a TV interface written for a 10-foot screen: 1920×1080
  stage, large type, an ivory focus that lifts, and full D-pad navigation (nothing needs touch).
- **FireDev service** (Python, FastAPI, asyncssh): one command API shared by the TV, the phone microphone, the
  CLI, an MCP server and an Alexa skill endpoint.
- **Runs in tmux**: every agent turn, test and build is a tmux window whose log and exit code live on the
  machine. The TV can sleep, Wi-Fi can drop, the service can restart: FireDev reattaches and carries on from
  the exact byte it had read.
- **Agent adapters**: Codex (`codex exec --json`, resumed by thread so it remembers what "it" is), Claude Code
  (`stream-json`), and plain shell, all normalised into the same structured events. The TV never scrapes a
  terminal.
- **Safety by construction**: Codex runs in its workspace-write sandbox with no network. Its only way to build,
  test or touch anything outside the repo is a small `firedev` helper that queues the request. FireDev
  classifies the command itself (levels 0–3), asks the TV when it matters, runs it outside the sandbox, and
  streams the output back to the agent. Only a paired TV can approve. Level 3 commands need OK held for two
  seconds.

## Challenges

- Gradle can't run inside Codex's network-less sandbox: it can't even open its daemon socket. Opening the
  network would have let the agent `git push` unapproved. Instead, builds and tests became FireDev-run project
  actions that the agent requests, which also gave the TV real JUnit counts and compiler errors.
- Codex's sandboxed login shell drops custom PATH entries, so the helper is passed by absolute path.
- Remote helper processes outlived dropped SSH connections and silently claimed requests. Now they exit with
  their connection, and a regression test proves no process is left behind.

## What we're proud of

The whole demo is real: real SSH, real tmux, real Codex fixing a real Android build, a real APK and a real
preview deployment with a health check, in about 70 seconds of agent time. 90 backend tests (including live SSH and
tmux), 11 remote-key browser tests, and the full journey scripted against the real agent.

## What's next

Alexa+ as the native voice (today the Alexa skill endpoint is real but driven from a simulated client),
project setup from the TV, several machines side by side, and per-project agent users for stronger isolation.

## Built with

Kotlin · Android WebView · JavaScript · Python · FastAPI · asyncssh · tmux · Codex CLI · Claude Code · MCP ·
Alexa Skills Kit · Playwright
