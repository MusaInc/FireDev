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
        self.codes: dict[str, dict] = {}

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
        return [{k: v for k, v in d.items() if k != "token_sha256"} for d in self.devices]

    # Pairing: an unpaired TV gets a 6-digit code; `firedev pair <code>` on the server approves it.
    def new_code(self, waiter) -> str:
        self.codes = {c: v for c, v in self.codes.items() if v["expires"] > time.time()}
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.codes[code] = {"expires": time.time() + 600, "waiter": waiter}
        return code

    def take_code(self, code: str):
        entry = self.codes.pop(code.replace(" ", ""), None)
        if not entry or entry["expires"] < time.time():
            return None
        return entry["waiter"]
