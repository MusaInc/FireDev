"""Voice and assistant utterances → FireDev intents.

This is the safety layer between speech and the machine: an utterance becomes one of a fixed set of intents,
never a raw shell command. Free-form development requests become a task for the coding agent, which works in
its sandbox and has to ask FireDev (and so the TV) for anything risky.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Intent names are also the command names used by the WebSocket API, MCP tools and the Alexa skill.
INTENTS = {
    "open_project", "go_home", "send_agent_task", "run_action", "show_changes", "show_activity", "get_status",
    "approve_action", "reject_action", "cancel_task", "continue_task", "undo_changes", "help",
    "ui_edit", "ui_back", "navigate", "speech_stop", "conversation_end", "preview",
}


@dataclass
class Intent:
    name: str
    title: str
    args: dict = field(default_factory=dict)
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "args": self.args, "confidence": self.confidence}


def _norm(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"^(hey |ok |okay )?(alexa|fire ?dev|firedev)[,.]?\s+", "", t)
    # Talking to the agent by name: "Codex, open my FireDev project".
    t = re.sub(r"^(hey |ok |okay )?(codex|claude code|claude)[,.]?\s+(?=\S)", "", t)
    t = re.sub(r"^(ask|tell) fire ?dev (to )?", "", t)
    t = re.sub(r"^(please|can you|could you|would you|i want you to|let's|lets)\s+", "", t)
    t = re.sub(r"[?!.,]+$", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    return t


_STOP = re.compile(r"^(stop|stop it|stop that|cancel|cancel (it|that|the task)|abort|halt|never mind|nevermind)( (now|please))?$")
_CONTINUE = re.compile(r"^(continue|carry on|keep going|go on|resume|proceed)( (please|with (it|that)))?$")
_APPROVE = re.compile(r"^(approve|approve (it|that|this)|yes,? approve( it)?|go ahead|do it|confirm|allow it)$")
_REJECT = re.compile(r"^(reject|reject (it|that|this)|deny( it)?|don't do (it|that)|no,? (don't|reject|stop)( it)?|decline)$")
_UNDO = re.compile(r"\b(undo|revert|roll back|rollback)\b.*\b(change|changes|that|edit|edits|it|last)\b|^(undo|revert)( it| that)?$")
_DIFF = re.compile(r"^(show|see|review|open)\b.*\b(changed|changes|diff|difference|edits)\b|^(the )?(diff|changes)$")
_ACTIVITY = re.compile(r"\b(show|open)\b.*\b(activity|history|timeline|what (you|it|codex|claude) did)\b")
_STATUS = re.compile(r"^(status|what'?s (the )?status|how'?s it going|what are you doing|what is (codex|claude|the agent) doing|where are we|progress)$")
_HOME = re.compile(r"^(go )?(home|back to (projects|home)|show (my )?projects|switch projects?)$")
_HELP = re.compile(r"^(help|what can (i|you) (say|do))$")
_SPEECH_STOP = re.compile(r"^(stop (talking|speaking|reading)|be quiet|quiet|shush|shut up|silence|that'?s enough|enough)( please)?$")
_CONVO_END = re.compile(r"^(stop listening|goodbye|good bye|bye|that'?s all( for now)?|we'?re done|i'?m done|thanks,? that'?s all)$")
_TERMINAL = re.compile(r"^((go )?back to|show( me)?|open|switch to) (the )?(terminal|console|workspace)$|^terminal$")
_UI_BACK = re.compile(r"^(go back|back|previous screen|show the previous screen|the last screen|undo that screen)$")
_THAT = re.compile(r"^(?:(?:that|this|it)(?: (?P<k1>\w+))?|the (?P<k2>chart|graph|diagram|table|card|panel|list|terminal|code|diff|screen))$")
_KINDS = {"chart": "chart", "graph": "graph", "diagram": "graph", "table": "table", "card": "card", "panel": "card", "list": "list",
          "terminal": "terminal", "code": "code", "diff": "diff"}


def _target(what: str) -> tuple[str | None, str | None]:
    """'that chart' -> (None, 'chart'): the focused component, of that kind. 'the codex agent' -> ('codex agent', None)."""
    m = _THAT.match(what)
    if m:
        return None, _KINDS.get(m.group("k1") or m.group("k2") or "")
    return re.sub(r"^(the|my) ", "", what), None
_RESIZE = re.compile(r"^(make|zoom) (?P<what>.+?) (?P<dir>bigger|larger|smaller|wider|narrower)$|^(?P<zin>zoom in|enlarge)( on)?( (that|this|it))?$|^(?P<zout>zoom out|shrink)( (that|this|it))?$")
_EXPAND = re.compile(r"^(?P<op>expand|open up|show details (?:of|for)|collapse|close up|hide details (?:of|for)) (?P<what>.+)$")
_UI_MODIFY = re.compile(r"^(only show|just show|show (that|this|it|them) as|turn (that|this|it) into|make (that|this|it) (a|an) |hide|filter|sort|"
                        r"group|add (a|an|the|another|more)|remove|compare (that|this|it|them) (with|to)|switch (that|this|it) to|"
                        r"change (that|this|it)|highlight|put .+ (first|at the top)|show more|show less|show fewer)")
_PRESENT = re.compile(r"^(show me|show my|show (?:the|all|our)|display|visuali[sz]e|chart|graph|draw|plot|diagram|map out|"
                      r"give me an? (overview|dashboard|summary)|dashboard|explain (this|the|our|my) architecture|"
                      r"how (are|is) my .+ (doing|performing)|how are my apps|compare (these|the|my) .+)")
_EXPLAIN_THAT = re.compile(r"^(explain|what does|what is|what's|tell me (more )?about) (that|this|it)( mean)?$")
_QUESTION = re.compile(r"^(what|why|how|where|when|which|who|is|are|does|do|did|can|could|should|would|explain|tell me|describe)\b")
_OPEN = re.compile(r"^(open|switch to|go to|connect to|load|start)\s+(?:up\s+)?(?:my |the )?(?P<name>.+?)(?: project| repo| repository| app)?$")
_WHY_BUILD = re.compile(r"\bwhy\b.*\b(build|compile|compilation)\b.*\b(fail|failing|failed|broken|break|red)|\bwhat'?s wrong with the build\b")
_WHY_TESTS = re.compile(r"\bwhy\b.*\btests?\b.*\b(fail|failing|failed|broken|red)")
_RUN_TESTS = re.compile(r"^(run|rerun|re-run|start|check) (the |all |all the |my )?(unit )?tests?( again)?$")
_BUILD = re.compile(r"^(build|rebuild|compile|assemble|make)( (it|the|my|a))?( (android|debug|release))?( (app|apk|build|project))?( again)?$")
_DEPLOY = re.compile(r"^(deploy|ship|release|publish|push)( (it|the|a|my))?( (latest|new))?( (preview|build|app|staging))*( (to|on) (the )?(preview|staging|dev(elopment)?)( (server|environment))?)?$")
_LOGS = re.compile(r"^(check|show|open|read|tail|look at)( me)? (the )?(server |app |preview |error )?logs?$")
_AGENT_PREFIX = re.compile(r"^(ask|tell|get|have) (codex|claude|claude code|the agent|the ai|it) (to )?")
_FIX = re.compile(r"\b(fix|repair|solve|resolve|sort out|make .* pass|get .* working)\b")
_AND_TESTS = re.compile(r"\b(run|rerun|re-run|check)\b.*\btests?\b")
_AND_BUILD = re.compile(r"\b(re)?build\b")


def _match_project(name: str, projects: list[dict]) -> dict | None:
    name = name.strip().lower()
    words = set(re.findall(r"[a-z0-9]+", name)) - {"my", "the", "project", "app", "repo", "a"}
    best, best_score = None, 0.0
    for p in projects:
        names = [p["id"], p["name"].lower(), *p.get("aliases", [])]
        if name in names:
            return p
        pw = set()
        for n in names:
            pw |= set(re.findall(r"[a-z0-9]+", n.lower()))
        pw -= {"my", "the", "project", "app", "repo", "a"}
        if not words or not pw:
            continue
        score = len(words & pw) / len(words)
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 0.5 else None


def parse(text: str, projects: list[dict], context: dict | None = None) -> Intent:
    """context: {'project': id or None, 'agent': 'Codex', 'actions': {...}, 'last_build_failed': bool,
    'last_tests_failed': bool, 'pending_approval': bool}"""
    ctx = context or {}
    agent = ctx.get("agent") or "the agent"
    actions = ctx.get("actions") or {}
    t = _norm(text)

    if not t:
        return Intent("help", "I didn't catch that", confidence=0.0)
    if _SPEECH_STOP.match(t):
        return Intent("speech_stop", "Stop speaking")
    if _CONVO_END.match(t):
        return Intent("conversation_end", "Stop listening")
    if _STOP.match(t):
        return Intent("cancel_task", "Stop the current task")
    if _APPROVE.match(t) or _REJECT.match(t):
        if not ctx.get("pending_approval"):
            return Intent("help", "Nothing is waiting for your approval", confidence=0.5)
        if _APPROVE.match(t):
            return Intent("approve_action", "Approve on the TV")
        return Intent("reject_action", "Reject the request")
    if _CONTINUE.match(t):
        return Intent("continue_task", "Continue")
    if _HELP.match(t):
        return Intent("help", "What you can say")
    if _HOME.match(t):
        return Intent("go_home", "Show projects")
    if ctx.get("project"):
        from . import preview_intents
        pv = preview_intents.parse(t, text, ctx)
        if pv:
            return pv
    if _TERMINAL.match(t):
        return Intent("navigate", "Back to the workspace", {"to": "terminal" if "terminal" in t or "console" in t else "workspace"})
    surface = ctx.get("has_surface")
    if surface and _UI_BACK.match(t):
        return Intent("ui_back", "Previous screen")
    if surface:
        m = _RESIZE.match(t)
        if m:
            what = (m.group("what") or "that").strip()
            bigger = bool(m.group("zin")) or m.group("dir") in ("bigger", "larger", "wider")
            target, kind = _target(what)
            return Intent("ui_edit", "Make it bigger" if bigger else "Make it smaller",
                          {"op": "bigger" if bigger else "smaller", "target": target, "kind": kind, "task": text.strip()})
        m = _EXPAND.match(t)
        if m:
            what = m.group("what").strip()
            op = "expand" if m.group("op") in ("expand", "open up") or m.group("op").startswith("show") else "collapse"
            target, kind = _target(what)
            return Intent("ui_edit", f"{op.capitalize()} {what}", {"op": op, "target": target, "kind": kind, "task": text.strip()})
        if _UI_MODIFY.match(t):
            return Intent("ui_edit", _title(t), {"task": text.strip()})
    if _UNDO.search(t):
        return Intent("undo_changes", "Undo the last change")
    if _STATUS.match(t):
        return Intent("get_status", "Project status")
    if _ACTIVITY.search(t):
        return Intent("show_activity", "Show activity")
    if _DIFF.search(t) and not _FIX.search(t) and not re.search(r"\b(dashboard|overview|chart|graph|diagram|visuali[sz]e)\b", t):
        return Intent("show_changes", "Show what changed")

    m = _OPEN.match(t)
    if m and not _FIX.search(t):
        p = _match_project(m["name"], projects)
        if p:
            return Intent("open_project", f"Open {p['name']}", {"project": p["id"]})
        if not ctx.get("project"):
            return Intent("help", f"I couldn't find a project called “{m['name']}”", confidence=0.2)

    if t in ("show me", "show me that", "show it", "show that", "put that on screen", "put it on the screen", "put it on screen"):
        return Intent("send_agent_task", "Show it on screen", {"task": "Show what we were just talking about on the TV.",
                                                                "mode": "present", "lane": "ui", "any_project": not ctx.get("project")})
    if _PRESENT.match(t) and not _DIFF.search(t) and not _ACTIVITY.search(t):
        return Intent("send_agent_task", _title(_AGENT_PREFIX.sub("", t)), {"task": text.strip(), "mode": "present", "lane": "ui",
                                                                             "any_project": not ctx.get("project")})
    if not ctx.get("project"):
        # Outside a project the only useful thing is choosing one.
        p = _match_project(t, projects)
        if p:
            return Intent("open_project", f"Open {p['name']}", {"project": p["id"]})
        if len(projects) == 1:
            return Intent("open_project", f"Open {projects[0]['name']}", {"project": projects[0]["id"], "then": text})
        return Intent("help", "Choose a project first", confidence=0.3)

    def action_for(kind: str) -> str | None:
        for aid, a in actions.items():
            if a.get("kind") == kind or aid == kind:
                return aid
        return None

    if _RUN_TESTS.match(t) and action_for("tests"):
        return Intent("run_action", "Run the tests", {"action": action_for("tests")})
    if _BUILD.match(t) and action_for("build"):
        return Intent("run_action", actions[action_for("build")].get("title") or "Build the app", {"action": action_for("build")})
    if _DEPLOY.match(t):
        aid = next((a for a in actions if "preview" in a and actions[a].get("kind") == "deploy"), None) or action_for("deploy")
        if aid:
            return Intent("run_action", actions[aid].get("title") or "Deploy", {"action": aid})
    if _LOGS.match(t) and action_for("logs"):
        return Intent("run_action", "Check the logs", {"action": action_for("logs")})

    # Everything else is a development task for the coding agent.
    request = _AGENT_PREFIX.sub("", t).strip()
    if _WHY_BUILD.search(t):
        return Intent("send_agent_task", "Find out why the build fails", {
            "task": "Why is the build failing? Run the build with `firedev run build`, find the root cause of each error, "
                    "and explain it briefly. Don't change any files yet.",
            "mode": "investigate"})
    if _WHY_TESTS.search(t):
        return Intent("send_agent_task", "Find out why tests fail", {
            "task": "Why are the tests failing? Run them with `firedev run test`, find the root cause, and explain it briefly. "
                    "Don't change any files yet.",
            "mode": "investigate"})
    if _FIX.search(t):
        what = "the failing build" if ctx.get("last_build_failed") else "the failing tests" if ctx.get("last_tests_failed") else None
        steps = [f"{text.strip()}"]
        if what and re.search(r"\bfix (it|this|that|them)\b", t):
            steps.append(f"(“it” means {what}.)")
        if _AND_TESTS.search(t):
            steps.append("When the fix is in, run the tests with `firedev run test` and make sure they pass.")
        elif _AND_BUILD.search(t):
            steps.append("When the fix is in, build with `firedev run build`.")
        else:
            steps.append("Check your fix with `firedev run build` or `firedev run test`.")
        title = f"Fix {what}" if what and re.search(r"\bfix (it|this|that|them)\b", t) else _title(request)
        if _AND_TESTS.search(t):
            title += " and run tests"
        return Intent("send_agent_task", title, {"task": " ".join(steps), "mode": "change"})
    if _EXPLAIN_THAT.match(t):
        return Intent("send_agent_task", "Explain that", {"task": text.strip(), "mode": "answer"})
    if _QUESTION.match(request):
        return Intent("send_agent_task", _title(request), {"task": text.strip(), "mode": "answer"}, confidence=0.8)
    return Intent("send_agent_task", _title(request), {"task": text.strip(), "mode": "change"}, confidence=0.8)


def _title(request: str) -> str:
    r = request.strip().rstrip(".?!")
    r = re.sub(r"^(please|can you|could you)\s+", "", r, flags=re.I)
    if not r:
        return "New task"
    r = r[0].upper() + r[1:]
    return r if len(r) <= 60 else r[:57].rsplit(" ", 1)[0] + "…"


SUGGESTIONS = {
    "home": ["Open my Android project", "What's the status?"],
    "idle": ["Why is the build failing?", "Run the tests", "Build the app", "Show me what changed", "Deploy the preview", "Check the logs"],
    "failed_build": ["Why is the build failing?", "Fix it and run the tests", "Show me what changed"],
    "working": ["What are you doing?", "Stop", "Show activity"],
    "changes": ["Run the tests", "Build the app", "Show me what changed", "Undo that change"],
    "built": ["Deploy the preview", "Run the tests", "Show me what changed"],
}
