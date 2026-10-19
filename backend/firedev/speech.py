"""What FireDev says out loud: turning an agent's written answer into a short spoken reply.

Agents are asked to open their final message with one or two sentences meant for speech. This keeps only those,
cleans away anything that sounds wrong read aloud (markdown, code, long paths, URLs), and keeps it short enough
for a TV conversation.
"""

from __future__ import annotations

import posixpath
import random
import re

MAX_SPOKEN = 320


def spoken_text(text: str | None, max_len: int = MAX_SPOKEN) -> str:
    if not text:
        return ""
    t = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    first = ""
    for p in paras:
        lines = [ln for ln in p.splitlines() if ln.strip()]
        prose = [ln for ln in lines if not re.match(r"^\s*([-*•]|\d+[.)]|#+)\s", ln)]
        if prose:
            first = " ".join(ln.strip() for ln in prose)
            break
    if not first and paras:
        first = re.sub(r"^\s*([-*•]|\d+[.)]|#+)\s*", "", paras[0].splitlines()[0])
    return clean_for_speech(first, max_len)


def clean_for_speech(text: str, max_len: int = MAX_SPOKEN) -> str:
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"(?<![\w.])(?:~|\.{0,2})?/?(?:[\w.-]+/)+([\w.-]+)", lambda m: posixpath.basename(m.group(0)), t)
    t = re.sub(r"[*_#>]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_len:
        return t
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", t):
        if len(out) + len(s) + 1 > max_len:
            break
        out = (out + " " + s).strip()
    return out or t[: max_len - 1].rsplit(" ", 1)[0] + "…"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def echo_score(heard: str, spoken: str) -> float:
    """How much of what the microphone heard is just the TV's own speech (0..1)."""
    h = words(heard)
    if not h:
        return 0.0
    s = set(words(spoken))
    return sum(1 for w in h if w in s) / len(h)


def _names(items: list[str], limit: int = 3) -> str:
    items = list(dict.fromkeys(items))
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# The narrator's lines, plain first: playful mode picks among all of them, plain mode always takes the first.
MADE = ["I've created {x}", "Fresh out of the oven: {x}", "Say hello to {x}", "New on the scene: {x}"]
CHANGED = ["updated {x}", "given {x} a makeover", "tweaked {x}"]
READING = ["I've been reading {x}.", "Just skimmed {x}, so I know what I'm working with.",
           "Reading {x}. Homework first, fun bit next."]
NOW = ["Now I'm {x}.", "Next up: {x}.", "Right now I'm {x}."]
GOOD_NEWS = ["Nice.", "Green across the board.", "Not bad for a TV."]


def narration(activity: list[dict], now: str = "", *, playful: bool = True, rng: random.Random | None = None) -> str:
    """What the agent has been doing since it last spoke, said in its own voice ("I've created index.html and
    script.js. Now I'm editing styles.css."), so a long piece of work never goes quiet. Empty when there's nothing new.
    Playful mode varies the wording and now and then adds a light remark; bad news is always said plainly."""
    rng = rng or random.Random()
    pick = (lambda opts: rng.choice(opts)) if playful else (lambda opts: opts[0])
    made, changed, removed, read, done = [], [], [], [], []
    for a in activity:
        kind, title = a.get("kind"), a.get("title") or ""
        if kind == "file":
            name = posixpath.basename(a.get("detail") or title.split(" ", 1)[-1])
            (made if title.startswith("Created") else removed if title.startswith("Deleted") else changed).append(name)
        elif kind == "command" and title.startswith("Reading "):
            read.append(title[len("Reading "):])
        elif kind in ("tests", "build", "deploy"):
            done.append((title.rstrip("."), a.get("status") != "failed"))
    changed = [n for n in changed if n not in made]
    parts = []
    if made:
        parts.append(pick(MADE).format(x=_names(made)) + ".")
    if changed:
        parts.append("I've " + ("also " if made else "") + pick(CHANGED).format(x=_names(changed)) + ".")
    if removed:
        parts.append(f"I've removed {_names(removed)}.")
    out = " ".join(parts)
    if not parts and read:
        out = pick(READING).format(x=_names(read, 2))
    for title, ok in done:
        out = (out + " " + title + ".").strip()
        if ok and playful and rng.random() < 0.5:
            out += " " + rng.choice(GOOD_NEWS)
    step = re.sub(r"[.…]+$", "", (now or "").strip())
    # The agent's own steps ("Editing script.js…"), not FireDev's ("Waiting to start", "Asking Codex…").
    if step and step.split(" ", 1)[0].endswith("ing") and not re.match(r"(Waiting|Asking|Reconnecting|Starting)\b", step):
        out = (out + " " + pick(NOW).format(x=step[0].lower() + step[1:])).strip()
    return clean_for_speech(out.replace("’", "'"))
