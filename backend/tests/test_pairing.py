import asyncio

from firedev import auth
from firedev.auth import DeviceStore


def _waiter(loop):
    return loop.create_future()


def test_a_new_code_never_replaces_a_live_one(tmp_path, monkeypatch):
    loop = asyncio.new_event_loop()
    store = DeviceStore(tmp_path)
    draws = iter([123456, 123456, 654321])
    monkeypatch.setattr(auth.secrets, "randbelow", lambda n: next(draws))
    tv, stranger = _waiter(loop), _waiter(loop)
    assert store.new_code(tv) == "123456"
    assert store.new_code(stranger) == "654321"
    assert store.take_code("123456") is tv
    loop.close()


def test_pending_codes_are_capped_and_freed_when_a_tv_gives_up(tmp_path):
    loop = asyncio.new_event_loop()
    store = DeviceStore(tmp_path)
    waiters = [_waiter(loop) for _ in range(auth.MAX_PENDING_CODES)]
    codes = [store.new_code(w) for w in waiters]
    assert None not in codes
    assert store.new_code(_waiter(loop)) is None
    waiters[0].cancel()  # that TV disconnected
    assert store.new_code(_waiter(loop)) is not None
    assert store.take_code(codes[0]) is None
    loop.close()
