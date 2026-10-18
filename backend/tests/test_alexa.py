"""The Alexa skill: request verification (with a throwaway CA standing in for Amazon's), linking on the TV,
answers inside Alexa's time limit, and what Alexa says about progress and results."""

import asyncio
import base64
import datetime as dt
import json

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from firedev import alexa
from firedev import alexa_verify as av
from firedev import config as cfg
from firedev.auth import Client, DeviceStore
from firedev.service import CommandError, FireDev

SKILL = "amzn1.ask.skill.00000000-test"
CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert-99.pem"
NOW = dt.datetime(2026, 10, 18, 12, 0, 0, tzinfo=dt.timezone.utc)
TV = Client("tv1", "Living room", "tv")


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _ca(cn="Test Root"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(_name(cn)).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - dt.timedelta(days=365)).not_valid_after(NOW + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def _leaf(ca_key, ca_cert, san="echo-api.amazon.com", days=(-30, 300)):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (x509.CertificateBuilder().subject_name(_name(san)).issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW + dt.timedelta(days=days[0])).not_valid_after(NOW + dt.timedelta(days=days[1]))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return key, cert


@pytest.fixture(scope="module")
def pki():
    ca_key, ca = _ca()
    key, leaf = _leaf(ca_key, ca)
    return {"ca": ca, "ca_key": ca_key, "key": key, "leaf": leaf}


def _pem(*certs):
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


def _verifier(pki, chain=None, skill=SKILL, now=NOW):
    served = chain if chain is not None else _pem(pki["leaf"])
    return av.AlexaVerifier(skill, roots=[pki["ca"]], fetch=lambda url: served, now=lambda: now)


def _envelope(rtype="LaunchRequest", intent=None, user="amzn1.ask.account.USER", skill=SKILL, ts=NOW, **extra):
    req = {"type": rtype, "requestId": "amzn1.echo-api.request.1", "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "locale": "en-GB"}
    if intent:
        req["intent"] = intent
    system = {"application": {"applicationId": skill}, "user": {"userId": user}, "device": {"deviceId": "dev1"},
              "apiEndpoint": "https://api.eu.amazonalexa.com", "apiAccessToken": "tok"}
    return {"version": "1.0", "session": {"application": {"applicationId": skill}, "user": {"userId": user}},
            "context": {"System": system}, "request": {**req, **extra}}


def _signed(pki, body: dict, key=None, url=CERT_URL):
    raw = json.dumps(body).encode()
    sig = (key or pki["key"]).sign(raw, padding.PKCS1v15(), hashes.SHA256())
    return {"signaturecertchainurl": url, "signature-256": base64.b64encode(sig).decode()}, raw


# --- verification --------------------------------------------------------------------------------------------

async def test_a_correctly_signed_fresh_request_passes(pki):
    headers, raw = _signed(pki, _envelope())
    req = await _verifier(pki).verify(headers, raw)
    assert req["request"]["type"] == "LaunchRequest"


@pytest.mark.parametrize("url", [
    "https://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "HTTPS://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com:443/echo.api/../echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com//echo.api//echo-api-cert.pem",
])
def test_certificate_urls_alexa_uses_are_accepted(url):
    assert av.normalize_cert_url(url) == "https://s3.amazonaws.com/echo.api/echo-api-cert.pem"


@pytest.mark.parametrize("url", [
    "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
    "https://notamazon.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com.evil.com/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
    "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",
    "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/../invalid.path/echo-api-cert.pem",
    "https://s3.amazonaws.com/echo.api/%2e%2e/invalid.path/echo-api-cert.pem",
    "",
])
def test_other_certificate_urls_are_refused(url):
    with pytest.raises(av.VerificationError):
        av.normalize_cert_url(url)


async def test_a_changed_body_fails(pki):
    headers, raw = _signed(pki, _envelope())
    with pytest.raises(av.VerificationError, match="signature"):
        await _verifier(pki).verify(headers, raw.replace(b"LaunchRequest", b"IntentRequest"))


async def test_a_signature_from_another_key_fails(pki):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    headers, raw = _signed(pki, _envelope(), key=other)
    with pytest.raises(av.VerificationError, match="signature"):
        await _verifier(pki).verify(headers, raw)


@pytest.mark.parametrize("skew", [151, -151, 3600])
async def test_stale_or_future_requests_fail(pki, skew):
    headers, raw = _signed(pki, _envelope(ts=NOW - dt.timedelta(seconds=skew)))
    with pytest.raises(av.VerificationError, match="150 seconds"):
        await _verifier(pki).verify(headers, raw)


async def test_requests_within_150_seconds_pass(pki):
    headers, raw = _signed(pki, _envelope(ts=NOW - dt.timedelta(seconds=149)))
    await _verifier(pki).verify(headers, raw)


async def test_another_skills_request_fails(pki):
    headers, raw = _signed(pki, _envelope(skill="amzn1.ask.skill.someone-else"))
    with pytest.raises(av.VerificationError, match="different skill"):
        await _verifier(pki).verify(headers, raw)


async def test_nothing_passes_until_a_skill_id_is_configured(pki):
    headers, raw = _signed(pki, _envelope())
    with pytest.raises(av.VerificationError, match="skill ID"):
        await _verifier(pki, skill=None).verify(headers, raw)


async def test_missing_headers_or_only_the_old_sha1_signature_fail(pki):
    headers, raw = _signed(pki, _envelope())
    with pytest.raises(av.VerificationError):
        await _verifier(pki).verify({}, raw)
    with pytest.raises(av.VerificationError):
        await _verifier(pki).verify({"signaturecertchainurl": CERT_URL, "signature": headers["signature-256"]}, raw)
    with pytest.raises(av.VerificationError):
        await _verifier(pki).verify({**headers, "signature-256": "not base64!"}, raw)


async def test_a_certificate_for_another_name_fails(pki):
    key, leaf = _leaf(pki["ca_key"], pki["ca"], san="example.com")
    headers, raw = _signed(pki, _envelope(), key=key)
    with pytest.raises(av.VerificationError, match="certificate rejected"):
        await _verifier(pki, chain=_pem(leaf)).verify(headers, raw)


async def test_an_expired_or_not_yet_valid_certificate_fails(pki):
    for days in ((-400, -1), (1, 300)):
        key, leaf = _leaf(pki["ca_key"], pki["ca"], days=days)
        headers, raw = _signed(pki, _envelope(), key=key)
        with pytest.raises(av.VerificationError, match="certificate rejected"):
            await _verifier(pki, chain=_pem(leaf)).verify(headers, raw)


async def test_a_certificate_from_an_untrusted_ca_fails(pki):
    rogue_key, rogue = _ca("Rogue Root")
    key, leaf = _leaf(rogue_key, rogue)
    headers, raw = _signed(pki, _envelope(), key=key)
    with pytest.raises(av.VerificationError, match="certificate rejected"):
        await _verifier(pki, chain=_pem(leaf, rogue)).verify(headers, raw)


async def test_a_cached_certificate_is_rechecked_and_refused_once_it_expires(pki):
    clock = {"now": NOW}
    key, leaf = _leaf(pki["ca_key"], pki["ca"], days=(-30, 1))
    v = av.AlexaVerifier(SKILL, roots=[pki["ca"]], fetch=lambda url: _pem(leaf), now=lambda: clock["now"])
    headers, raw = _signed(pki, _envelope(), key=key)
    await v.verify(headers, raw)
    clock["now"] = NOW + dt.timedelta(days=2)
    headers, raw = _signed(pki, _envelope(ts=clock["now"]), key=key)
    with pytest.raises(av.VerificationError, match="certificate rejected"):
        await v.verify(headers, raw)


# --- the skill -----------------------------------------------------------------------------------------------

@pytest.fixture
def fd(tmp_path):
    raw = {"server": {"data_dir": str(tmp_path / "data"), "alexa_skill_id": SKILL},
           "machines": {"m": {"host": "example.invalid", "user": "u", "auth": "agent"}},
           "projects": [{"id": "app", "name": "My Android App", "machine": "m", "path": "~/app",
                         "actions": {"deploy_preview": {"run": "./deploy.sh", "kind": "deploy", "level": 2}}}]}
    return FireDev(cfg.parse(raw), DeviceStore(tmp_path / "home"))


def _say(r):
    return r["response"]["outputSpeech"]["text"]


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_an_unknown_alexa_account_is_linked_only_by_holding_ok_on_the_tv(fd):
    skill = alexa.AlexaSkill(fd, fd.devices)
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "RunTestsIntent"}))
    code = skill.pending_links["amzn1.ask.account.USER"]
    assert " ".join(code) in _say(r) and "TV" in _say(r)
    await _settle()
    (a,) = fd.approvals.values()
    assert a.kind == "link" and a.level == 3 and a.source == "alexa" and code[0] in a.command

    # Asking again repeats the same code; nothing else works until the TV approves.
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "ProgressIntent"}))
    assert " ".join(code) in _say(r) and len(fd.approvals) == 1
    alexa_device = Client("x", "Alexa", "assistant")
    with pytest.raises(CommandError):
        fd.decide(a.id, True, alexa_device)
    with pytest.raises(CommandError):
        fd.decide(a.id, True, TV)  # a plain press isn't enough
    fd.decide(a.id, True, TV, held=True)
    await _settle()
    assert skill.pending_links == {}
    assert fd.devices.alexa_client("amzn1.ask.account.USER").role == "assistant"
    assert fd.devices.alexa_client("amzn1.ask.account.SOMEONE-ELSE") is None

    r = await skill.handle_signed(_envelope("LaunchRequest"))
    assert _say(r).startswith("FireDev is ready.") and r["response"]["shouldEndSession"] is False


async def test_a_rejected_link_stays_unlinked_and_can_be_asked_again(fd):
    skill = alexa.AlexaSkill(fd, fd.devices)
    await skill.handle_signed(_envelope())
    await _settle()
    (a,) = fd.approvals.values()
    fd.decide(a.id, False, TV)
    await _settle()
    assert fd.devices.alexa_client("amzn1.ask.account.USER") is None and skill.pending_links == {}
    await skill.handle_signed(_envelope())
    assert len(skill.pending_links) == 1


async def test_link_requests_are_capped(fd):
    skill = alexa.AlexaSkill(fd, fd.devices)
    for i in range(alexa.MAX_PENDING_LINKS):
        await skill.handle_signed(_envelope(user=f"u{i}"))
    r = await skill.handle_signed(_envelope(user="one-too-many"))
    assert "already waiting" in _say(r) and len(skill.pending_links) == alexa.MAX_PENDING_LINKS


async def test_help_and_stop_work_before_linking(fd):
    skill = alexa.AlexaSkill(fd, fd.devices)
    assert _say(await skill.handle_signed(_envelope("IntentRequest", {"name": "AMAZON.StopIntent"}))) == "Okay."
    assert "ask fire dev" in _say(await skill.handle_signed(_envelope("IntentRequest", {"name": "AMAZON.HelpIntent"})))
    assert skill.pending_links == {}


async def _linked(fd, monkeypatch, voice):
    fd.devices.link_alexa("amzn1.ask.account.USER")
    monkeypatch.setattr(fd, "voice", voice)
    return alexa.AlexaSkill(fd, fd.devices, post=lambda url, payload, token: posted.append((url, payload, token)))


posted: list = []


async def test_a_coding_task_is_started_and_answered_at_once(fd, monkeypatch):
    seen = []

    async def voice(text, client, source, project=None):
        seen.append((text, client.role, source))
        return {"ok": True, "intent": {"name": "send_agent_task"}, "message": "Asking Codex."}

    skill = await _linked(fd, monkeypatch, voice)
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "FixTaskIntent", "slots": {"request": {"name": "request", "value": "the failing tests"}}}))
    assert seen == [("Fix the failing tests", "assistant", "alexa")]
    assert "Asking Codex." in _say(r) and "how it's going" in _say(r) and r["response"]["shouldEndSession"] is True
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "AgentTaskIntent", "slots": {"request": {"name": "request", "value": "add a dark mode"}}}))
    assert seen[-1][0] == "Ask the agent to add a dark mode"


async def test_slow_work_gets_a_progressive_response_and_an_answer_inside_the_limit(fd, monkeypatch):
    monkeypatch.setattr(alexa, "FIRST_WAIT", 0.05)
    monkeypatch.setattr(alexa, "LIMIT", 0.1)
    done = asyncio.Event()

    async def voice(text, client, source, project=None):
        await asyncio.sleep(0.4)  # e.g. connecting to the machine
        done.set()
        return {"ok": True, "intent": {"name": "open_project"}, "message": "Connected."}

    posted.clear()
    skill = await _linked(fd, monkeypatch, voice)
    loop = asyncio.get_running_loop()
    start = loop.time()
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "OpenProjectIntent", "slots": {"project": {"name": "project", "value": "pulse"}}}))
    assert loop.time() - start < 0.3
    assert "watch the TV" in _say(r)
    await asyncio.sleep(0.05)
    url, payload, token = posted[0]
    assert url == "https://api.eu.amazonalexa.com/v1/directives" and token == "tok"
    assert payload["header"]["requestId"] == "amzn1.echo-api.request.1" and payload["directive"]["type"] == "VoicePlayer.Speak"
    await asyncio.wait_for(done.wait(), 1)  # the work wasn't cancelled


async def test_progressive_responses_only_go_to_alexa(fd, monkeypatch):
    posted.clear()
    skill = await _linked(fd, monkeypatch, None)
    body = _envelope()
    body["context"]["System"]["apiEndpoint"] = "https://evil.example.com"
    await skill._progressive(body, "One moment.")
    assert posted == []


async def test_approving_by_voice_points_to_the_tv(fd, monkeypatch):
    async def voice(text, client, source, project=None):
        return {"ok": True, "intent": {"name": "approve_action"}, "message": "Press OK on the remote to approve.", "navigate": "approval"}

    skill = await _linked(fd, monkeypatch, voice)
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "ApproveIntent"}))
    assert "Approvals stay on the TV" in _say(r)


async def test_progress_and_result_are_short_and_shown_on_the_tv(fd):
    fd.devices.link_alexa("amzn1.ask.account.USER")
    skill = alexa.AlexaSkill(fd, fd.devices)
    q = fd.bus.subscribe(100)
    r = await skill.handle_signed(_envelope("IntentRequest", {"name": "ProgressIntent"}))
    assert "Nothing is running" in _say(r)
    types = [q.get_nowait().type for _ in range(q.qsize())]
    assert types == ["voice_command", "voice_reply"]


def test_progress_speech():
    now = __import__("time").time()
    snap = {"name": "My Android App", "connection": "connected", "agent": {"name": "Codex"}, "queue": [], "approvals": [1],
            "task": {"title": "Fix the failing tests", "started": now - 125, "status_text": "Running the unit tests"}}
    s = alexa.progress_speech(snap)
    assert s.startswith("Codex is working on fix the failing tests, for 2 minutes so far.")
    assert "Running the unit tests." in s and "approval on the TV" in s


def test_result_speech_reads_the_summary_without_code_or_paths():
    snap = {"task": None, "changes": {"files": ["a", "b"]}, "tests": {"total": 12, "passed": 12},
            "last_task": {"title": "Fix the failing tests", "status": "completed",
                          "summary": "Fixed **Streaks.kt**: `currentStreak` skipped today.\n```kotlin\nval x = 1\n```\nSee app/src/main/java/dev/Streaks.kt and https://example.com/x"}}
    s = alexa.result_speech(snap)
    assert "```" not in s and "`" not in s and "**" not in s and "app/src" not in s and "https" not in s
    assert "Fixed Streaks.kt: currentStreak skipped today." in s
    assert "2 files changed. 12 of 12 tests passed. The details are on your TV." in s


def test_long_speech_is_clipped_at_a_sentence():
    s = alexa._clip("One sentence here. " * 100, 120)
    assert len(s) <= 120 and s.endswith(".")


# --- over HTTP -----------------------------------------------------------------------------------------------

async def test_the_endpoint_refuses_unsigned_requests_and_answers_signed_ones(fd, pki, monkeypatch, tmp_path):
    from firedev import app as app_module
    monkeypatch.setattr(app_module, "AlexaVerifier", lambda skill_id: _verifier(pki, skill=skill_id, now=NOW))
    application = app_module.create_app(fd.config, fd.devices)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://t") as c:
        r = await c.post("/alexa", content=json.dumps(_envelope()))
        assert r.status_code == 400
        headers, raw = _signed(pki, _envelope())
        r = await c.post("/alexa", content=raw, headers={"SignatureCertChainUrl": headers["signaturecertchainurl"],
                                                          "Signature-256": headers["signature-256"]})
        assert r.status_code == 200 and "look at your TV" in r.json()["response"]["outputSpeech"]["text"]
        r = await c.post("/alexa", content=raw, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401


async def test_every_intent_in_the_interaction_model_is_handled(fd, monkeypatch):
    from pathlib import Path
    model = json.loads((Path(__file__).resolve().parents[2] / "alexa" / "interaction-model.json").read_text())
    lm = model["interactionModel"]["languageModel"]
    assert lm["invocationName"] == "fire dev"

    async def voice(text, client, source, project=None):
        return {"ok": True, "intent": {"name": "x"}, "message": "Fine."}

    skill = await _linked(fd, monkeypatch, voice)
    for i in lm["intents"]:
        slots = {s["name"]: {"name": s["name"], "value": "something"} for s in i.get("slots", [])}
        r = await skill.handle_signed(_envelope("IntentRequest", {"name": i["name"], "slots": slots}))
        assert "doesn't know that one" not in _say(r), i["name"]
