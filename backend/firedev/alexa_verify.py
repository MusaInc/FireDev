"""Checks that a request to /alexa really comes from Alexa, for the FireDev skill, and is fresh.

Follows Amazon's rules for custom skills hosted as a web service ("Host a Custom Skill as a Web Service"):
the SignatureCertChainUrl must be https://s3.amazonaws.com/echo.api/… on port 443 (after removing dot segments
and duplicate slashes); the certificate must be valid now, name echo-api.amazon.com and chain to a trusted root;
Signature-256 must be the RSA signature of the SHA-256 hash of the exact request body; the request timestamp must
be within 150 seconds. On top of that FireDev only answers its own skill (the configured skill ID).
Any failure means HTTP 400 and the request is never looked at.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import datetime as dt
import json
import posixpath
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import verification

SIGNING_NAME = "echo-api.amazon.com"
MAX_SKEW_SECONDS = 150
MAX_CHAIN_BYTES = 64 * 1024
ROOT_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/cert.pem")


class VerificationError(Exception):
    pass


def normalize_cert_url(url: str) -> str:
    """Returns the normalized certificate URL, or raises if it isn't one Alexa uses."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme.lower() != "https":
        raise VerificationError("certificate URL is not https")
    if (parts.hostname or "").lower() != "s3.amazonaws.com":
        raise VerificationError("certificate URL is not on s3.amazonaws.com")
    try:
        port = parts.port
    except ValueError as e:
        raise VerificationError("certificate URL has a bad port") from e
    if port not in (None, 443):
        raise VerificationError("certificate URL is not on port 443")
    path = posixpath.normpath(urllib.parse.unquote(parts.path) or "/")
    path = "/" + path.lstrip("/")  # normpath keeps a leading "//"
    if not path.startswith("/echo.api/"):
        raise VerificationError("certificate URL path is not under /echo.api/")
    return f"https://s3.amazonaws.com{path}"


def _default_roots() -> list[x509.Certificate]:
    for p in ROOT_BUNDLES:
        if Path(p).exists():
            return x509.load_pem_x509_certificates(Path(p).read_bytes())
    import certifi  # comes with httpx
    return x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes())


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=4) as r:  # noqa: S310 (URL checked by normalize_cert_url)
        data = r.read(MAX_CHAIN_BYTES + 1)
    if len(data) > MAX_CHAIN_BYTES:
        raise VerificationError("certificate chain is too large")
    return data


class AlexaVerifier:
    def __init__(self, skill_id: str | None, *, roots: list[x509.Certificate] | None = None,
                 fetch: Callable[[str], bytes] = _fetch, now: Callable[[], dt.datetime] | None = None) -> None:
        self.skill_id = skill_id
        self._roots = roots
        self._fetch = fetch
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._chains: dict[str, list[x509.Certificate]] = {}

    def _store(self) -> verification.Store:
        if self._roots is None:
            self._roots = _default_roots()
        return verification.Store(self._roots)

    async def _signing_cert(self, url: str) -> x509.Certificate:
        chain = self._chains.get(url)
        if chain is None:
            try:
                pem = await asyncio.to_thread(self._fetch, url)
                chain = x509.load_pem_x509_certificates(pem)
            except VerificationError:
                raise
            except Exception as e:  # noqa: BLE001 - any download or parse problem is a failed verification
                raise VerificationError(f"couldn't load the signing certificate: {e}") from e
        leaf = chain[0]
        try:
            # Validity dates, the echo-api.amazon.com name and the chain to a trusted root, checked every time so a
            # cached certificate stops working the moment it expires.
            verifier = verification.PolicyBuilder().store(self._store()).time(self._now()) \
                .build_server_verifier(x509.DNSName(SIGNING_NAME))
            verifier.verify(leaf, chain[1:])
        except Exception as e:  # noqa: BLE001
            self._chains.pop(url, None)
            raise VerificationError(f"signing certificate rejected: {e}") from e
        self._chains[url] = chain
        return leaf

    async def verify(self, headers, body: bytes) -> dict:
        """Returns the parsed request if it passes every check, else raises VerificationError."""
        if not self.skill_id:
            raise VerificationError("no Alexa skill ID is configured (server.alexa_skill_id)")
        url = headers.get("signaturecertchainurl")
        sig = headers.get("signature-256")
        if not url or not sig:
            raise VerificationError("missing Signature-256 or SignatureCertChainUrl")
        leaf = await self._signing_cert(normalize_cert_url(url))
        try:
            leaf.public_key().verify(base64.b64decode(sig, validate=True), body, padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, binascii.Error, ValueError) as e:
            raise VerificationError("signature doesn't match the request body") from e

        try:
            req = json.loads(body)
            ts = dt.datetime.fromisoformat(req["request"]["timestamp"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise VerificationError("request has no valid timestamp") from e
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        if abs((self._now() - ts).total_seconds()) > MAX_SKEW_SECONDS:
            raise VerificationError("request timestamp is more than 150 seconds off")

        system = (req.get("context") or {}).get("System") or {}
        app_id = ((system.get("application") or {}).get("applicationId")
                  or ((req.get("session") or {}).get("application") or {}).get("applicationId"))
        if app_id != self.skill_id:
            raise VerificationError("request is for a different skill")
        return req
