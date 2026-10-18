"""Paired devices and their roles. Tokens are random 32-byte secrets; only their SHA-256 is stored.

Roles
  tv         the Fire TV app: everything, including approvals
  voice      a phone used as the microphone: voice commands and rejecting, never approving
  assistant  Alexa / MCP clients: commands and status; approving only if the server allows it (level 2 at most)
  admin      the local CLI (token in ~/.config/firedev/admin.token)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

ROLES = {"tv", "voice", "assistant", "admin"}


@dataclass
class Client:
    id: str
    name: str
    role: str


MAX_PENDING_CODES = 16
PAIRING_TTL = 600     # seconds a code stays valid; the TV says "10 minutes"
PAIRED_HOLD = 600     # how long a token waits for a TV that dropped off just as it was approved


def bound_code(fingerprint: str, claim: str, nonce: str) -> str:
    """The 6-digit pairing code for a TLS-bound pairing. The TV app computes the same from the certificate it pinned."""
    digest = hashlib.sha256(f"firedev-pair:{fingerprint.lower()}:{claim}:{nonce}".encode()).hexdigest()
    return f"{int(digest[:15], 16) % 1_000_000:06d}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.path = home / "devices.json"
        home.mkdir(parents=True, exist_ok=True)
        self.devices: list[dict] = []
        if self.path.exists():
            self.devices = json.loads(self.path.read_text()).get("devices", [])
        self.admin_path = home / "admin.token"
        if not self.admin_path.exists():
            self.admin_path.write_text(secrets.token_urlsafe(32) + "\n")
            os.chmod(self.admin_path, 0o600)
        self._admin = _hash(self.admin_path.read_text().strip())
        self.pairings: dict[str, dict] = {}  # sha256(claim) -> {code, expires, token, live, created}

    def machine_id(self) -> str:
        """A stable random id for this FireDev install, so a TV recognises the same machine at a new address."""
        p = self.home / "machine-id"
        if not p.exists():
            p.write_text(secrets.token_hex(8))
        return p.read_text().strip()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.devices}, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def authenticate(self, token: str | None) -> Client | None:
        if not token:
            return None
        h = _hash(token)
        if secrets.compare_digest(h, self._admin):
            return Client("admin", "FireDev CLI", "admin")
        for d in self.devices:
            if secrets.compare_digest(h, d["token_sha256"]):
                if d.get("expires") and d["expires"] < time.time():
                    return None
                d["last_seen"] = time.time()
                return Client(d["id"], d["name"], d["role"])
        return None

    def create(self, name: str, role: str, ttl: float | None = None) -> tuple[str, dict]:
        if role not in ROLES - {"admin"}:
            raise ValueError(f"role must be one of {sorted(ROLES - {'admin'})}")
        token = secrets.token_urlsafe(32)
        dev = {"id": "d" + secrets.token_hex(4), "name": name, "role": role, "token_sha256": _hash(token),
               "created": time.time(), "expires": time.time() + ttl if ttl else None}
        self.devices.append(dev)
        self._save()
        return token, dev

    def revoke(self, device_id: str) -> bool:
        before = len(self.devices)
        self.devices = [d for d in self.devices if d["id"] != device_id]
        self._save()
        return len(self.devices) != before

    def list(self) -> list[dict]:
        return [{k: v for k, v in d.items() if k not in ("token_sha256", "alexa_user")} for d in self.devices]

    # An Alexa account is linked by approving it on the TV; afterwards its (verified) Alexa user ID is its credential.
    def alexa_client(self, user_id: str | None) -> Client | None:
        if not user_id:
            return None
        h = _hash("alexa:" + user_id)
        for d in self.devices:
            if d.get("alexa_user") and secrets.compare_digest(h, d["alexa_user"]):
                d["last_seen"] = time.time()
                return Client(d["id"], d["name"], d["role"])
        return None

    def link_alexa(self, user_id: str) -> dict:
        _, dev = self.create("Alexa", "assistant")  # the token is never handed out: Alexa authenticates by signature
        dev["alexa_user"] = _hash("alexa:" + user_id)
        self._save()
        return dev

    # Pairing: an unpaired TV gets a 6-digit code; `firedev pair <code>` on the server approves it.
    # The TV holds a random claim (kept across reconnects), and the code belongs to that claim, so a dropped
    # connection or a heartbeat reconnect gets the same code back until it expires. Anyone who can reach the
    # service can ask for a code, so a code is only ever handed to its own claim (a stranger can't take over the
    # one a TV is showing) and only a few can wait at once.
    def _prune(self) -> None:
        now = time.time()
        self.pairings = {k: v for k, v in self.pairings.items()
                         if (v["token"] and v["paired_at"] + PAIRED_HOLD > now) or (not v["token"] and v["expires"] > now)}

    def pairing(self, claim: str, bind: str | None = None) -> dict | None:
        """The live pairing for this claim, or a new one. None when too many TVs are waiting.

        bind: the fingerprint of the TLS certificate this TV connected through (LAN mode). The code is then derived
        from that fingerprint, the TV's claim and a fresh nonce, and the TV computes it itself from the certificate it
        saw. Behind a man in the middle the TV shows a different code than the one this machine accepts, so the
        middle can't get paired."""
        self._prune()
        key = _hash("claim:" + claim)
        entry = self.pairings.get(key)
        if entry and not entry["token"] and entry.get("bind") == bind:
            return entry
        pending = [k for k, v in self.pairings.items() if not v["token"]]
        if len(pending) >= MAX_PENDING_CODES:
            idle = sorted((self.pairings[k]["created"], k) for k in pending if self.pairings[k]["live"] == 0)
            if not idle:
                return None
            del self.pairings[idle[0][1]]  # the oldest code nobody is looking at
        taken = {v["code"] for k, v in self.pairings.items() if k != key}
        while True:
            nonce = secrets.token_hex(8) if bind else None
            code = bound_code(bind, claim, nonce) if bind else f"{secrets.randbelow(1_000_000):06d}"
            if code not in taken:
                break
        entry = {"key": key, "code": code, "created": time.time(), "expires": time.time() + PAIRING_TTL,
                 "token": None, "paired_at": 0.0, "live": 0, "bind": bind, "nonce": nonce}
        self.pairings[key] = entry
        return entry

    def renew_code(self, claim: str, bind: str | None = None) -> dict | None:
        """Replaces this claim's code with a fresh one (the TV asked, usually after expiry)."""
        key = _hash("claim:" + claim)
        old = self.pairings.get(key)
        if old and not old["token"]:
            del self.pairings[key]
        entry = self.pairing(claim, bind)
        if entry and old and not old["token"]:
            entry["live"] = old["live"]
        return entry

    def drop_pairing(self, claim: str) -> None:
        entry = self.pairings.get(_hash("claim:" + claim))
        if entry and not entry["token"]:
            del self.pairings[entry["key"]]

    def approve_code(self, code: str, name: str) -> tuple[dict, str] | None:
        """Pairs the TV showing `code`: creates its device and holds the token for that TV. Returns (device, key)."""
        self._prune()
        code = code.replace(" ", "")
        entry = next((v for v in self.pairings.values() if v["code"] == code and not v["token"]), None)
        if not entry or entry["expires"] <= time.time():
            return None
        token, dev = self.create(name, "tv")
        entry["token"], entry["paired_at"] = token, time.time()
        return dev, entry["key"]

    def collect_token(self, claim: str) -> str | None:
        """The token for an approved claim. Kept until the TV connects with it (see token_delivered), so a TV that
        drops off between approval and receiving it still gets it on its next connection."""
        self._prune()
        entry = self.pairings.get(_hash("claim:" + claim))
        return entry["token"] if entry and entry["token"] else None

    def token_delivered(self, token: str) -> None:
        self.pairings = {k: v for k, v in self.pairings.items() if v["token"] != token}
