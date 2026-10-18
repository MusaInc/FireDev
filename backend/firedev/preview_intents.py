"""Spoken commands for the live preview: show it, fullscreen, back to Codex, mobile, zoom, reload, move around the page,
start or stop the dev server. Returns None for anything else, which then goes through the normal intents.

While the TV shows the preview, a request to change something visible ("make the header smaller") is a task for the
coding agent about the site, not an edit of a generated FireDev screen.
"""

from __future__ import annotations

import re

from .intents import Intent, _title

_THING = r"(the |my |our |a |this )?(live |web )?(preview|website|web ?site|site|web ?page|landing page|page|web ?app|demo)"
_SHOW = re.compile(rf"^(show|open|display|bring up|pull up|put up|let me see|can i see|see|view)( me)? {_THING}( (running|live))?"
                   r"( (on|on the) (tv|screen|television))?( (again|now))?$|^(live )?preview( (it|that))?$|^preview the (site|page|app)$")
_SHOW_IT = re.compile(r"^((show|let) me (what it looks like|how it looks|it running|the result)|what does it look like( now)?|how does it look( now)?|"
                      r"(show|open|bring up) (me )?(the|my) (app|result)( running)?|let me see( it)?|show me it)$")
_FULL = re.compile(r"^((show|make|put|open) (it|that|the preview|the site|the page|the website|the app) )?(in )?(full ?screen|full size|maximi[sz]ed?)( mode)?$|"
                   r"^(go|switch to|show) full ?screen$|^(show|open) (the )?(preview|site|website|page|app) (in )?full ?screen$|"
                   r"^maximi[sz]e( (it|the preview|the site))?$|^(make|show) (it|the preview) (fill|take up) the (whole )?screen$")
_SPLIT = re.compile(r"^(split ?screen|side by side|split (view|it)|exit full ?screen|leave full ?screen|"
                    r"(show|put) (codex|claude|the agent|it) (and|next to|beside|alongside) the (preview|site|page|website)|"
                    r"(go back to |back to |show( me)? )?(the )?split ?(screen|view))$")
_CLOSE = re.compile(r"^((go )?back to|show( me)?|return to|switch (back )?to|take me back to|open) (codex|claude|claude code|the agent|"
                    r"the conversation|the chat|fire ?dev)$|^(close|hide|exit|dismiss|leave) (the )?(live )?(preview|site|website|page)$|"
                    r"^stop (showing|previewing)( (the|it))?( (preview|site|website))?$")
_MOBILE = re.compile(r"\b(mobile|phone|iphone|android phone|smartphone) (version|view|layout|size|preview|width|screen|mode)\b|"
                     r"^(show|switch to|view|use|try)( it| me)?( (on|as|like))?( a| the)? (phone|mobile|iphone)( screen| view)?$|"
                     r"^(what does it look like|how does it look) on (a |my )?(phone|mobile)$")
_TABLET = re.compile(r"\b(tablet|ipad) (version|view|layout|size|preview|width|mode)\b|^(show|switch to|view)( it)?( (on|as))?( a)? (tablet|ipad)$")
_DESKTOP = re.compile(r"\b(desktop|laptop|computer|full|wide) (version|view|layout|size|width|mode)\b|"
                      r"^(show|switch to|view|go back to)( it)?( (on|as))?( a| the)? (desktop|laptop|computer)$")
_ZOOM = re.compile(r"^zoom (?P<dir>in|out)( (a bit|more|on (the )?(preview|page|site)))?$|^(make|zoom) (the )?(page|text|site|content) "
                   r"(?P<dir2>bigger|larger|smaller)$|^(?P<reset>reset (the )?zoom|actual size|normal size|zoom to (fit|100( percent)?))$")
_SIZE = re.compile(r"^make (the )?(preview|site|page) (panel |pane |side )?(?P<dir>bigger|larger|wider|smaller|narrower)$|"
                   r"^(give|make) (the )?(?P<who>preview|site|codex|claude|the agent|agent) (more|less) (room|space)$")
_RELOAD = re.compile(r"^(reload|refresh)( the)?( (preview|page|site|website|it|app))?( (again|please))?$")
_BACK = re.compile(r"^(go )?back( a page| one page)?$|^previous page$")
_FORWARD = re.compile(r"^(go )?forward( a page)?$|^next page$")
_SCROLL = re.compile(r"^(scroll|page|go) (?P<dir>down|up)( a (bit|little|lot))?( more)?$|^(scroll|go|jump) to the (?P<edge>top|bottom)( of the page)?$")
_GOTO = re.compile(r"^(go to|open|navigate to|take me to|show( me)?) the (?P<what>[a-z0-9 '-]{2,40}?) (page|section|tab)$|^(go to|open|navigate to) (?P<path>/[\w/.-]*)$")
_CLICK = re.compile(r"^(click|press|tap|select|hit)( on)? (the )?(?P<what>[a-z0-9 '-]{2,40}?)( button| link| tab| menu)?$")
_START = re.compile(r"^(start|run|launch|boot|spin up|fire up|serve)( up)? (the )?(dev(elopment)? server|preview( server)?|web server|local server|"
                    r"server|site|website|web app|app in the browser)$")
_RESTART = re.compile(r"^restart (the )?(dev(elopment)? server|preview( server)?|web server|server)$")
_STOP = re.compile(r"^(stop|kill|shut down|close)( down)? (the )?(dev(elopment)? server|preview server|web server|local server)$")
_INSTALL = re.compile(r"^(install|npm install|set up) (the )?(dependencies|deps|packages|node modules|npm packages)( and start( it)?)?$")
_STATUS = re.compile(r"^is the (preview|dev server|site|website|server) (running|up|working|live)$|^(preview|dev server) status$")
_SCREENSHOTS = re.compile(r"^show (me )?(the )?(screenshots?|snapshots?|screenshot tests?)$")
# A change to what's on screen, said while looking at the site.
_SITE_EDIT = re.compile(r"^(make|change|move|add|remove|delete|put|set|turn|use|give|center|centre|align|replace|swap|rename|increase|"
                        r"decrease|reduce|shrink|enlarge|darken|lighten|hide|show more|show less|redesign|restyle|tweak|fix|update|"
                        r"improve)\b")


def parse(t: str, text: str, ctx: dict) -> Intent | None:
    """t: the normalised utterance (intents._norm)."""
    status = ctx.get("preview")  # None when the project has no preview information yet
    visible = bool(ctx.get("preview_visible"))
    live = status in ("running", "starting")

    def p(op: str, title: str, **args) -> Intent:
        return Intent("preview", title, {"op": op, **args})

    if _CLOSE.match(t) and (visible or "preview" in t or "site" in t or "codex" in t or "claude" in t or "agent" in t):
        return p("close", "Back to the agent")
    if _FULL.match(t):
        return p("show", "Fullscreen preview", layout="full")
    if _SPLIT.match(t):
        return p("show", "Preview beside the agent", layout="split")
    if _SHOW.match(t) or (_SHOW_IT.match(t) and (live or status == "idle" or visible)):
        return p("show", "Show the live preview")
    if _SCREENSHOTS.match(t) and status == "native":
        return p("show", "Show the screenshots", screenshots=True)
    if _START.match(t) and ("server" in t or "preview" in t or ctx.get("preview_kind") in ("web", "static")):
        return p("start", "Start the dev server")
    if _RESTART.match(t):
        return p("restart", "Restart the dev server")
    if _STOP.match(t):
        return p("stop", "Stop the dev server")
    if _INSTALL.match(t) and (ctx.get("preview_needs_install") or "dependencies" in t):
        return p("install", "Install dependencies and start")
    if _STATUS.match(t):
        return p("status", "Preview status")
    if not (visible or (live and re.search(r"\b(preview|site|page|website)\b", t))):
        return None
    # Only while the preview is on screen (or named): the controls of the preview itself.
    if _MOBILE.search(t):
        return p("viewport", "Mobile view", viewport="mobile")
    if _TABLET.search(t):
        return p("viewport", "Tablet view", viewport="tablet")
    if _DESKTOP.search(t):
        return p("viewport", "Desktop view", viewport="desktop")
    m = _ZOOM.match(t)
    if m:
        if m.group("reset"):
            return p("zoom", "Reset zoom", zoom="reset")
        d = m.group("dir") or ("in" if m.group("dir2") in ("bigger", "larger") else "out")
        return p("zoom", "Zoom " + d, zoom=d)
    m = _SIZE.match(t)
    if m:
        who = m.group("who")
        if who:
            more = " more " in f" {t} "
            bigger = (who in ("preview", "site")) == more
        else:
            bigger = m.group("dir") in ("bigger", "larger", "wider")
        return p("size", "Bigger preview" if bigger else "Smaller preview", size="bigger" if bigger else "smaller")
    if _RELOAD.match(t):
        return p("nav", "Reload the preview", nav="reload")
    if visible and _BACK.match(t):
        return p("nav", "Previous page", nav="back")
    if visible and _FORWARD.match(t):
        return p("nav", "Next page", nav="forward")
    m = _SCROLL.match(t)
    if m and visible:
        if m.group("edge"):
            return p("nav", f"Scroll to the {m.group('edge')}", nav=m.group("edge"))
        return p("nav", f"Scroll {m.group('dir')}", nav="scroll", dir=m.group("dir"))
    m = _GOTO.match(t)
    if m and visible:
        if m.group("path"):
            return p("nav", f"Open {m.group('path')}", nav="go", path=m.group("path"))
        what = re.sub(r"^(the|my) ", "", m.group("what")).strip()
        if what not in ("preview", "agent", "codex"):
            return p("nav", f"Open the {what} page", nav="click", text=what, path="/" + re.sub(r"[^a-z0-9]+", "-", what).strip("-"))
    m = _CLICK.match(t)
    if m and visible:
        return p("nav", f"Click {m.group('what')}", nav="click", text=m.group("what"))
    if visible and _SITE_EDIT.match(t):
        return Intent("send_agent_task", _title(text.strip()), {"task": text.strip(), "mode": "change", "preview": True})
    return None
