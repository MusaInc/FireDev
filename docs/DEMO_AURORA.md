# Hackathon demo: Aurora Lodge, built by voice

Three minutes. Real Codex builds a premium website from an empty folder, live on the TV, then redesigns it on
request while the page updates in front of you. Nothing in it is scripted on the agent's side: the stand-in agent is
not used, every line of code on screen is a real edit replayed from the file, and the test results come from real
browser checks.

## The project

`demo/aurora-site` is the template. It is an empty website project for Aurora Lodge, a fictional glass-cabin retreat
in Lapland, and it contains only:

- `AGENTS.md`: the brief Codex reads. It sets an agency-quality bar (display serif and clean sans, CSS/SVG art
  instead of stock photos, tasteful motion, responsive from 390 px to 1920 px, accessible). It also covers the TV:
  readable code that looks good replayed, `transform`/`opacity`-only animation for older Fire TVs, and no network.
- `tests/site.check.mjs` and `test.sh`: 14 browser checks in headless Chromium at phone, tablet, laptop and TV sizes.
  They cover script errors, sideways scrolling at each size, broken in-page links, missing local files, alt text,
  landmarks, one `h1`, real copy (no lorem ipsum), reduced motion, and working offline. Codex is told never to edit them.

`firedev demo setup|reset --project aurora` copies it to `~/firedev-demo/aurora` as a fresh git repo. A reset
also stops the project's tmux session, clears FireDev's view of the project, forgets Codex's thread (so the next
take really starts from nothing), stops the preview server and clears the live-code memory. It never touches FireDev
itself or other projects.

## Before you record (5 minutes)

```bash
./firedev demo reset --project aurora     # empty project, fresh Codex thread
./firedev status aurora                   # connected, Codex ready
```

- Fire TV: open FireDev; it shows the splash, then Home ("What shall we build?").
- Phone: press OK on **Start talking** and open the phone-microphone link. Speak into the phone.
- Recording: a capture card on the TV's HDMI at 1080p. A second camera on the sofa and phone is optional.
- Optional B-roll: `tmux attach -t firedev-aurora` on a laptop shows Codex really running.

## Exact voice commands

| # | Say | What happens by itself (no remote) | Rehearsal timing |
|---|---|---|---|
| 1 | *"Build a beautiful modern website for Aurora Lodge, a glass cabin retreat in Lapland. Keep it light and airy, like a snowy morning."* | The workspace opens with stages Understand → Explore → Build → Verify → Done. Codex says its plan aloud. Files appear in the list. Once the page **and its stylesheet** load, the TV switches to three panels: Codex's steps, the live site and LIVE CODE replaying each real edit. The checks run (14/14). | Task in 1.4 s; site on screen 2:15; done 3:15 |
| 2 | *"Now give it a dramatic makeover. Switch to a dark midnight theme, with glowing northern lights moving across a full screen hero, a huge headline, and warm gold accents."* | The live site stays on screen from the first second. Each save reloads it (CSS changes swap in place, without a flash), and LIVE CODE types out the diff. | On screen in 0.8 s; done 1:25 |
| 3 | *"Run the checks and walk me through what you built."* | Tests 14/14 in the left panel. Codex walks you through the page aloud. | 27 s |

A light first version makes the midnight makeover unmistakable: ask for both in this order. Agent timing varies
between takes; the structure doesn't. Keep the camera rolling and cut the waits in the edit.

## Three-minute script and shot plan

| Time | Shot | On screen | Audio |
|---|---|---|---|
| 0:00–0:08 | Living room, TV off, remote in hand. Press Home → FireDev. | Ember splash → Home: orb, "What shall we build?" | VO: "This is an ordinary Fire TV. Tonight it's my development workstation." |
| 0:08–0:18 | Close on the phone, then the TV. Speak command 1. | Words appear as spoken; "I'll do that in Aurora Lodge." Workspace opens on **Understand**. | Live voice. FireDev answers in the TV's own voice. |
| 0:18–0:40 | TV full frame. | Codex explains its plan aloud; stage rail moves to **Explore**, then **Build**; files appear (index.html +78…). | Codex's spoken plan. Lower third: *real Codex · real files · nothing mocked*. |
| 0:40–1:20 | TV full frame, speed-ramp the quiet parts 4–8×. | The switch to three panels. Left: what Codex is doing. Centre: the site appears. Right: LIVE CODE types the real CSS. | Narration ("I've created styles.css…"). VO: "I didn't touch the remote. FireDev moves the screen to wherever the work is." |
| 1:20–1:35 | Slow push-in on the centre panel. | The finished first version: hero, cabin, typography. Tests 14/14 in the steps. | Codex: "The site is on the TV now…" |
| 1:35–1:45 | Phone, then the TV. Speak command 2. | The live site stays put; "Codex · working". | Live voice. |
| 1:45–2:25 | TV full frame (the wow moment, speed-ramp 2–4×). | The page turns to midnight, the aurora starts to move, the headline grows. LIVE CODE replays each edit, with an ember ring on every update. | Codex narrating. VO: "Every change lands in front of me, while it's being written." |
| 2:25–2:40 | Speak command 3. | Tests: 14/14 passed; Done 5 of 5. | Codex's spoken walk-through (keep 10–15 s). |
| 2:40–2:52 | Split: TV and `tmux attach` on a laptop, or the architecture diagram (`docs/architecture.png`). | Real Codex in tmux on the dev machine. | VO: "Your machine, your agent, over SSH. Risky steps still wait for your approval." |
| 2:52–3:00 | Hero shot of the finished site on the TV, logo. | FireDev wordmark. | VO: "FireDev. Build software from the sofa." |

Captions to burn in: the three spoken commands (as they're said) and the corner tag *real Codex · real files ·
nothing mocked* from 0:18 to 2:40. Keep FireDev's and Codex's TV voice audible under the voice-over (duck it, don't
mute it).

## If something goes wrong on camera

- Codex says the site already exists: the reset didn't run. Run `./firedev demo reset --project aurora` and start again.
- The site isn't on screen 30 s after styles.css appears: say *"Show me the live preview."*
- To start over mid-take, say *"Stop"*, then reset.
- The design differs every take. That's the point: it's really being built. If a take's design is weak, reset and do
  another one (each is about 6 minutes).

## Evidence: rehearsals with real Codex

`tools/rehearse.mjs` drives a FireDev instance through the commands above with real Codex. It watches the TV UI at
1920×1080 the whole time with no key presses, and records a timeline, screenshots and (with `REHEARSE_VIDEO=1`) a
video:

```bash
REHEARSE_VIDEO=1 node tools/rehearse.mjs http://127.0.0.1:8130 tokens.json out/ script.json
```

The final rehearsal (take 3, 2026-10-19) went from an empty folder to the finished, redesigned site in about
6 minutes, with no key presses and no page errors. The browser checks passed 14/14 after every step. Its timeline,
report and screenshots are in `docs/rehearsal/`.

### The video

`tools/cut-demo.mjs` turns a recorded rehearsal into a ~3-minute video. It keeps the recording in order, at real
speed around every command, spoken line, screen switch and test result, and fast-forwards the waits with a visible
*Fast-forward N×* badge. It adds the commands as captions, plus title and end cards in the TV's own font. The end
card's numbers come from the rehearsal report.

```bash
node tools/cut-demo.mjs <rehearsal dir> firedev-demo.mp4 180
```

The take 3 cut is at `~/firedev-demo/video/firedev-demo-3min.mp4` (177 s, 1080p), next to the raw TV recording.
It is silent: headless Chromium has no text-to-speech, so what FireDev and Codex say appears in the TV's own
speech captions. For the submission, record the real TV, which speaks with its Android voice, and use this cut as
the edit reference.

Failures the rehearsals found, now fixed:

1. **Bare HTML on screen.** Codex wrote `index.html` about 50 s before `styles.css`, and the TV switched straight to
   an unstyled page that looked broken. The live switch now waits until the page's own stylesheets load
   (90 s grace).
2. **"Aurora Lodge is already built."** A reset done before the project connected didn't clear the saved Codex
   thread, so the next take resumed the old conversation. The forgotten thread is now applied on connect.
3. **One giant minified CSS line** in LIVE CODE. The brief now asks for readable code, because it's replayed on
   the TV.
4. **"Now I'm running pwd."** The narrator no longer reads out housekeeping commands.
5. **The closing summary said twice.** It was said once by Codex and once as the reply. A reply that repeats what
   the agent said in the last 45 s now stays quiet.
6. **Task titles lost their capitals** ("aurora lodge"). Titles now keep the casing the words were said with.
7. **A regex read aloud** ("now I'm searching for ^(:root|\.hero…"). Search patterns are now said as "searching
   the code".
8. **"Switch the whole site…" went to the wrong project.** In production, Web Lab answers to "site" and "website".
   Inside a project, a generic word now means the current project, unless it's said as something new ("build me
   a landing page"). Naming another project still switches.

## Pacing (2026-10-19)

- Every request is acknowledged by FireDev at once ("On it. Codex is starting now."), about 0.1 s after it's heard.
  Codex's own first words follow 7–11 s later.
- Between steps, the TV shows Codex's real reasoning headlines ("Designing animated aurora overlay"), not a generic
  "Thinking". Headlines about its own reporting are left out. After 15 s with no new step, it says so ("No new step
  for 20 s · still working").
- Full-screen reveals: the page fills the TV for a few seconds when it first appears ("First look") and when Codex
  finishes ("Done"). Any key ends one at once, and the live-code replay pauses during a reveal so no edit plays unseen.
- Timings: each task records when it was heard, dispatched, Codex's first event, first file, first speech and
  done. They're in the task snapshot and logged as `timings <project> <task>: …`.
  The phone mic sends as soon as the words are final (at most 0.7 s after release) and reports how long recognition took.

## Camera direction in the live preview (2026-10-19)

While Codex works, the preview directs itself, and everything it shows is the real page:

- **Following edits comes first.** Each real code change is mapped to the page element it touched: ids and classes
  on changed HTML lines (or the nearest tag above a text edit), the rule selectors of changed CSS, the element using a
  changed image or SVG, and `querySelector` targets in JS. The view glides there (eased, about 1.5 s) and, once the saved
  version has rendered, outlines that element once. Labels read "Updating the cabins section". Theme-wide changes
  (`:root`, `body`), selectors that match many things (`.card`), and elements parked off the page keep the current
  view.
- **Touring when quiet.** After 12 s without a visible update while Codex works, it holds each real section for 7 s
  and glides to the next ("Exploring the page"), then drifts back to the top at the end.
- **Viewer first.** Any remote key, "Use the page", or scrolling/touching the page stops it at once. It resumes after
  20 s without a key. The view's position survives reloads.
- Made for Fire TV WebViews: the in-page helper is plain ES5 and scrolls by hand per frame, not with smooth scrolling.

Tests: `npx playwright test --config e2e/camera.config.mjs` (mapping, tour, follow and highlight, viewer takeover,
naming), plus the live-build test in `e2e/preview.spec.mjs`. Real-Codex rehearsal take 7 toured and followed edits
for the whole build.
