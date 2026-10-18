import time

from firedev import auth
from firedev.auth import DeviceStore

TV = "tv-claim-aaaaaaaaaaaaaaaaaaaaaa"
STRANGER = "stranger-claim-bbbbbbbbbbbbbbbbb"


def test_a_claim_keeps_its_code_across_reconnects(tmp_path):
    store = DeviceStore(tmp_path)
    first = store.pairing(TV)
    again = store.pairing(TV)  # the TV reconnected (heartbeat, Wi-Fi blip, app resumed)
    assert again["code"] == first["code"] and again["expires"] == first["expires"]
    assert first["expires"] - time.time() > auth.PAIRING_TTL - 5


def test_a_new_code_never_replaces_or_reveals_a_live_one(tmp_path, monkeypatch):
    store = DeviceStore(tmp_path)
    draws = iter([123456, 123456, 654321])
    monkeypatch.setattr(auth.secrets, "randbelow", lambda n: next(draws))
    assert store.pairing(TV)["code"] == "123456"
    assert store.pairing(STRANGER)["code"] == "654321"  # a clash is redrawn, never shared
    dev, key = store.approve_code("123456", "Living room")
    assert key == store.pairings[key]["key"] and dev["role"] == "tv"
    assert store.collect_token(STRANGER) is None
    assert store.authenticate(store.collect_token(TV)).name == "Living room"


def test_codes_expire_and_can_be_renewed(tmp_path, monkeypatch):
    store = DeviceStore(tmp_path)
    code = store.pairing(TV)["code"]
    now = time.time()
    monkeypatch.setattr(auth.time, "time", lambda: now + auth.PAIRING_TTL + 1)
    assert store.approve_code(code, "TV") is None  # expired
    fresh = store.renew_code(TV)
    assert fresh["expires"] > now + auth.PAIRING_TTL
    assert store.approve_code(fresh["code"], "TV") is not None


def test_renew_gives_a_different_code_and_kills_the_old_one(tmp_path, monkeypatch):
    store = DeviceStore(tmp_path)
    draws = iter([111111, 222222])
    monkeypatch.setattr(auth.secrets, "randbelow", lambda n: next(draws))
    assert store.pairing(TV)["code"] == "111111"
    assert store.renew_code(TV)["code"] == "222222"
    assert store.approve_code("111111", "TV") is None


def test_wrong_codes_are_refused(tmp_path):
    store = DeviceStore(tmp_path)
    store.pairing(TV)
    assert store.approve_code("000000x", "TV") is None
    assert store.approve_code("", "TV") is None


def test_token_waits_for_a_tv_that_dropped_off_until_it_connects(tmp_path):
    store = DeviceStore(tmp_path)
    store.approve_code(store.pairing(TV)["code"], "TV")
    token = store.collect_token(TV)
    assert store.collect_token(TV) == token  # still there after a lost "paired" message
    store.token_delivered(token)
    assert store.collect_token(TV) is None


def test_pending_codes_are_capped_but_abandoned_ones_make_room(tmp_path):
    store = DeviceStore(tmp_path)
    entries = [store.pairing(f"claim-{i:02d}-cccccccccccccccccccc") for i in range(auth.MAX_PENDING_CODES)]
    for e in entries:
        e["live"] = 1  # every TV is still looking at its code
    assert store.pairing("one-more-claim-dddddddddddddddd") is None
    entries[0]["live"] = 0  # that TV disconnected
    assert store.pairing("one-more-claim-dddddddddddddddd") is not None
    assert store.approve_code(entries[0]["code"], "TV") is None
