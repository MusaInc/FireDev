"""Starts a throwaway FireDev service for the browser tests: a fixture project on localhost over real SSH and tmux,
the scripted stand-in agent (backend/tests/fixtures/fake-codex), and tokens written to <dir>/tokens.json."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = Path(os.environ.get("FIREDEV_E2E_DIR", ROOT / "e2e" / ".tmp"))
PORT = int(os.environ.get("FIREDEV_E2E_PORT", "8139"))

shutil.rmtree(DIR, ignore_errors=True)
proj = DIR / "proj"
(proj / "src").mkdir(parents=True)
(proj / "src" / "app.txt").write_text("broken\n")
(proj / "README.md").write_text("Fixture\n" + "".join(f"line {i}\n" for i in range(60)))
scripts = {
    "test.sh": """\
        mkdir -p reports
        if grep -q fixed src/app.txt; then f=""; code=0; else f='<failure message="app is broken"/>'; code=1; fi
        printf '<testsuite name="t"><testcase classname="app.AppTest" name="a"/><testcase classname="app.AppTest" name="b"/><testcase classname="app.AppTest" name="c">%s</testcase></testsuite>' "$f" > reports/TEST-app.xml
        echo "ran 3 tests"; exit $code""",
    "build.sh": """\
        if grep -q fixed src/app.txt; then echo "BUILD SUCCESSFUL in 2s"; mkdir -p out; head -c 2400000 /dev/zero > out/app.apk; exit 0; fi
        echo "e: file://$PWD/src/Main.kt:3:5 Unresolved reference 'x'."; echo "BUILD FAILED in 1s"; exit 1""",
    "deploy.sh": "echo Uploading; sleep 0.5; echo 'Health check passed: preview is serving abc123'; echo 'Preview live at http://127.0.0.1:9/'",
}
for name, body in scripts.items():
    (proj / name).write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body) + "\n")
    (proj / name).chmod(0o755)
(proj / ".gitignore").write_text("reports/\nout/\n")
g = ["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@t"]
subprocess.run(["git", "init", "-q", str(proj)], check=True)
subprocess.run([*g, "add", "-A"], check=True)
subprocess.run([*g, "commit", "-qm", "init"], check=True)

home = DIR / "home"
cfg = {
    "server": {"host": "127.0.0.1", "port": PORT, "data_dir": str(DIR / "data"), "alexa_skill_id": "amzn1.ask.skill.e2e"},
    "machines": {"local": {"label": "test-box", "host": "localhost", "user": os.environ.get("USER", "abdul"), "auth": "key",
                           "key_file": "~/.config/firedev/keys/firedev_ed25519"}},
    "projects": [{"id": "e2e", "name": "Fixture App", "machine": "local", "path": str(proj), "agent": "codex",
                  "tmux_session": f"firedev-e2e-{PORT}", "agent_options": {"binary": str(ROOT / "backend/tests/fixtures/fake-codex")},
                  "actions": {
                      "test": {"run": "./test.sh", "kind": "tests", "reports": "reports/*.xml", "title": "Run the tests"},
                      "build": {"run": "./build.sh", "kind": "build", "artifact": "out/app.apk", "title": "Build the app"},
                      "deploy_preview": {"run": "./deploy.sh", "kind": "deploy", "level": 2, "title": "Deploy the preview",
                                         "reason": "Publish the latest build to the preview server.", "risk": "Testers get the new build."},
                      "wipe": {"run": "rm -rf out reports", "kind": "command", "level": 3, "title": "Wipe build outputs",
                               "reason": "Start from a clean slate.", "risk": "Deletes every build output."},
                  }}],
}
if os.environ.get("FIREDEV_E2E_SCENARIO") == "preview":
    # Live preview: a static web project, and the preview gateway on its own port (its own origin).
    web = DIR / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text(textwrap.dedent("""\
        <!doctype html><html><head><meta charset="utf-8"><title>Fixture Site</title>
        <style>body{margin:0;font:20px sans-serif}header{display:flex;gap:30px;padding:20px}main{padding:40px}.tall{height:1600px}</style></head>
        <body><header><b>Fixture</b><a href="/about.html">About</a><a href="#more">More</a></header>
        <main><h1 id="h">Version one</h1><button id="b" onclick="this.textContent='Clicked'">Press me</button><div class="tall"></div></main></body></html>"""))
    (web / "about.html").write_text("<!doctype html><meta charset=utf-8><title>About us</title><h1>About page</h1>")
    subprocess.run(["git", "init", "-q", str(web)], check=True)
    cfg["server"]["preview_port"] = int(os.environ.get("FIREDEV_E2E_PREVIEW_PORT", "8136"))
    # An empty site the stand-in agent builds (its DEMO script): live preview and live code end to end.
    demo = DIR / "demo"
    demo.mkdir(parents=True)
    (demo / "README.md").write_text("Pulse\n")
    (demo / "src").mkdir()
    (demo / "src" / "app.txt").write_text("broken\n")
    subprocess.run(["git", "init", "-q", str(demo)], check=True)
    dg = ["git", "-C", str(demo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*dg, "add", "-A"], check=True)
    subprocess.run([*dg, "commit", "-qm", "init"], check=True)
    (demo / "test.sh").write_text("#!/usr/bin/env bash\necho 'ran 3 tests'\n")
    (demo / "test.sh").chmod(0o755)
    cfg["projects"] = [{"id": "web", "name": "Fixture Site", "machine": "local", "path": str(web), "agent": "shell",
                        "tmux_session": f"firedev-e2e-web-{PORT}"},
                       {"id": "demo", "name": "Pulse", "machine": "local", "path": str(demo), "agent": "codex",
                        "tmux_session": f"firedev-e2e-demo-{PORT}", "agent_options": {"binary": str(ROOT / "backend/tests/fixtures/fake-codex")},
                        "actions": {"test": {"run": "./test.sh", "kind": "tests", "title": "Run the tests"}}}]
    for t in (f"=firedev-e2e-web-{PORT}", f"=firedev-e2e-demo-{PORT}"):
        subprocess.run(["tmux", "kill-session", "-t", t], capture_output=True)
else:
    cfg["server"]["preview_port"] = 0
DIR.mkdir(parents=True, exist_ok=True)
import yaml  # noqa: E402

(DIR / "firedev.yaml").write_text(yaml.safe_dump(cfg))
os.environ["FIREDEV_HOME"] = str(home)
os.environ["FIREDEV_CONFIG"] = str(DIR / "firedev.yaml")
subprocess.run(["tmux", "kill-session", "-t", f"=firedev-e2e-{PORT}"], capture_output=True)
sys.path.insert(0, str(ROOT / "backend"))

from firedev import config  # noqa: E402
from firedev.app import create_app  # noqa: E402
from firedev.auth import DeviceStore  # noqa: E402

devices = DeviceStore(home)
tv, _ = devices.create("E2E TV", "tv")
voice, _ = devices.create("E2E phone", "voice")
(DIR / "tokens.json").write_text(json.dumps({"tv": tv, "voice": voice, "admin": (home / "admin.token").read_text().strip()}))

# Alexa: trust a throwaway CA instead of Amazon's, so the tests can sign real requests to /alexa
# (the key is in .tmp/alexa-key.pem; the certificate URL in requests is never fetched).
import datetime as dt  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

from firedev import app as app_module  # noqa: E402
from firedev.alexa_verify import AlexaVerifier  # noqa: E402


def _cert(subject, issuer, pub, signer, ca, extra):
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)])).public_key(pub)
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
         .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    for e in extra:
        b = b.add_extension(e, critical=False)
    return b.sign(signer, hashes.SHA256())


ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca_cert = _cert("E2E Root", "E2E Root", ca_key.public_key(), ca_key, True, [
    x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
                  key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False),
    x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())])
leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
leaf_cert = _cert("echo-api.amazon.com", "E2E Root", leaf_key.public_key(), ca_key, False, [
    x509.SubjectAlternativeName([x509.DNSName("echo-api.amazon.com")]),
    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
    x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key())])
(DIR / "alexa-key.pem").write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()))
leaf_pem = leaf_cert.public_bytes(serialization.Encoding.PEM)
app_module.AlexaVerifier = lambda skill_id: AlexaVerifier(skill_id, roots=[ca_cert], fetch=lambda url: leaf_pem)

import uvicorn  # noqa: E402

uvicorn.run(create_app(config.load(DIR / "firedev.yaml"), devices), host="127.0.0.1", port=PORT, log_level="warning")
