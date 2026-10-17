# FireDev

**FireDev turns Fire TV into a voice-first development workstation, letting developers control AI coding agents running on remote machines through SSH.**

Pick a project with the remote. Say *"Why is the build failing?"*. Codex investigates on your own machine
while the TV shows what it is doing, in type you can read from the sofa. Say *"Fix it and run the tests"* and
watch the edits, the test count and the build. Say *"Deploy the preview"* and FireDev stops: it shows the
exact command, the reason and the risk, and nothing runs until you press Approve on the remote.

![Workspace while Codex works](docs/screens/04-agent-investigating.png)

FireDev is not a terminal on a TV, speech-to-shell, or remote desktop. The coding agent does the work. The
TV is where you watch progress, inspect diffs, errors and tests, and approve anything risky. Your voice is
how you give instructions.

- [ARCHITECTURE.md](ARCHITECTURE.md): how it fits together
- [SECURITY.md](SECURITY.md): the safety model, risk levels and approvals
- [DEMO.md](DEMO.md): the 60–90 second demo, step by step
- [docs/DEVPOST.md](docs/DEVPOST.md): hackathon write-up
- [docs/VIDEO_PLAN.md](docs/VIDEO_PLAN.md): shot list for the demo video

## What works, what's simulated, what's next

| | Status |
|---|---|
| SSH to your machine with keys (asyncssh), reconnect with backoff | **Working**, tested against a real sshd |
| Persistent tmux session per project; runs keep going when the TV sleeps or the network drops | **Working**, tested (drop, reconnect, no lost output) |
| FireDev restarts and reattaches to a running agent turn | **Working**, tested |
| Codex: real `codex exec --json` turns in its workspace-write sandbox, resumed by thread id | **Working**, full demo journey passes against real Codex (about 75 s) |
| Structured events: agent steps, commands, file changes, tests (JUnit XML), build errors, deploys | **Working** |
| Risk levels 0–3, approvals on the TV, held confirmation for level 3 | **Working**, tested with remote keys |
| Agent requests outside its sandbox (`firedev run`, `firedev request`) gated by the TV | **Working**, tested |
| Diff review, raw terminal output, undo of the agent's last change | **Working**, tested |
| Fire TV app (Kotlin WebView shell + TV interface), D-pad only | **Working** in the browser at 1080p (11 remote-key tests); APK builds. **Not yet run on a physical Fire TV** |
| Phone microphone (`/voice`): live transcript on the TV, typed fallback | **Working** typed and over WebSocket. Speech recognition needs HTTPS; not yet tried on a phone |
| Pairing a TV with a 6-digit code | **Working**, tested |
| MCP server (`/mcp`, `firedev mcp` stdio) | **Working**, unit-tested |
| Claude Code adapter (`claude -p --output-format stream-json`) | Implemented, parser tested; **not run end to end** |
| Plain shell adapter | Implemented |
| Alexa | **Simulated**: a real Alexa Skills Kit endpoint (`/alexa`) and interaction model, driven from the phone page. Not published as a skill (no request-signature check yet) |
| On-device speech on the TV | Used if the device has a recogniser; most Fire TVs don't |
| Multiple machines in one UI, project setup from the TV, Alexa+ integration | Future |

## Requirements

- **Service machine** (laptop or VPS): Python 3.11+, Node 18+ (only to rebuild the TV interface), and SSH access to your dev machine.
- **Dev machine** (can be the same box): `tmux`, `git`, `bash`, and [Codex CLI](https://github.com/openai/codex) signed in (`codex login`). Optional: Claude Code.
- **Fire TV** in developer mode (Settings → My Fire TV → Developer options → Install unknown apps / ADB debugging), on a network that can reach the service.

## Setup

```bash
git clone <this repo> firedev && cd firedev
tools/setup.sh                 # venv, TV bundle, ~/.config/firedev/firedev.yaml
```

### 1. SSH key for FireDev

Use a dedicated key, so you can revoke FireDev without touching your other access:

```bash
mkdir -p ~/.config/firedev/keys
ssh-keygen -t ed25519 -N '' -C firedev -f ~/.config/firedev/keys/firedev_ed25519
ssh-copy-id -i ~/.config/firedev/keys/firedev_ed25519.pub you@dev-server
ssh -i ~/.config/firedev/keys/firedev_ed25519 you@dev-server true   # records the host key in known_hosts
```

FireDev checks host keys against `~/.ssh/known_hosts` and never stores passwords. If you must use a password,
set `auth: password` and `password_env: SOME_VAR`; the password is read from that environment variable.

### 2. Connect a machine and a project

Edit `~/.config/firedev/firedev.yaml` (the example is [config/firedev.example.yaml](config/firedev.example.yaml)):

```yaml
machines:
  dev-server:
    host: 203.0.113.10
    user: alex
    auth: key
    key_file: ~/.config/firedev/keys/firedev_ed25519

projects:
  - id: pulse
    name: My Android App
    machine: dev-server
    path: ~/code/pulse
    agent: codex                 # codex | claude | shell
    tmux_session: firedev-pulse
    actions:                     # what "run the tests", "build", "deploy" mean for this project
      test:   { kind: tests,  run: ./gradlew testDebugUnitTest, reports: "app/build/test-results/**/*.xml" }
      build:  { kind: build,  run: ./gradlew assembleDebug, artifact: app/build/outputs/apk/debug/app-debug.apk }
      deploy_preview: { kind: deploy, level: 2, run: ./scripts/deploy-preview.sh,
                        reason: "Publish the latest build to testers", risk: "The preview server restarts" }
```

### 3. Codex

On the dev machine: install Codex CLI and run `codex login` once. FireDev runs it as
`codex exec --json` with `sandbox_mode=workspace-write` and no network. If the agent needs to write somewhere
else (a Gradle cache, for example), list it in `agent_options.writable_roots`. If the `codex` that a login
shell finds isn't the one you want, set `agent_options.binary`.

### 4. Start FireDev

```bash
./firedev serve            # http://127.0.0.1:8130 ; set server.host: 0.0.0.0 for a TV on your network
```

Open `http://127.0.0.1:8130/tv/` in a desktop browser to see the TV interface at 1920×1080 (arrow keys,
Enter = OK, Escape = Back, M = Menu).

### 5. Install on Fire TV

```bash
cd tv/android
echo "firedev.server=http://192.168.1.20:8130" >> local.properties   # where the TV finds the service
./gradlew assembleRelease                                              # signed with the debug key for sideloading
adb connect <fire-tv-ip>:5555
adb install -r app/build/outputs/apk/release/app-release.apk
```

Open FireDev on the TV. It shows a 6-digit code; on the service machine run `./firedev pair 123456`.
To point an installed TV at a different service without rebuilding:
`adb shell am start -n dev.firedev.tv/.MainActivity -e server http://host:8130`.

### 6. Talk to it

- **Phone microphone**: on the TV choose *Phone microphone* and scan the code. Hold the button and speak;
  the words appear on the TV as you say them. Browser speech recognition needs HTTPS (put the service behind
  a reverse proxy with a certificate and set `server.public_url`). Over plain HTTP, type, or use your keyboard's
  dictation key.
- **Remote only**: press ☰ (or *Speak*) and choose a suggested command.
- **Terminal**: `./firedev say "why is the build failing?"`
- **MCP**: `./firedev add-assistant "Claude Desktop"` prints a token; run `FIREDEV_TOKEN=… ./firedev mcp` as a stdio MCP server.
- **Alexa (simulated)**: switch the phone page to *Alexa (simulated)*; it sends real Alexa Skills Kit requests to `/alexa` and speaks the reply.

Things you can say: *open my Android project · why is the build failing · fix it and run the tests · run the
tests · build the app · show me what changed · check the logs · deploy the preview · what are you doing · stop ·
continue · undo that change*. Anything else becomes a task for the coding agent.

## Demo project

`./firedev demo setup` creates `~/firedev-demo/pulse`: a small Android app with two compile errors and
18 unit tests. `./firedev demo reset` puts it back between takes. See [DEMO.md](DEMO.md).

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q                       # 78 unit tests
FIREDEV_TEST_SSH=1 ../.venv/bin/python -m pytest -q                  # + 11 over real SSH/tmux to localhost
npm run test:e2e                                                     # 11 TV tests driven by remote keys
cd tv/android && ./gradlew testDebugUnitTest assembleDebug           # shell tests + APK
.venv/bin/python tools/demo_flow.py                                  # the whole demo against real Codex
```

The SSH and browser tests use a scripted stand-in for Codex that emits the same JSONL events
(`backend/tests/fixtures/fake-codex`), so they are fast and repeatable. `tools/demo_flow.py` and
`tools/capture-demo.mjs` use the real agent.

## Repository

```
backend/firedev/      service: config, ssh, session (tmux runs), agents/, risk, intents, service, app, mcp, alexa, cli
remote/bin/           helpers installed on the dev machine: firedev (agent requests), firedev-follow, firedev-watch
tv/web/               TV interface (ES modules → dist/app.js), fonts
tv/android/           Fire TV shell (Kotlin WebView)
companion/            phone microphone page
alexa/                Alexa interaction model
demo/pulse-android/   the broken sample app
e2e/                  Playwright remote-key tests
docs/                 screenshots, architecture diagram, write-ups, brand
```
