"""FireDev configuration: machines you can SSH into and the projects that live on them.

The file is YAML (default ~/.config/firedev/firedev.yaml, or $FIREDEV_CONFIG). Secrets never live in
it: SSH keys are referenced by path, and a password, if you must use one, is read from an environment
variable named in the config.
"""

from __future__ import annotations

import getpass
import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(os.environ.get("FIREDEV_CONFIG", "~/.config/firedev/firedev.yaml")).expanduser()
DEFAULT_HOME = Path(os.environ.get("FIREDEV_HOME", "~/.config/firedev")).expanduser()

ACTION_KINDS = {"tests", "build", "deploy", "logs", "command"}
AGENTS = {"codex", "claude", "shell"}
AUTH_METHODS = {"key", "agent", "password"}
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class ConfigError(ValueError):
    pass


@dataclass
class Machine:
    id: str
    host: str
    user: str
    port: int = 22
    auth: str = "key"
    key_file: str | None = None
    password_env: str | None = None
    known_hosts: str | None = "~/.ssh/known_hosts"
    label: str | None = None
    local: bool = False  # this machine: FireDev runs commands directly, no SSH

    @property
    def display(self) -> str:
        return self.label or self.id


@dataclass
class Action:
    """A project command FireDev itself runs (outside the agent's sandbox), with a declared risk level."""

    id: str
    run: str
    title: str
    kind: str = "command"
    level: int = 0
    reason: str | None = None
    risk: str | None = None
    reports: str | None = None  # glob of JUnit XML files written by a test run
    artifact: str | None = None  # path of the build output to report


@dataclass
class Project:
    id: str
    name: str
    machine: Machine
    path: str
    agent: str = "codex"
    tmux_session: str = ""
    startup: str | None = None
    description: str | None = None
    aliases: list[str] = field(default_factory=list)
    actions: dict[str, Action] = field(default_factory=dict)
    agent_options: dict = field(default_factory=dict)
    # Web addresses this project's screens may show in a preview component (its preview server, for example).
    previews: list[str] = field(default_factory=list)
    # Live preview of the project's own dev server: command ("{port}" is filled in), port, path, dir, hmr, auto_open.
    preview: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tmux_session:
            self.tmux_session = f"firedev-{self.id}"


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8130
    data_dir: Path = DEFAULT_HOME / "data"
    public_url: str | None = None
    # Which non-TV clients may approve. "tv" means approvals only ever come from a paired TV remote.
    remote_approval: str = "tv"
    # The Alexa skill allowed to call /alexa (from the Alexa developer console). Unset: signed Alexa requests are refused.
    alexa_skill_id: str | None = None
    # The live-preview gateway: a separate origin that proxies dev servers to paired TVs. preview_url is its public
    # address (where the TV reaches it, e.g. a port the TV app forwards); unset, TVs use the server's host with preview_port.
    preview_port: int = 8132
    preview_url: str | None = None
    # LAN mode: also listen on the local network with TLS (lan_port for the API, lan_preview_port for previews) and
    # announce this machine with mDNS, so a TV on the same Wi-Fi finds it. Pairing codes are tied to the certificate.
    lan: bool = False
    lan_port: int = 8134
    lan_preview_port: int = 8135
    name: str | None = None  # how TVs list this machine (default: the host name)


@dataclass
class Config:
    settings: Settings
    machines: dict[str, Machine]
    projects: dict[str, Project]
    path: Path | None = None


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{where}: '{key}' is required")
    return d[key]


def parse(raw: dict, path: Path | None = None) -> Config:
    raw = raw or {}
    s = raw.get("server") or {}
    settings = Settings(
        host=str(s.get("host", "127.0.0.1")),
        port=int(s.get("port", 8130)),
        data_dir=Path(str(s.get("data_dir", DEFAULT_HOME / "data"))).expanduser(),
        public_url=s.get("public_url"),
        remote_approval=str(s.get("remote_approval", "tv")),
        alexa_skill_id=(str(s["alexa_skill_id"]).strip() or None) if s.get("alexa_skill_id") else None,
        preview_port=int(s.get("preview_port", 8132)),
        lan=bool(s.get("lan", False)),
        lan_port=int(s.get("lan_port", 8134)),
        lan_preview_port=int(s.get("lan_preview_port", 8135)),
        name=str(s["name"]) if s.get("name") else None,
        preview_url=(str(s["preview_url"]).rstrip("/") or None) if s.get("preview_url") else None,
    )
    if settings.remote_approval not in {"tv", "level2"}:
        raise ConfigError("server.remote_approval must be 'tv' or 'level2'")

    machines: dict[str, Machine] = {}
    for mid, m in (raw.get("machines") or {}).items():
        where = f"machines.{mid}"
        m = m or {}
        if m.get("local"):
            machines[mid] = Machine(id=mid, host="localhost", user=getpass.getuser(), label=m.get("label") or settings.name or socket.gethostname(),
                                    local=True, known_hosts=None)
            continue
        auth = str(m.get("auth", "key"))
        if auth not in AUTH_METHODS:
            raise ConfigError(f"{where}: auth must be one of {sorted(AUTH_METHODS)}")
        if auth == "password" and not m.get("password_env"):
            raise ConfigError(f"{where}: password auth reads the password from an environment variable; set password_env")
        if "password" in m:
            raise ConfigError(f"{where}: don't put passwords in the config file; use password_env or an SSH key")
        machines[mid] = Machine(
            id=mid,
            host=str(_require(m, "host", where)),
            user=str(_require(m, "user", where)),
            port=int(m.get("port", 22)),
            auth=auth,
            key_file=m.get("key_file"),
            password_env=m.get("password_env"),
            known_hosts=m.get("known_hosts", "~/.ssh/known_hosts"),
            label=m.get("label"),
        )

    projects: dict[str, Project] = {}
    for i, p in enumerate(raw.get("projects") or []):
        where = f"projects[{i}]"
        pid = str(_require(p, "id", where))
        if not _ID.match(pid):
            raise ConfigError(f"{where}: id must be lowercase letters, digits and dashes")
        if pid in projects:
            raise ConfigError(f"{where}: duplicate project id '{pid}'")
        mid = str(p.get("machine") or "local")
        if mid == "local" and mid not in machines:
            # The machine FireDev runs on, with no SSH: the local-first default.
            machines[mid] = Machine(id="local", host="localhost", user=getpass.getuser(), label=settings.name or socket.gethostname(), local=True,
                                    known_hosts=None)
        if mid not in machines:
            raise ConfigError(f"{where}: unknown machine '{mid}'")
        agent = str(p.get("agent", "codex"))
        if agent not in AGENTS:
            raise ConfigError(f"{where}: agent must be one of {sorted(AGENTS)}")
        actions = {}
        for aid, a in (p.get("actions") or {}).items():
            if isinstance(a, str):
                a = {"run": a}
            kind = str(a.get("kind", "command"))
            if kind not in ACTION_KINDS:
                raise ConfigError(f"{where}.actions.{aid}: kind must be one of {sorted(ACTION_KINDS)}")
            level = int(a.get("level", 0))
            if level not in (0, 1, 2, 3):
                raise ConfigError(f"{where}.actions.{aid}: level must be 0-3")
            actions[aid] = Action(
                id=aid,
                run=str(_require(a, "run", f"{where}.actions.{aid}")),
                title=str(a.get("title") or aid.replace("_", " ").capitalize()),
                kind=kind,
                level=level,
                reason=a.get("reason"),
                risk=a.get("risk"),
                reports=a.get("reports"),
                artifact=a.get("artifact"),
            )
        projects[pid] = Project(
            id=pid,
            name=str(_require(p, "name", where)),
            machine=machines[mid],
            path=str(_require(p, "path", where)),
            agent=agent,
            tmux_session=str(p.get("tmux_session") or ""),
            startup=p.get("startup"),
            description=p.get("description"),
            aliases=[str(x).lower() for x in p.get("aliases") or []],
            actions=actions,
            agent_options=dict(p.get("agent_options") or {}),
            previews=[str(x) for x in p.get("previews") or []],
            preview=_preview(p.get("preview"), where),
        )
    return Config(settings=settings, machines=machines, projects=projects, path=path)


def _preview(raw, where: str) -> dict:
    if raw in (None, False):
        return {} if raw is None else {"auto_open": "never"}
    if isinstance(raw, str):
        raw = {"command": raw}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.preview must be a command or a mapping")
    unknown = set(raw) - {"command", "port", "path", "dir", "hmr", "auto_open", "label", "framework", "install"}
    if unknown:
        raise ConfigError(f"{where}.preview: unknown key(s) {sorted(unknown)}")
    if "auto_open" in raw and raw["auto_open"] not in ("auto", "open", "offer", "never"):
        raise ConfigError(f"{where}.preview.auto_open must be auto, open, offer or never")
    if raw.get("port") is not None and not (1024 <= int(raw["port"]) <= 65535):
        raise ConfigError(f"{where}.preview.port must be 1024-65535")
    if raw.get("path") and not str(raw["path"]).startswith("/"):
        raise ConfigError(f"{where}.preview.path must start with /")
    if raw.get("dir") and (str(raw["dir"]).startswith("/") or ".." in str(raw["dir"]).split("/")):
        raise ConfigError(f"{where}.preview.dir must be a folder inside the project")
    return dict(raw)


def load(path: Path | str | None = None) -> Config:
    path = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not path.exists():
        raise ConfigError(f"No config at {path}. Copy config/firedev.example.yaml there and edit it.")
    with path.open() as f:
        return parse(yaml.safe_load(f), path)
