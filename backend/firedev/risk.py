"""Risk levels for commands. FireDev classifies every command itself; it never trusts a level the agent claims.

Level 0  read-only        runs automatically (ls, git status, reading files, tests)
Level 1  project edits    runs automatically, always shown in the activity feed
Level 2  system/deploy    needs an explicit approval on the TV
Level 3  destructive      needs a strong (held) confirmation on the TV, with the exact command shown

A compound command (a && b; c | d) takes the highest level of its parts. Unknown commands are level 1:
visible, never silent.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

LEVEL_NAMES = {0: "Read-only", 1: "Project edit", 2: "System change", 3: "Destructive"}


@dataclass(frozen=True)
class Risk:
    level: int
    label: str
    explanation: str

    def to_dict(self) -> dict:
        return {"level": self.level, "label": self.label, "name": LEVEL_NAMES[self.level], "explanation": self.explanation}


# (pattern on one simple command, level, label, explanation). First match wins within a level; the highest
# level across all rules wins overall.
_RULES: list[tuple[re.Pattern, int, str, str]] = []


def _rule(level: int, label: str, explanation: str, *patterns: str) -> None:
    for p in patterns:
        _RULES.append((re.compile(p), level, label, explanation))


# Level 3: destructive or security-sensitive.
_rule(3, "Recursive delete", "Permanently deletes files and folders. This cannot be undone.",
      r"^rm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b", r"^find\b.*\s-delete\b", r"^shred\b")
_rule(3, "Wipe disk", "Overwrites a disk or filesystem. Data on it is lost.", r"^(mkfs(\.\w+)?|wipefs|fdisk|parted)\b", r"^dd\b.*\bof=")
_rule(3, "Force push", "Rewrites history on the remote. Other people's work can be lost.",
      r"^git\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s\+\S)")
_rule(3, "Discard work", "Throws away uncommitted changes in the repository.",
      r"^git\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s+\.|restore\s+\.)", r"^git\s+branch\s+-D\b")
_rule(3, "Firewall change", "Changes which network traffic this machine accepts.",
      r"^(ufw|iptables|ip6tables|nft|firewall-cmd)\b")
_rule(3, "User or permission change", "Changes users, passwords or who can access files on this machine.",
      r"^(useradd|userdel|usermod|groupadd|groupdel|passwd|chpasswd|visudo)\b", r"^(chmod|chown|chgrp)\s+(-[a-zA-Z]*R|--recursive)\b",
      r"^chmod\b.*\b777\b")
_rule(3, "Destructive database command", "Deletes or empties database data.",
      r"(?i)\b(drop\s+(table|database|schema)|truncate\s+table|delete\s+from\s+\w+\s*;?\s*$)", r"(?i)\bflushall\b|\bdropdb\b")
_rule(3, "Power off", "Shuts down or restarts the whole machine.", r"^(shutdown|reboot|poweroff|halt)\b", r"^systemctl\s+(reboot|poweroff|halt)\b")
_rule(3, "Delete infrastructure", "Deletes running infrastructure.", r"^kubectl\s+delete\b", r"^docker\s+(system\s+prune|volume\s+rm|rm\s+-f)\b", r"^terraform\s+destroy\b")

# Level 2: changes outside the project, deployments, network side effects.
_rule(2, "Administrator command", "Runs with administrator (root) privileges.", r"^(sudo|doas|su)\b")
_rule(2, "Push to remote", "Publishes commits to a shared remote repository.", r"^git\s+push\b")
_rule(2, "Service restart", "Starts, stops or restarts a running service. Users may see a short interruption.",
      r"^systemctl\s+(start|stop|restart|reload|enable|disable|mask)\b", r"^service\s+\S+\s+(start|stop|restart|reload)\b",
      r"^(pm2|supervisorctl)\s+(start|stop|restart|reload|delete)\b")
_rule(2, "Package install", "Installs or removes software packages on the machine.",
      r"^(apt|apt-get|yum|dnf|apk|pacman|brew|snap)\s+(install|remove|purge|upgrade|add|del)\b",
      r"^(pip3?|pipx|uv\s+pip)\s+install\b", r"^npm\s+(install|i)\s+(-g|--global)\b", r"^(gem|cargo)\s+install\b")
_rule(2, "Deployment", "Deploys a build to a server that other people use.",
      r"^(\S*/)?deploy[\w.-]*(\s|$)", r"^(fly|vercel|netlify|firebase)\s+deploy\b", r"^(kubectl\s+apply|helm\s+(install|upgrade))\b",
      r"^docker\s+(compose\s+)?(up|push)\b", r"^docker-compose\s+up\b", r"^(npm|yarn|pnpm)\s+publish\b", r"^gradlew?\b.*\bpublish")
_rule(2, "Database migration", "Changes the database schema.",
      r"^(\S*/)?migrate\b", r"^(rails|rake|alembic|flyway|knex|sequelize|(npx\s+)?prisma|python3?\s+manage\.py|php\s+artisan)\b.*\bmigrat")
_rule(2, "Remote copy", "Copies files to or from another machine.", r"^(scp|rsync|sftp)\b")
_rule(2, "Scheduled job change", "Changes jobs that run automatically on this machine.", r"^crontab\b")
_rule(2, "Network request that changes data", "Sends data to another service.", r"^curl\b.*(\s-X\s*(POST|PUT|PATCH|DELETE)|\s(-d|--data\S*|-F|--form|-T|--upload-file)\s)")
_rule(2, "Stop processes", "Stops running programs.", r"^(kill|pkill|killall)\b")

# Level 1: edits inside the project.
_rule(1, "Edit files", "Changes files in the project.",
      r"^sed\s+(-[a-zA-Z]*i|--in-place)", r"^(mv|cp|mkdir|touch|tee|patch|ln|truncate|rm)\b", r"^perl\s+-[a-zA-Z]*i",
      r"^git\s+(add|commit|checkout|switch|stash|apply|merge|rebase|cherry-pick|restore|tag|mv|rm|am)\b",
      r"^(prettier\b.*--write|black|ruff\s+format|ktlint\b.*-F|gofmt\s+-w|cargo\s+fmt)\b", r"^apply_patch\b")
_rule(1, "Install project dependencies", "Downloads dependencies into the project.", r"^(npm|yarn|pnpm)\s+(install|i|add|ci)\b", r"^(pip3?|uv)\s+sync\b")

# Level 0: read-only and checks.
_READ_ONLY = re.compile(
    r"^(ls|ll|pwd|cat|less|head|tail|wc|stat|file|du|df|tree|find|rg|grep|egrep|fgrep|ag|awk|sed|cut|sort|uniq|diff|cmp|"
    r"echo|printf|true|false|test|\[|which|type|command|whoami|id|hostname|uname|date|env|printenv|ps|top|free|uptime|"
    r"jq|yq|xxd|od|basename|dirname|realpath|readlink|nl|column|tr|fold|sha\d*sum|md5sum|journalctl|tmux\s+(ls|list-\w+|capture-pane))\b"
)
_GIT_READ = re.compile(r"^git\s+(status|diff|log|show|branch|remote|rev-parse|ls-files|blame|describe|config\s+--get|grep|shortlog|stash\s+list)\b")
_CHECKS = re.compile(
    r"^((\./)?(gradlew?|mvnw?)\b.*\b(test|check|lint|build|assemble|compile|bundle)\w*"
    r"|(npm|yarn|pnpm)\s+(run\s+)?(test|build|lint|check)\b|(pytest|jest|vitest|tox)\b|python3?\s+-m\s+pytest\b"
    r"|(go|cargo|dotnet|swift|flutter)\s+(test|build|vet|check)\b|make(\s+(test|check|build|all))?\s*$)"
)
_FIREDEV = re.compile(r"^(\S*/)?firedev\s+(run|request|actions|status)\b")

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_REDIRECT = re.compile(r"(?<![0-9&])>{1,2}\s*(?!&|/dev/null)(\S+)")
_ENV_PREFIX = re.compile(r"^(\w+=\S*\s+)+")


def unwrap(command: str) -> str:
    """Strips the shell wrapper agents put around commands: /bin/bash -lc "..." → ..."""
    c = command.strip()
    for _ in range(3):
        m = re.match(r"^(?:/usr)?(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+(.*)$", c, re.S)
        if not m:
            break
        rest = m.group(1).strip()
        try:
            parts = shlex.split(rest)
            c = parts[0] if parts else rest
        except ValueError:
            c = rest.strip("'\"")
    return c


def _segments(command: str) -> list[str]:
    out = []
    for seg in _SPLIT.split(command):
        seg = seg.strip().lstrip("(").rstrip(")").strip()
        if not seg:
            continue
        seg = _ENV_PREFIX.sub("", seg)
        seg = re.sub(r"^(time|nohup|nice|timeout\s+\S+|exec|xargs(\s+-\S+)*)\s+", "", seg)
        out.append(seg)
    return out


def _classify_segment(seg: str) -> Risk:
    best: Risk | None = None
    for pattern, level, label, explanation in _RULES:
        if pattern.search(seg) and (best is None or level > best.level):
            best = Risk(level, label, explanation)
    # sudo wraps another command: the inner command can raise it further.
    m = re.match(r"^(sudo|doas)\s+(-\S+\s+)*(.*)$", seg)
    if m and m.group(3):
        inner = _classify_segment(m.group(3))
        if inner.level > 2:
            best = inner
    if best is not None:
        return best
    if _FIREDEV.match(seg):
        return Risk(0, "FireDev request", "Asks FireDev to run a project action; FireDev checks it again.")
    if _GIT_READ.match(seg) or _READ_ONLY.match(seg):
        return Risk(0, "Read-only", "Reads the project without changing it.")
    if _CHECKS.match(seg):
        return Risk(0, "Build or test", "Builds or tests the project.")
    return Risk(1, "Command", "Runs a command in the project.")


def classify(command: str) -> Risk:
    command = unwrap(command)
    result = Risk(0, "Read-only", "Reads the project without changing it.")
    for seg in _segments(command) or [command]:
        r = _classify_segment(seg)
        if r.level > result.level:
            result = r
    # Writing output into a file is an edit, unless it's already riskier.
    if result.level == 0 and _REDIRECT.search(command):
        result = Risk(1, "Edit files", "Writes command output into a file in the project.")
    return result


def needs_approval(level: int) -> bool:
    return level >= 2


def needs_strong_confirmation(level: int) -> bool:
    return level >= 3
