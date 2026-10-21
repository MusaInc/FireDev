"""The TV's pairing conversation over the real WebSocket endpoint: heartbeat, reconnects, expiry, renewal, approval."""

import pytest
from starlette.testclient import TestClient

from firedev import auth
from firedev import config as cfg
from firedev.app import create_app
from firedev.auth import DeviceStore

CLAIM = "a" * 48


@pytest.fixture
def setup(tmp_path):
    devices = DeviceStore(tmp_path / "home")
    c = cfg.parse({"server": {"data_dir": str(tmp_path / "data")}})
    admin = (tmp_path / "home" / "admin.token").read_text().strip()
    with TestClient(create_app(c, devices)) as client:
        yield client, devices, {"Authorization": "Bearer " + admin}


def test_heartbeat_is_answered_and_reconnects_keep_the_code(setup):
    client, _, _ = setup
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        first = ws.receive_json()
        assert first["type"] == "pairing" and len(first["code"]) == 6 and 590 <= first["expires_in"] <= 600
        for _ in range(3):
            ws.send_json({"op": "ping"})
            assert ws.receive_json() == {"type": "pong"}
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        again = ws.receive_json()
        assert again["code"] == first["code"] and again["expires_in"] <= first["expires_in"]


def test_a_different_tv_gets_a_different_code(setup):
    client, _, _ = setup
    with client.websocket_connect("/ws?pair=" + CLAIM) as a, client.websocket_connect("/ws?pair=" + "b" * 48) as b:
        assert a.receive_json()["code"] != b.receive_json()["code"]


def test_an_old_tv_without_a_claim_still_pairs(setup):
    client, _, admin = setup
    with client.websocket_connect("/ws") as ws:
        code = ws.receive_json()["code"]
        assert client.post("/api/pair", json={"code": code, "name": "Old TV"}, headers=admin).status_code == 200
        assert ws.receive_json()["type"] == "paired"


def test_wrong_and_unauthorised_pairing_is_refused(setup):
    client, _, admin = setup
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        code = ws.receive_json()["code"]
        wrong = "000000" if code != "000000" else "111111"
        r = client.post("/api/pair", json={"code": wrong}, headers=admin)
        assert r.status_code == 404 and "No TV is showing that code" in r.json()["detail"]
        assert client.post("/api/pair", json={"code": code}).status_code == 401
        assert client.post("/api/pair", json={"code": code}, headers={"Authorization": "Bearer nope"}).status_code == 401
        ws.send_json({"op": "ping"})
        assert ws.receive_json() == {"type": "pong"}  # still waiting, same code


def test_expiry_is_announced_and_a_new_code_can_be_requested(setup, monkeypatch):
    client, _, admin = setup
    monkeypatch.setattr(auth, "PAIRING_TTL", 1)
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        old = ws.receive_json()["code"]
        assert ws.receive_json() == {"type": "pairing_expired"}
        assert client.post("/api/pair", json={"code": old}, headers=admin).status_code == 404
        monkeypatch.setattr(auth, "PAIRING_TTL", 600)
        ws.send_json({"op": "new_code"})
        fresh = ws.receive_json()
        assert fresh["type"] == "pairing" and fresh["expires_in"] > 590
        assert client.post("/api/pair", json={"code": fresh["code"]}, headers=admin).status_code == 200
        assert ws.receive_json()["type"] == "paired"


def test_approval_while_the_tv_is_away_is_delivered_when_it_returns(setup):
    client, devices, admin = setup
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        code = ws.receive_json()["code"]
    assert client.post("/api/pair", json={"code": code, "name": "Sony"}, headers=admin).status_code == 200
    with client.websocket_connect("/ws?pair=" + CLAIM) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "paired"
    with client.websocket_connect("/ws?token=" + msg["token"]) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["client"]["name"] == "Sony"
    assert devices.collect_token(CLAIM) is None  # handed over, gone


def test_the_tv_knows_when_a_phone_microphone_is_connected(setup):
    client, devices, _ = setup
    tv_token, _ = devices.create("Sony TV", "tv")
    phone_token, _ = devices.create("Phone microphone", "voice")
    with client.websocket_connect("/ws?token=" + tv_token) as tv:
        hello = tv.receive_json()
        assert hello["type"] == "hello" and hello["home"]["voice_links"] == []
        with client.websocket_connect("/ws?token=" + phone_token) as phone:
            assert phone.receive_json()["type"] == "hello"
            ev = tv.receive_json()
            assert ev["type"] == "voice_link" and ev["data"]["links"] == ["Phone microphone"]
        ev = tv.receive_json()
        assert ev["type"] == "voice_link" and ev["data"]["links"] == []


def _next(ws, type_):
    """The next message of one type (the phone and TV also get each other's link events and the keys' broadcast)."""
    while True:
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg


def test_the_phone_remote_sends_keys_to_the_tv_and_lets_go_when_it_drops(setup):
    client, devices, _ = setup
    tv_token, _ = devices.create("Sony TV", "tv")
    phone_token, _ = devices.create("Phone microphone", "voice")
    with client.websocket_connect("/ws?token=" + tv_token) as tv:
        assert tv.receive_json()["home"]["tvs"] == 1
        with client.websocket_connect("/ws?token=" + phone_token) as phone:
            assert phone.receive_json()["home"]["tvs"] == 1
            phone.send_json({"op": "key", "id": 1, "key": "down"})
            assert _next(phone, "key_ack") == {"type": "key_ack", "id": 1, "ok": True, "tvs": 1}
            ev = _next(tv, "remote_key")
            assert ev["data"] == {"action": "down", "down": True, "source": "phone"}
            phone.send_json({"op": "key", "id": 2, "key": "power"})
            assert _next(phone, "key_ack")["ok"] is False  # only FireDev's own navigation keys
            phone.send_json({"op": "key", "id": 3, "key": "select", "down": True})
            assert _next(phone, "key_ack")["ok"] is True
            ev = _next(tv, "remote_key")
            assert ev["data"] == {"action": "select", "down": True, "source": "phone"}
        # The phone dropped mid-hold: the TV is told to let go, so a held Approve can't complete without it.
        ev = _next(tv, "remote_key")
        assert ev["data"] == {"action": "select", "down": False, "source": "phone"}
        assert _next(tv, "voice_link")["data"]["links"] == []


def test_only_a_phone_sends_remote_keys(setup):
    client, devices, _ = setup
    tv_token, _ = devices.create("Sony TV", "tv")
    with client.websocket_connect("/ws?token=" + tv_token) as tv:
        tv.receive_json()
        tv.send_json({"op": "key", "id": 1, "key": "select"})
        assert tv.receive_json() == {"type": "key_ack", "id": 1, "ok": False, "error": "Only a paired phone can send remote keys"}
