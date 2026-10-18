"""The FireDev Alexa skill: Alexa Skills Kit request JSON in, ASK response JSON out.

Real requests from Alexa arrive signed and are checked by alexa_verify before they get here; the phone page can
also drive this handler with a FireDev token (the "simulated Alexa" path). Each Alexa intent becomes the
canonical utterance FireDev's voice layer already understands, so Alexa goes through exactly the same intent and
safety path as the phone microphone. The interaction model is in alexa/interaction-model.json.

- Linking: an Alexa account FireDev doesn't know gets a 4-digit code and a link request on the TV, approved by
  holding OK on the remote. Nothing else works until then.
- Alexa allows about 8 seconds per answer, but agent tasks take minutes. A task is started and answered at once
  ("on it, the TV shows progress"); the user asks for progress or the result whenever they like. Anything slower
  than 1.5 s gets a progressive "one moment", and nothing is allowed to run past 6.5 s: the work carries on and
  the TV shows it.
- Approvals never happen by voice: the TV remote approves.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import urllib.parse
import urllib.request

from .auth import Client, DeviceStore
from .service import CommandError, FireDev

# Alexa intent → what the user would have said to FireDev directly.
CANONICAL = {
    "WhyBuildFailingIntent": "Why is the build failing?",
    "FixAndTestIntent": "Fix it and run the tests",
    "RunTestsIntent": "Run the tests",
    "BuildAppIntent": "Build the app",
    "DeployPreviewIntent": "Deploy the preview",
    "CheckLogsIntent": "Check the logs",
    "ShowChangesIntent": "Show me what changed",
    "UndoIntent": "Undo that change",
    "StopTaskIntent": "Stop",
    "ContinueTaskIntent": "Continue",
    "ApproveIntent": "Approve",
    "RejectIntent": "Reject",
}
# Free-form tasks: the carrier verb Alexa matched is put back in front of the slot value.
TASK_VERBS = {"AgentTaskIntent": "", "FixTaskIntent": "Fix ", "AddTaskIntent": "Add ", "ChangeTaskIntent": "Change "}

HELP = ("You can say: ask fire dev why the build is failing, ask fire dev to fix the failing tests, "
        "ask fire dev how it's going, or ask fire dev for the result. Details show on your TV.")
FIRST_WAIT = 1.5  # before saying "one moment"
LIMIT = 5.0       # more after that; Alexa gives up at about 8 seconds
MAX_SPEECH = 600
MAX_PENDING_LINKS = 2


def _speak(text: str, end: bool = True, reprompt: str | None = None) -> dict:
    resp = {"outputSpeech": {"type": "PlainText", "text": _clip(text, MAX_SPEECH)}, "shouldEndSession": end}
    if reprompt:
        resp["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return {"version": "1.0", "response": resp}


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + "…"


def speakable(text: str | None) -> str:
    """Agent summaries are written for screens: drop code, markdown and paths so they read well aloud."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"(?<![\w.])(?:[\w.-]+/)+([\w.-]+)", r"\1", t)  # app/src/main/Foo.kt → Foo.kt
    t = re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", t, flags=re.M)
    t = re.sub(r"[#*_>|]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _ago(ts: float | None) -> str:
    if not ts:
        return ""
    s = max(0, int(time.time() - ts))
    if s < 60:
        return f"{s} seconds"
    m = s // 60
    return f"{m} minute{'s' if m != 1 else ''}"


def progress_speech(snap: dict) -> str:
    parts = []
    agent = (snap.get("agent") or {}).get("name") or "The agent"
    t = snap.get("task")
    last = snap.get("last_task")
    if t:
        parts.append(f"{agent} is working on {t['title'].rstrip('.').lower()}, for {_ago(t.get('started') or t.get('created'))} so far.")
        if t.get("status_text"):
            parts.append(t["status_text"].rstrip(".") + ".")
        if snap.get("queue"):
            parts.append(f"{len(snap['queue'])} more waiting.")
    elif last and last.get("status") == "completed":
        parts.append(f"{last['title'].rstrip('.')} is done. Ask for the result to hear what happened.")
    elif last and last.get("status") == "failed":
        parts.append(f"{last['title'].rstrip('.')} failed. {_clip(speakable(last.get('summary') or last.get('error')), 200)}".strip())
    elif last and last.get("status") == "cancelled":
        parts.append(f"{last['title'].rstrip('.')} was stopped.")
    else:
        parts.append(f"{snap['name']} is {'ready' if snap.get('connection') == 'connected' else snap.get('connection', 'not connected')}. Nothing is running.")
    if snap.get("approvals"):
        parts.append("Something is waiting for your approval on the TV.")
    return " ".join(parts)


def result_speech(snap: dict) -> str:
    t = snap.get("task")
    last = snap.get("last_task")
    if t:
        return f"Still working on {t['title'].rstrip('.').lower()}. {t.get('status_text', '')}".strip()
    if not last:
        return "Nothing has run yet. Try: ask fire dev why the build is failing."
    title = last["title"].rstrip(".")
    if last.get("status") == "failed":
        return f"{title} failed. {_clip(speakable(last.get('summary') or last.get('error')), 300)}. The details are on your TV.".replace("..", ".").strip()
    if last.get("status") == "cancelled":
        return f"{title} was stopped before it finished."
    parts = [f"{title}: {_clip(speakable(last.get('summary')), 320)}" if last.get("summary") else f"{title} finished."]
    files = (snap.get("changes") or {}).get("files") or []
    if files:
        parts.append(f"{len(files)} file{'s' if len(files) != 1 else ''} changed.")
    tests = snap.get("tests") or {}
    if tests.get("total"):
        parts.append(f"{tests.get('passed', 0)} of {tests['total']} tests passed.")
    parts.append("The details are on your TV.")
    return " ".join(p for p in parts if p)


class AlexaSkill:
    def __init__(self, service: FireDev, devices: DeviceStore, post=None) -> None:
        self.service = service
        self.devices = devices
        self._post = post or _post_json
        self.pending_links: dict[str, str] = {}  # Alexa user ID → the code being shown on the TV
        self._background: set[asyncio.Task] = set()

    # --- entry points ---------------------------------------------------------------------------------------

    async def handle_signed(self, body: dict) -> dict:
        """A request Alexa signed (already verified). Its user ID says who it is."""
        system = (body.get("context") or {}).get("System") or {}
        user_id = (system.get("user") or {}).get("userId") or ((body.get("session") or {}).get("user") or {}).get("userId")
        return await self._respond(body, self.devices.alexa_client(user_id), user_id, signed=True)

    async def handle_simulated(self, client: Client, body: dict) -> dict:
        """The phone page's "simulated Alexa" (or a test) with a FireDev token: already trusted, never linked."""
        return await self._respond(body, client, None, signed=False)

    # --- the conversation ------------------------------------------------------------------------------------

    async def _respond(self, body: dict, client: Client | None, user_id: str | None, signed: bool) -> dict:
        req = body.get("request") or {}
        rtype = req.get("type")
        if rtype == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        intent = req.get("intent") or {}
        name = intent.get("name", "") if rtype == "IntentRequest" else ""
        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
            return _speak("Okay.")
        if name == "AMAZON.HelpIntent":
            return _speak(HELP, end=False, reprompt="What should I do?")
        if client is None:
            return self._link(user_id)
        if rtype == "LaunchRequest":
            status = self._snapshot_speech(progress_speech)
            return _speak(f"FireDev is ready. {status} What should I do?", end=False,
                          reprompt="You can say: why is the build failing?")
        if rtype != "IntentRequest":
            return _speak("Sorry, I can't handle that yet.")
        if name in ("ProgressIntent", "StatusIntent"):
            return _speak(self._tell("How's it going?", "get_status", progress_speech))
        if name == "ResultIntent":
            return _speak(self._tell("What was the result?", "get_result", result_speech))
        if name == "OpenProjectIntent":
            text = f"Open {_slot(intent, 'project') or 'my project'}"
        elif name in TASK_VERBS:
            request = _slot(intent, "request")
            if not request:
                return _speak("What should the agent do?", end=False, reprompt="For example: fix the failing tests.")
            text = TASK_VERBS[name] + request if TASK_VERBS[name] else f"Ask the agent to {request}"
        elif name in CANONICAL:
            text = CANONICAL[name]
        elif name == "AMAZON.FallbackIntent":
            return _speak("Sorry, FireDev didn't catch that. " + HELP)
        else:
            return _speak("Sorry, FireDev doesn't know that one yet.")

        work = asyncio.ensure_future(self.service.voice(text, client, source="alexa"))
        result = await self._within_limit(work, body if signed else None)
        if result is None:
            return _speak("I'm on it. It's taking a moment, so watch the TV, and ask me how it's going in a bit.")
        return _speak(self._phrase(result))

    def _phrase(self, result: dict) -> str:
        msg = (result.get("message") or "Done.").strip()
        intent = (result.get("intent") or {}).get("name")
        if intent == "approve_action" and result.get("ok"):
            return "Approvals stay on the TV. Press OK on your remote to approve."
        if result.get("ok") and intent == "send_agent_task":
            return f"{msg} I'll show the progress on the TV. Ask me how it's going, or for the result, whenever you like."
        if result.get("ok") and intent == "run_action" and "approval" not in msg.lower():
            return f"{msg} Ask me for the result when it's done."
        return msg

    def _snapshot_speech(self, fn) -> str:
        try:
            return fn(self.service.session(None).snapshot())
        except CommandError as e:
            return str(e)

    def _tell(self, asked: str, intent: str, fn) -> str:
        """Progress and result are read straight from the project's state, and shown on the TV like any voice command."""
        pid = self.service.focus_project
        text = self._snapshot_speech(fn)
        self.service.bus.emit("voice_command", pid, None, text=asked, source="alexa",
                              intent={"name": intent, "title": asked, "args": {}, "confidence": 1.0})
        self.service.bus.emit("voice_reply", pid, None, text=asked, intent=intent, reply=text, ok=True)
        return text

    async def _within_limit(self, work: asyncio.Future, signed_body: dict | None) -> dict | None:
        try:
            return await asyncio.wait_for(asyncio.shield(work), FIRST_WAIT)
        except asyncio.TimeoutError:
            pass
        if signed_body:
            self._spawn(self._progressive(signed_body, "One moment."))
        try:
            return await asyncio.wait_for(asyncio.shield(work), LIMIT)
        except asyncio.TimeoutError:
            return None  # it keeps running; the TV shows it

    # --- linking ---------------------------------------------------------------------------------------------

    def _link(self, user_id: str | None) -> dict:
        if not user_id:
            return _speak("Sorry, FireDev couldn't tell which Alexa account this is.")
        code = self.pending_links.get(user_id)
        if code is None:
            if len(self.pending_links) >= MAX_PENDING_LINKS:
                return _speak("FireDev is already waiting for a link to be approved on the TV. Try again in a minute.")
            code = f"{secrets.randbelow(10_000):04d}"
            self.pending_links[user_id] = code
            self._spawn(self._link_flow(user_id, code))
        spaced = " ".join(code)
        return _speak(f"To link this Alexa account to FireDev, look at your TV. If it shows the code {spaced}, "
                      f"hold OK on your remote to approve. Then ask me again.")

    async def _link_flow(self, user_id: str, code: str) -> None:
        try:
            session = self.service.session(None) if self.service.focus_project or len(self.service.sessions) == 1 \
                else next(iter(self.service.sessions.values()))
            ok, _ = await self.service.request_approval(
                session, task=None, command=f"Code {' '.join(code)}", title="Link Alexa to FireDev",
                reason="Alexa will be able to start tasks, stop them and read progress. Approvals stay on this TV.",
                risk="Only approve if Alexa just told you this code", level=3, source="alexa", kind="link")
            if ok and self.devices.alexa_client(user_id) is None:
                self.devices.link_alexa(user_id)
        finally:
            self.pending_links.pop(user_id, None)

    # --- progressive responses -------------------------------------------------------------------------------

    async def _progressive(self, body: dict, text: str) -> None:
        system = (body.get("context") or {}).get("System") or {}
        endpoint, token = system.get("apiEndpoint"), system.get("apiAccessToken")
        request_id = (body.get("request") or {}).get("requestId")
        host = urllib.parse.urlsplit(endpoint or "").hostname or ""
        if not (token and request_id and endpoint and endpoint.startswith("https://")
                and (host.endswith(".amazonalexa.com") or host == "api.fe.amazon.com")):
            return
        payload = {"header": {"requestId": request_id},
                   "directive": {"type": "VoicePlayer.Speak", "speech": f"<speak>{text}</speak>"}}
        try:
            await asyncio.to_thread(self._post, endpoint.rstrip("/") + "/v1/directives", payload, token)
        except Exception:  # noqa: BLE001 - a missed "one moment" never matters
            pass

    def _spawn(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._background.add(t)
        t.add_done_callback(self._background.discard)


def _slot(intent: dict, name: str) -> str | None:
    slot = (intent.get("slots") or {}).get(name) or {}
    return slot.get("value")


def _post_json(url: str, payload: dict, token: str) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3):  # noqa: S310 (host checked by the caller)
        pass
