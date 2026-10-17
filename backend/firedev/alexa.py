"""Alexa custom-skill endpoint (simulated during the hackathon).

Accepts Alexa Skills Kit request JSON and answers with ASK response JSON. Each Alexa intent becomes the
canonical utterance FireDev's voice layer already understands, so Alexa goes through exactly the same
intent and safety path as the phone microphone. The interaction model is in alexa/interaction-model.json.

Not done here (needed for a published skill): verifying Alexa's request signature and certificate chain,
and account linking. Until then the endpoint is protected by an assistant token like any other client.
"""

from __future__ import annotations

from .auth import Client
from .service import FireDev

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
    "StatusIntent": "What's the status?",
    "StopTaskIntent": "Stop",
    "ContinueTaskIntent": "Continue",
    "AMAZON.HelpIntent": "Help",
}


def _speak(text: str, end: bool = True, reprompt: str | None = None) -> dict:
    resp = {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}
    if reprompt:
        resp["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return {"version": "1.0", "response": resp}


def _slot(intent: dict, name: str) -> str | None:
    slot = (intent.get("slots") or {}).get(name) or {}
    return slot.get("value")


async def handle(service: FireDev, client: Client, body: dict) -> dict:
    req = body.get("request") or {}
    rtype = req.get("type")
    if rtype == "LaunchRequest":
        return _speak("FireDev is ready. What should I do?", end=False, reprompt="You can say: why is the build failing?")
    if rtype == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    if rtype != "IntentRequest":
        return _speak("Sorry, I can't handle that yet.")
    intent = req.get("intent") or {}
    name = intent.get("name", "")
    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
        return _speak("Okay.")
    if name == "OpenProjectIntent":
        text = f"Open {_slot(intent, 'project') or 'my project'}"
    elif name == "AgentTaskIntent":
        request = _slot(intent, "request")
        if not request:
            return _speak("What should the agent do?", end=False, reprompt="For example: fix the failing tests.")
        text = f"Ask the agent to {request}"
    elif name == "ApproveIntent":
        text = "Approve"
    elif name == "RejectIntent":
        text = "Reject"
    elif name in CANONICAL:
        text = CANONICAL[name]
    else:
        return _speak("Sorry, FireDev doesn't know that one yet.")
    result = await service.voice(text, client, source="alexa")
    return _speak(result.get("message") or "Done.")
