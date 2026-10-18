"""What FireDev says out loud: turning an agent's written answer into a short spoken reply.

Agents are asked to open their final message with one or two sentences meant for speech. This keeps only those,
cleans away anything that sounds wrong read aloud (markdown, code, long paths, URLs), and keeps it short enough
for a TV conversation.
"""

from __future__ import annotations

import posixpath
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
