# FireDev, local-first

Goal: thousands of Fire TV users, zero hosting for the FireDev developer. Everything runs on the user's TV and the
user's own machines; credentials, code and agent logins never leave them.

## What is "central" today (review, 2026-10-18)

The FireDev service (`backend/firedev`) was always meant to run on the user's machine (`firedev serve` on a laptop or
VPS). It is central today only because of how it is deployed:

| Piece | Today | Hosted by us? | Local-first home |
|---|---|---|---|
| FireDev service: API, `/ws`, sessions, tmux runs, agents, approvals, intents, generative UI, history | `firedev.service` on musastudios.co.uk behind Caddy `/firedev/*` | **Yes** (deployment only) | User's machine: `firedev serve` from the CLI. No code change needed for the logic |
| TV's server address | Built into the APK (`firedev.server=https://musastudios.co.uk/firedev`) | **Yes** | TV finds the machine itself: LAN discovery, or an SSH server the user enters |
| Pairing (6-digit code, `firedev pair` on the service machine) | On our VPS | **Yes** (deployment) | Same flow, on the user's machine. In SSH mode the SSH login itself is the proof, so no code |
| Coding agents (Codex, Claude Code) and their logins | Run on the dev machine over SSH from the service | Only because the service is ours | Dev machine, as now. FireDev never sees agent credentials |
| SSH from the service to the dev machine | Service on our VPS → `localhost` | Yes | `machine: local` (no SSH to itself) when the service runs on the dev machine; SSH stays for other machines |
| Live preview gateway | Second listener in the service; briefly exposed on `musastudios.co.uk:8443` (**reverted**) | Was | In `firedev serve` on the user's machine, reached through the TV's own connection (below). Never public |
| Voice: on-device recogniser, Android TTS, two-way conversation, generative UI renderer, D-pad focus, approvals UI | TV app | No | TV app (unchanged) |
| Phone microphone page (`/voice`) | Served by the service; browser speech recognition needs HTTPS | Yes (HTTPS via our domain) | Served on the LAN by `firedev serve`. **Honest limit:** over plain HTTP a phone browser can't use speech recognition, so it falls back to typing or the keyboard's dictation key |
| Alexa skill (`/alexa`) | Public HTTPS endpoint + a skill in our Amazon account | **Yes, inherently** | Can't be local-first: Alexa only calls public HTTPS endpoints. Make it optional "bring your own skill and endpoint", or drop it from the product |
| MCP (`/mcp`, `firedev mcp`) | Service | Deployment only | User's machine (unchanged) |

## Target: two ways to connect, no relay

```
LAN mode                                         SSH mode (remote VPS)
TV ── LAN (mDNS _firedev._tcp) ──▶ laptop         TV ── SSH (user's key) ──▶ VPS
        firedev serve, machine: local                 firedev serve on 127.0.0.1, machine: local
        agents run here                               agents run here
TV app: relay 127.0.0.1:P ──▶ laptop:8130/8132    TV app: SSH local forwards 127.0.0.1:P ──▶ VPS 127.0.0.1:8130/8132
```

In both modes the TV interface talks to `http://127.0.0.1:<port>` inside the TV, which the Kotlin shell connects to
the user's machine. That one choice keeps every existing API, WebSocket and test unchanged, and fixes the preview
cookie problem: `127.0.0.1` is a secure context, so the preview session cookie (`SameSite=None; Secure`) works next
to the app's local files without HTTPS certificates. The preview stays on its own origin (its own port).

- **LAN**: `firedev serve --lan` advertises itself with mDNS. The TV lists machines it finds (Android `NsdManager`),
  the user picks one, the TV shows a code, `firedev pair <code>` on the laptop approves it (the existing flow). LAN
  traffic should be encrypted: the service makes a self-signed certificate and the TV pins its fingerprint during
  pairing (the code confirms it), or the TV relay speaks TLS to the service.
- **SSH**: the TV generates its own key (kept in Android Keystore) and shows the public key as a QR code plus a
  one-line command to add it to `~/.ssh/authorized_keys`. Host key: shown once and pinned. The shell (an SSH library
  such as sshj) logs in, starts `firedev serve` on the VPS if it isn't running (in tmux, 127.0.0.1 only), asks it for a
  TV token over the same SSH session (`firedev pair --issue`), and forwards the API and preview ports locally.
- **Neither works** (laptop behind NAT, TV elsewhere): FireDev provides no relay. The user needs an SSH server the TV
  can reach, or their own VPN (Tailscale, ZeroTier, WireGuard), which then looks like LAN mode. Setup says this plainly.

What moves where:

- **TV app (Kotlin shell)**: discovery, SSH client and key generation, host-key pinning, port forwards / LAN relay,
  saved machines, onboarding screens (pick a machine, QR for the key), auto-reconnect of the transport. Voice, TTS,
  generative UI and the remote already live here.
- **User's CLI (`firedev`)**: `serve` (API, sessions, previews, agents), `pair`, `--lan` advertising, `machine: local`,
  `setup` (writes the config, checks Codex/Claude logins, prints the QR), `pair --issue` for SSH mode. Installable with
  `pipx install firedev` (or a single bundled binary) on the user's machine.
- **Our infrastructure**: none in normal operation. The APK ships through the Appstore. musastudios.co.uk/firedev
  stays up only until the replacement is verified on a real TV, then the Caddy route and service are retired.

## Plan (each step verified before anything is removed)

1. **Service on the user's machine**: `machine: local` (subprocess instead of SSH, same `Remote` interface: run,
   stream, write_file, port connections), `firedev serve --lan` with mDNS, `firedev setup`. Verified by the existing
   184 backend tests run against both transports and the browser suites.
2. **TV transport**: LAN discovery and relay, then SSH client, key, QR, forwards, in the Kotlin shell. Verified with
   JVM tests against a local sshd and mDNS, `tools/shell-sim.mjs` for the interface, then the user's Sony TV.
3. **First-run on the TV**: no built-in server; onboarding chooses LAN or SSH. Then remove `firedev.server` from the
   APK build.
4. **Decisions for the owner**: Alexa (optional bring-your-own, or drop), phone microphone over plain HTTP (typing
   and dictation only), packaging of the CLI.
5. Retire the musastudios.co.uk deployment.
