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

- A sandboxed agent can still read files your user can read. Run the agent as a user that only has what the
  project needs if that matters to you.
- Level 1 is not gated. Edits are visible and undoable, but not prevented.
- The risk classifier is pattern-based. It is conservative (unknown commands are level 1, anything outside the
  sandbox is at least 2), but a cleverly wrapped command inside a script the agent wrote runs at the script's
  level. That's why the agent never runs anything outside its sandbox without FireDev in between.
