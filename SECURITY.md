# Security model

FireDev lets spoken words change code on real machines, so it is built around one rule: **the AI can propose
anything, but nothing risky happens until a person approves it on the TV with the remote.**

## Layers

1. **Voice is never shell.** Utterances become one of a fixed set of intents (run the declared tests, build,
   deploy, show changes, stop…) or a task for the coding agent. "rm -rf /" spoken aloud becomes a request to
   a sandboxed agent, not a command.
2. **The agent is sandboxed.** Codex runs with `sandbox_mode=workspace-write` and network access off: it can read,
   and write inside the project. It can't use the network, can't `sudo` (no-new-privileges), and can't write
   elsewhere. We checked this on the target machine: `sudo` fails and Gradle can't even open a socket inside the
   sandbox. The Claude Code adapter runs in print mode with a short tool allow-list (read-only commands,
   file edits and the FireDev helper); anything else is denied.
3. **The only way out is the request queue.** Builds, tests and anything outside the project go through
   `firedev run` / `firedev request`. FireDev classifies each command itself (the agent's opinion of the risk is
   never used), and level 2+ waits for the TV.
4. **Only the TV approves.** Every client has a role:

   | Role | Gets it how | Can |
   |---|---|---|
   | `tv` | 6-digit pairing code confirmed with `firedev pair` on the service machine | everything, including approving |
   | `voice` | QR link created by a paired TV, valid 12 hours | voice commands, status, reject, stop. **Never approve** |
   | `assistant` | `firedev add-assistant` (MCP), or an Alexa account linked on the TV | commands and status; approve only if `server.remote_approval: level2`, and never level 3 |
   | `admin` | `~/.config/firedev/admin.token` (mode 600) | pairing, devices, reset. **Not approving**: an agent running on the same box might read the file |

   Saying "approve" moves the TV's focus to the Approve button. The press still comes from the remote.

## Risk levels

| Level | Examples | What happens |
|---|---|---|
| 0 · Read-only | `ls`, `git status`, `git diff`, reading files, logs, tests and builds | Runs; shown in the activity feed |
| 1 · Project edit | editing files, `sed -i`, `git commit`, formatters, unknown commands | Runs; always visible, with an Edit tag. Undo restores the files from before the task |
| 2 · System change | `git push`, `sudo …`, service restarts, package installs, deploy scripts, migrations, `scp`/`rsync`, data-changing `curl`, `kill` | Full-screen approval on the TV: exact command, reason, risk. **Reject is focused first**; Back doesn't dismiss |
| 3 · Destructive | `rm -rf`, `find -delete`, force push, `git reset --hard`, firewall changes, user and permission changes, `DROP TABLE`, `mkfs`/`dd`, reboot | As level 2, plus a red "strong confirmation": **hold OK for two seconds**. The service rejects a level 3 approval from the TV unless the held flag is set |

Compound commands take their riskiest part (`git status && git push` is level 2). `sudo` lifts anything to at
least 2. Anything run outside the sandbox at the agent's request is at least level 2. Projects can declare a
higher level for an action (the demo's `deploy_preview` is level 2 with its own reason and risk text). The rules
are in `backend/firedev/risk.py`, with tests in `backend/tests/test_risk.py`.

## Live previews

The preview shows code the agent just wrote, so it never runs inside FireDev's own interface:

- **Its own origin.** Pages are served by the preview gateway on a separate port (e.g. `127.0.0.1:8132` inside the TV, a different origin from the interface), so a
  page can't read the TV's token or script FireDev's interface. The TV frames it with
  `sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-downloads"`: no top-level navigation, no popups.
  It talks to FireDev only through `postMessage`, and FireDev only acts on messages from the frame it opened.
- **Only paired TVs.** `preview_open` (TV role only) returns a one-time ticket, valid 90 seconds. The gateway trades it
  for an HttpOnly session cookie bound to that TV and project, and checks the TV still exists on every request:
  unpairing it ends its previews. Without a session, every path answers 403. FireDev's cookie is removed before
  requests reach the dev server.
- **No open proxy.** The gateway reaches exactly one place per session: `127.0.0.1:<port>` on that project's
  machine, through the project's SSH connection, where the port is one FireDev started or found the project's own
  processes listening on. There is no parameter that names a host or URL. Dev servers FireDev starts bind
  127.0.0.1 only, so nothing is exposed on the dev machine's network.
- **Starting code is approved.** FireDev's own static server runs no project code and starts freely. A framework
  dev server runs the project's config and scripts outside the agent's sandbox: when the agent asks for it, the TV
  shows a level 2 approval; when you ask (OK on the TV, or by voice), that is the approval. Installing dependencies
  always asks first (it downloads and runs install scripts).
- **Same site.** A port is a different origin but the same *site* as the host name, so the page could send
  same-site requests to other services on that host with cookies the browser holds for them. The TV's WebView holds
  none, but if you also open the preview in your desktop browser, prefer a `server.preview_url` on a host name of its own.

## Credentials and data

- SSH: keys by path (a dedicated `~/.config/firedev/keys/firedev_ed25519` is recommended). Host keys are checked
  against `known_hosts`. Passwords are refused in the config file; `auth: password` reads one from an
  environment variable.
- Device tokens: 32 random bytes; only their SHA-256 is stored (`~/.config/firedev/devices.json`, mode 600).
  The TV keeps its token in WebView storage.
- History: events (not command output) in `~/.config/firedev/data/history/<project>.jsonl`.
- On the dev machine: run logs in `~/.firedev/projects/<id>/runs`; the queue in `<project>/.firedev/` (added to
  `.git/info/exclude`, so it never shows in git).

## Network

The service listens on 127.0.0.1 by default. To reach it from a TV, either bind to your LAN (`server.host:
0.0.0.0`) on a network you trust, or put it behind a reverse proxy with HTTPS. HTTPS is also what lets the
phone page use speech recognition. Every endpoint except `/api/health` and the static pages needs a token, except `/alexa`, which instead needs a
request signed by Alexa (certificate, `Signature-256` and a timestamp within 150 seconds, checked as Amazon
specifies) for the configured skill ID, from an Alexa account that was linked by holding OK on the TV.

## Known gaps

- A live preview runs the project's dev server with your user's permissions, outside the agent's sandbox (after the
  approval above). What the page itself does in the TV's browser is contained by the origin and the sandbox.

- A sandboxed agent can still read files your user can read. Run the agent as a user that only has what the
  project needs if that matters to you.
- Level 1 is not gated. Edits are visible and undoable, but not prevented.
- The risk classifier is pattern-based. It is conservative (unknown commands are level 1, anything outside the
  sandbox is at least 2), but a cleverly wrapped command inside a script the agent wrote runs at the script's
  level. That's why the agent never runs anything outside its sandbox without FireDev in between.
