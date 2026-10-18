"""Generative TV interfaces: the versioned, declarative schema an agent uses to put a screen on the TV.

An agent never sends code to the TV. It sends a *surface*: a JSON tree of trusted components (cards, grids,
charts, diagrams, tables, diffs, terminals, choices...) that the TV renders with its own code. Everything that
comes from an agent is validated here first, strictly: unknown components, unknown properties, unsafe URLs,
oversized payloads and malformed actions are refused with messages the agent can act on. The TV validates
again before rendering and isolates each component, so one bad part never blanks the screen.

    {"schema": "firedev.ui/1", "title": "...", "subtitle": "...",
     "provenance": {"kind": "live|agent|mixed|example", "sources": ["..."], "note": "..."},
     "root": {"type": "grid", "columns": 3, "children": [...]}}

Values can be bound to FireDev's live data instead of copied ({"$bind": "project.tests.passed"}), so the TV keeps
them current as events arrive without the agent regenerating anything. Interactions are typed actions from a
fixed allowlist; anything sensitive still goes through FireDev's approvals.
"""

from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlparse

SCHEMA = "firedev.ui/1"
MAX_BYTES = 96 * 1024
MAX_NODES = 300
MAX_DEPTH = 10
MAX_TEXT = 1200
MAX_CODE = 12000

TONES = {"neutral", "good", "bad", "warn", "info", "accent"}
SIZES = {"s", "m", "l", "xl"}
ICONS = {"check", "cross", "agent", "terminal", "file", "code", "shield", "deploy", "wait", "android", "mic", "undo", "dot"}
BIND_ROOTS = {"project", "projects", "approvals", "agents", "history", "conversation"}
BIND_FORMATS = {"count", "percent", "duration", "bytes", "time", "text"}
NAV_TARGETS = {"home", "workspace", "activity", "changes", "terminal", "back", "surface", "preview"}
PROVENANCE = {"live", "agent", "mixed", "example"}
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
_BIND_PATH = re.compile(r"^[a-z_]+(\.[A-Za-z0-9_-]+|\[\d{1,3}\])*$")


class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:6]))
        self.errors = errors


# --- property checkers ------------------------------------------------------------------------------------

def _str(max_len=MAX_TEXT, bindable=True):
    def check(v, where, errs):
        if bindable and _is_bind(v):
            return _check_bind(v, where, errs)
        if not isinstance(v, str):
            errs.append(f"{where} must be text")
        elif len(v) > max_len:
            errs.append(f"{where} is too long ({len(v)} characters, at most {max_len})")
    return check


def _num(lo=None, hi=None, bindable=True):
    def check(v, where, errs):
        if bindable and _is_bind(v):
            return _check_bind(v, where, errs)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            errs.append(f"{where} must be a number")
        elif (lo is not None and v < lo) or (hi is not None and v > hi):
            errs.append(f"{where} must be between {lo} and {hi}")
    return check


def _scalar(max_len=200):
    def check(v, where, errs):
        if _is_bind(v):
            return _check_bind(v, where, errs)
        if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
            return
        if not isinstance(v, str):
            errs.append(f"{where} must be text or a number")
        elif len(v) > max_len:
            errs.append(f"{where} is too long (at most {max_len} characters)")
    return check


def _enum(options):
    def check(v, where, errs):
        if v not in options:
            errs.append(f"{where} must be one of {sorted(options)}")
    return check


def _bool(v, where, errs):
    if not isinstance(v, bool):
        errs.append(f"{where} must be true or false")


def _int(lo, hi):
    def check(v, where, errs):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            errs.append(f"{where} must be a whole number from {lo} to {hi}")
    return check


def _list(item, max_items, bindable=False):
    def check(v, where, errs):
        if bindable and _is_bind(v):
            return _check_bind(v, where, errs)
        if not isinstance(v, list):
            errs.append(f"{where} must be a list")
            return
        if len(v) > max_items:
            errs.append(f"{where} has {len(v)} items (at most {max_items})")
            return
        for i, x in enumerate(v):
            item(x, f"{where}[{i}]", errs)
    return check


def _obj(fields: dict, required=()):
    def check(v, where, errs):
        if not isinstance(v, dict):
            errs.append(f"{where} must be an object")
            return
        for k in required:
            if k not in v:
                errs.append(f"{where}.{k} is required")
        for k, x in v.items():
            if k not in fields:
                errs.append(f"{where}.{k} is not a known property (allowed: {', '.join(sorted(fields))})")
            else:
                fields[k](x, f"{where}.{k}", errs)
    return check


def _url(v, where, errs):
    if not isinstance(v, str) or len(v) > 600:
        errs.append(f"{where} must be a URL")
        return
    u = urlparse(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        errs.append(f"{where} must be an http(s) URL")


def _preview_url(v, where, errs):
    """"live" is this project's own running dev server, shown through FireDev's preview gateway."""
    if v == "live":
        return
    _url(v, where, errs)


def _point(v, where, errs):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return
    if isinstance(v, list) and len(v) == 2 and isinstance(v[1], (int, float)) and not isinstance(v[1], bool) \
            and (isinstance(v[0], (int, float, str)) and not isinstance(v[0], bool)):
        if isinstance(v[0], str) and len(v[0]) > 40:
            errs.append(f"{where} label is too long")
        return
    errs.append(f"{where} must be a number or [x, y]")


def _is_bind(v) -> bool:
    return isinstance(v, dict) and "$bind" in v


def _check_bind(v, where, errs):
    allowed = {"$bind", "x", "y", "fallback", "format", "filter"}
    for k in v:
        if k not in allowed:
            errs.append(f"{where}: binding key '{k}' is not allowed (allowed: {', '.join(sorted(allowed))})")
    path = v.get("$bind")
    if not isinstance(path, str) or not _BIND_PATH.match(path) or path.split(".")[0].split("[")[0] not in BIND_ROOTS:
        errs.append(f"{where}: $bind must be a data path starting with one of {sorted(BIND_ROOTS)}, e.g. project.tests.passed")
    for k in ("x", "y"):
        if k in v and (not isinstance(v[k], str) or not re.match(r"^[A-Za-z0-9_.]{1,40}$", v[k])):
            errs.append(f"{where}.{k} must be a field name")
    if "format" in v and v["format"] not in BIND_FORMATS:
        errs.append(f"{where}.format must be one of {sorted(BIND_FORMATS)}")
    if "filter" in v and (not isinstance(v["filter"], dict) or len(v["filter"]) > 4
                          or any(not isinstance(x, (str, int, float, bool)) for x in v["filter"].values())):
        errs.append(f"{where}.filter must map field names to plain values")
    if "fallback" in v and not isinstance(v["fallback"], (str, int, float)):
        errs.append(f"{where}.fallback must be text or a number")


# --- actions --------------------------------------------------------------------------------------------

ACTIONS = {
    # Sends text to the coding agent as if the user had said it (goes through intents like any voice command).
    "ask": {"text": _str(400, bindable=False)},
    "navigate": {"to": _enum(NAV_TARGETS)},
    "open_project": {"project": _str(60, bindable=False)},
    # A declared project action (tests, build, deploy...). Its risk level decides whether the TV asks first.
    "run_action": {"action": _str(60, bindable=False)},
    "open_diff": {"path": _str(400, bindable=False)},
    "open_output": {"ref": _str(80, bindable=False)},
    # Moves focus to FireDev's approval sheet. Generated UI can never approve or reject by itself.
    "open_approval": {"approval": _str(60, bindable=False)},
    "expand": {"target": _str(48, bindable=False)},
    "select_tab": {"target": _str(48, bindable=False), "tab": _int(0, 11)},
    "cancel_task": {},
    "open_preview": {"url": _preview_url},
}
ACTION_REQUIRED = {"ask": ("text",), "navigate": ("to",), "open_project": ("project",), "run_action": ("action",),
                   "open_diff": ("path",), "open_output": ("ref",), "expand": ("target",), "select_tab": ("target", "tab"),
                   "open_preview": ("url",)}


def _action(v, where, errs):
    if not isinstance(v, dict) or not isinstance(v.get("type"), str):
        errs.append(f"{where} must be an action like {{\"type\": \"ask\", \"text\": \"...\"}}")
        return
    t = v["type"]
    if t not in ACTIONS:
        errs.append(f"{where}.type '{t}' is not an allowed action ({', '.join(sorted(ACTIONS))})")
        return
    fields = dict(ACTIONS[t], type=lambda *_: None, label=_str(60, bindable=False))
    _obj(fields, ACTION_REQUIRED.get(t, ()))(v, where, errs)


# --- components -----------------------------------------------------------------------------------------

COMMON = {"id": None, "type": None, "size": _enum(SIZES), "example": _bool}
_item_fields = {"title": _scalar(200), "detail": _str(400), "badge": _scalar(40), "tone": _enum(TONES), "icon": _enum(ICONS),
                "time": _scalar(40), "status": _enum({"done", "running", "waiting", "failed", "todo"}), "action": _action,
                "value": _scalar(60)}

COMPONENTS: dict[str, dict] = {
    # layout
    "stack": {"children": True, "props": {"direction": _enum({"vertical", "horizontal"}), "gap": _enum({"s", "m", "l"}),
                                          "align": _enum({"start", "center", "stretch"})}},
    "grid": {"children": True, "props": {"columns": _int(1, 4), "gap": _enum({"s", "m", "l"})}},
    "tabs": {"children": "tab", "props": {"selected": _int(0, 11)}},
    "tab": {"children": True, "props": {"label": _str(40, bindable=False)}, "required": ("label",)},
    "section": {"children": True, "props": {"title": _str(120), "subtitle": _str(200)}},
    "card": {"children": True, "props": {"title": _scalar(120), "subtitle": _str(200), "badge": _scalar(40), "tone": _enum(TONES),
                                         "icon": _enum(ICONS), "action": _action, "expandable": _bool, "expanded": _bool,
                                         "detail": _str(MAX_TEXT)}},
    # content
    "text": {"props": {"text": _str(), "style": _enum({"display", "title", "heading", "body", "caption", "mono"}),
                       "tone": _enum(TONES), "max_lines": _int(1, 12)}, "required": ("text",)},
    "badge": {"props": {"text": _scalar(40), "tone": _enum(TONES), "icon": _enum(ICONS)}, "required": ("text",)},
    "button": {"props": {"label": _str(40, bindable=False), "action": _action, "tone": _enum(TONES), "icon": _enum(ICONS)},
               "required": ("label", "action")},
    "metric": {"props": {"label": _str(80), "value": _scalar(40), "unit": _str(16), "delta": _scalar(24), "tone": _enum(TONES),
                         "caption": _str(160), "trend": _list(_num(bindable=False), 60, bindable=True), "action": _action},
               "required": ("label", "value")},
    "progress": {"props": {"label": _str(120), "value": _num(0), "max": _num(0.0001), "indeterminate": _bool,
                           "tone": _enum(TONES), "caption": _str(160)}, "required": ("label",)},
    "timeline": {"props": {"items": _list(_obj(_item_fields), 40, bindable=True), "title": _str(120)}, "required": ("items",)},
    "list": {"props": {"items": _list(_obj(_item_fields), 60, bindable=True), "title": _str(120)}, "required": ("items",)},
    "table": {"props": {"title": _str(120),
                        "columns": _list(_obj({"key": _str(40, False), "label": _str(60, False), "align": _enum({"left", "right", "center"}),
                                               "format": _enum(BIND_FORMATS)}, ("key", "label")), 8),
                        "rows": _list(lambda v, w, e: None if isinstance(v, dict) and len(v) <= 12 else e.append(f"{w} must be an object with at most 12 fields"), 200, bindable=True),
                        "max_rows": _int(1, 30)},
              "required": ("columns", "rows")},
    "chart": {"props": {"kind": _enum({"line", "bar", "area", "pie"}), "title": _str(120), "unit": _str(16),
                        "x_label": _str(40), "y_label": _str(40), "labels": _list(_scalar(30), 60),
                        "series": _list(_obj({"name": _str(40, False), "points": _list(_point, 500, bindable=True),
                                              "tone": _enum(TONES)}, ("points",)), 6),
                        "views": _list(_enum({"line", "bar", "area", "pie"}), 4), "caption": _str(200)},
              "required": ("kind", "series")},
    "graph": {"props": {"title": _str(120), "direction": _enum({"right", "down"}),
                        "nodes": _list(_obj({"id": _str(48, False), "label": _str(60, False), "detail": _str(400, False),
                                             "group": _str(30, False), "tone": _enum(TONES), "icon": _enum(ICONS)}, ("id", "label")), 60),
                        "edges": _list(_obj({"from": _str(48, False), "to": _str(48, False), "label": _str(40, False),
                                             "style": _enum({"solid", "dashed"})}, ("from", "to")), 120)},
              "required": ("nodes",)},
    "code": {"props": {"title": _str(120), "language": _str(20, False), "code": _str(MAX_CODE, False),
                       "highlight": _list(_int(1, 100000), 40), "start_line": _int(1, 1000000)}, "required": ("code",)},
    # path: the working-tree diff of that file in this project, fetched and kept current by the TV. text: a unified diff.
    "diff": {"props": {"title": _str(120), "path": _str(400, False), "text": _str(MAX_CODE, False)}},
    # ref "last" follows the project's latest command live; a run id shows that run's output.
    "terminal": {"props": {"title": _str(120), "ref": _str(80, False), "text": _str(MAX_CODE, False), "lines": _int(4, 40)}},
    "image": {"props": {"src": _url, "alt": _str(200, False), "caption": _str(200)}, "required": ("src",)},
    "preview": {"props": {"url": _preview_url, "title": _str(120), "caption": _str(200)}, "required": ("url",)},
    "choice": {"props": {"prompt": _str(200), "options": _list(_obj({"label": _str(60, False), "value": _str(200, False),
                                                                      "detail": _str(200, False)}, ("label",)), 8)},
               "required": ("options",)},
    "form": {"props": {"title": _str(120), "submit": _str(40, False), "prompt": _str(300, False),
                       "fields": _list(_obj({"key": _str(40, False), "label": _str(80, False), "kind": _enum({"choice", "toggle", "number"}),
                                             "options": _list(_str(60, False), 8), "value": _scalar(60), "min": _num(bindable=False),
                                             "max": _num(bindable=False), "step": _num(bindable=False)}, ("key", "label", "kind")), 8)},
             "required": ("fields",)},
    # live: FireDev's coding agents and the agent processes on the machine (source "agents"), or this project's agent.
    "agent_status": {"props": {"title": _str(120), "source": _enum({"project", "agents"}), "filter": _str(60, False)}},
    # live: approvals waiting on the TV. Selecting one opens FireDev's approval sheet.
    "approvals": {"props": {"title": _str(120)}},
}
NEEDS_CHILD_CHECK = {"tabs"}


def _check_node(node, where: str, depth: int, state: dict, errs: list[str]) -> None:
    if not isinstance(node, dict):
        errs.append(f"{where} must be a component object")
        return
    state["nodes"] += 1
    if state["nodes"] > MAX_NODES:
        if state["nodes"] == MAX_NODES + 1:
            errs.append(f"too many components (at most {MAX_NODES})")
        return
    if depth > MAX_DEPTH:
        errs.append(f"{where} is nested too deeply (at most {MAX_DEPTH} levels)")
        return
    t = node.get("type")
    spec = COMPONENTS.get(t) if isinstance(t, str) else None
    if not spec:
        errs.append(f"{where}.type '{t}' is not a known component ({', '.join(sorted(COMPONENTS))})")
        return
    nid = node.get("id")
    if nid is not None:
        if not isinstance(nid, str) or not _ID.match(nid):
            errs.append(f"{where}.id must start with a letter and use letters, digits, _ or - (max 48)")
        elif nid in state["ids"]:
            errs.append(f"{where}.id '{nid}' is used twice")
        else:
            state["ids"].add(nid)
    for k in spec.get("required", ()):
        if k not in node:
            errs.append(f"{where} ({t}) needs '{k}'")
    props = spec["props"]
    kids = spec.get("children")
    for k, v in node.items():
        if k in ("id", "type"):
            continue
        if k == "children":
            if not kids:
                errs.append(f"{where} ({t}) can't have children")
            continue
        if k in COMMON:
            COMMON[k](v, f"{where}.{k}", errs)
        elif k in props:
            props[k](v, f"{where}.{k}", errs)
        else:
            errs.append(f"{where} ({t}): '{k}' is not a known property (allowed: {', '.join(sorted(set(props) | {'id', 'size', 'example'}))})")
    if kids:
        children = node.get("children", [])
        if not isinstance(children, list):
            errs.append(f"{where}.children must be a list")
            return
        if len(children) > 40:
            errs.append(f"{where} has {len(children)} children (at most 40)")
            return
        for i, c in enumerate(children):
            if kids == "tab" and (not isinstance(c, dict) or c.get("type") != "tab"):
                errs.append(f"{where}.children[{i}] must be a tab")
                continue
            _check_node(c, f"{where}.children[{i}]", depth + 1, state, errs)
    if t == "graph" and isinstance(node.get("nodes"), list):
        ids = {n.get("id") for n in node["nodes"] if isinstance(n, dict)}
        for i, e in enumerate(node.get("edges") or []):
            if isinstance(e, dict) and (e.get("from") not in ids or e.get("to") not in ids):
                errs.append(f"{where}.edges[{i}] joins a node that isn't in nodes")


def _assign_ids(node, counter: list[int], used: set[str]) -> None:
    if not isinstance(node, dict):
        return
    if not node.get("id"):
        while True:
            counter[0] += 1
            nid = f"n{counter[0]}"
            if nid not in used:
                break
        node["id"] = nid
        used.add(nid)
    for c in node.get("children") or []:
        _assign_ids(c, counter, used)


def _collect_ids(node, out: set[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            out.add(node["id"])
        for c in node.get("children") or []:
            _collect_ids(c, out)


def validate_surface(spec, preview_origins: list[str] | None = None) -> dict:
    """Returns a clean copy of the surface with every component given an id, or raises SpecError."""
    if isinstance(spec, (str, bytes)):
        if len(spec) > MAX_BYTES:
            raise SpecError([f"the surface is {len(spec)} bytes (at most {MAX_BYTES})"])
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise SpecError([f"not valid JSON: {e.msg} at line {e.lineno} column {e.colno}"]) from None
    if not isinstance(spec, dict):
        raise SpecError(["the surface must be a JSON object"])
    size = len(json.dumps(spec))
    if size > MAX_BYTES:
        raise SpecError([f"the surface is {size} bytes (at most {MAX_BYTES}); show fewer rows or points"])
    errs: list[str] = []
    allowed = {"schema", "title", "subtitle", "provenance", "root", "speak"}
    for k in spec:
        if k not in allowed:
            errs.append(f"'{k}' is not a known top-level field (allowed: {', '.join(sorted(allowed))})")
    if spec.get("schema") != SCHEMA:
        errs.append(f"schema must be \"{SCHEMA}\"")
    if not isinstance(spec.get("title"), str) or not spec["title"].strip() or len(spec["title"]) > 80:
        errs.append("title is required: a short heading for the screen (at most 80 characters)")
    if "subtitle" in spec and (not isinstance(spec["subtitle"], str) or len(spec["subtitle"]) > 160):
        errs.append("subtitle must be text (at most 160 characters)")
    if "speak" in spec and (not isinstance(spec["speak"], str) or len(spec["speak"]) > 300):
        errs.append("speak must be text (at most 300 characters)")
    _obj({"kind": _enum(PROVENANCE), "sources": _list(_str(80, False), 8), "note": _str(200, False)}, ("kind",))(
        spec.get("provenance"), "provenance", errs)
    state = {"nodes": 0, "ids": set()}
    if "root" not in spec:
        errs.append("root is required: the top component, usually a grid or stack")
    else:
        _check_node(spec["root"], "root", 1, state, errs)
    if not errs and preview_origins is not None:
        for where, url in _urls(spec["root"], "root"):
            if not origin_allowed(url, preview_origins):
                errs.append(f"{where}: {url} isn't an allowed preview address (allowed: {', '.join(preview_origins) or 'none configured'})")
    if errs:
        raise SpecError(errs)
    clean = copy.deepcopy(spec)
    _assign_ids(clean["root"], [0], set(state["ids"]))
    return clean


def _urls(node, where):
    if not isinstance(node, dict):
        return
    if node.get("type") in ("image", "preview"):
        yield f"{where}.{'src' if node['type'] == 'image' else 'url'}", node.get("src") or node.get("url")
    for k, v in node.items():
        if isinstance(v, dict) and v.get("type") == "open_preview":
            yield f"{where}.{k}", v.get("url")
        if k in ("items",) and isinstance(v, list):
            for i, it in enumerate(v):
                a = it.get("action") if isinstance(it, dict) else None
                if isinstance(a, dict) and a.get("type") == "open_preview":
                    yield f"{where}.items[{i}].action", a.get("url")
    for i, c in enumerate(node.get("children") or []):
        yield from _urls(c, f"{where}.children[{i}]")


def origin_allowed(url: str, origins: list[str]) -> bool:
    if url == "live":  # the gateway decides who sees the live preview, not the screen
        return True
    u = urlparse(url or "")
    here = f"{u.scheme}://{u.netloc}".lower()
    for o in origins:
        ou = urlparse(o)
        if f"{ou.scheme}://{ou.netloc}".lower() == here and (u.path or "/").startswith(ou.path or "/"):
            return True
    return False


# --- patches --------------------------------------------------------------------------------------------

PATCH_OPS = {"set", "replace", "append", "prepend", "remove", "meta"}


def find(node, nid: str, parent=None):
    """Returns (node, parent) for the component with that id, or (None, None)."""
    if not isinstance(node, dict):
        return None, None
    if node.get("id") == nid:
        return node, parent
    for c in node.get("children") or []:
        hit, par = find(c, nid, node)
        if hit is not None:
            return hit, par
    return None, None


def apply_patch(surface: dict, ops, preview_origins: list[str] | None = None) -> tuple[dict, list[str]]:
    """Applies patch operations to a copy of the surface and validates the result.
    Returns (new surface, ids of the components that changed; "*" for the header)."""
    if isinstance(ops, dict) and "ops" in ops:
        ops = ops["ops"]
    if not isinstance(ops, list) or not ops:
        raise SpecError(["a patch is a list of operations, e.g. [{\"op\": \"set\", \"id\": \"chart1\", \"props\": {\"size\": \"xl\"}}]"])
    if len(ops) > 40:
        raise SpecError(["at most 40 operations per patch"])
    s = copy.deepcopy(surface)
    changed: list[str] = []
    errs: list[str] = []
    for i, op in enumerate(ops):
        where = f"ops[{i}]"
        if not isinstance(op, dict) or op.get("op") not in PATCH_OPS:
            errs.append(f"{where}.op must be one of {sorted(PATCH_OPS)}")
            continue
        kind = op["op"]
        if kind == "meta":
            for k in ("title", "subtitle", "provenance", "speak"):
                if k in op:
                    s[k] = op[k]
            changed.append("*")
            continue
        nid = op.get("id")
        target, parent = find(s["root"], nid) if isinstance(nid, str) else (None, None)
        if target is None:
            errs.append(f"{where}: no component with id '{nid}'")
            continue
        if kind == "set":
            props = op.get("props")
            if not isinstance(props, dict) or not props or "children" in props or "type" in props or "id" in props:
                errs.append(f"{where}.props must be an object of properties to change (not type, id or children)")
                continue
            for k, v in props.items():
                if v is None:
                    target.pop(k, None)
                else:
                    target[k] = v
            changed.append(nid)
        elif kind == "replace":
            node = op.get("node")
            if not isinstance(node, dict):
                errs.append(f"{where}.node must be a component")
                continue
            node = dict(node)
            node.setdefault("id", nid)
            if parent is None:
                s["root"] = node
                changed.append("*root")
            else:
                parent["children"][parent["children"].index(target)] = node
                changed.append(parent["id"])
        elif kind in ("append", "prepend"):
            node = op.get("node")
            if not isinstance(node, dict):
                errs.append(f"{where}.node must be a component")
                continue
            if not COMPONENTS.get(target.get("type"), {}).get("children"):
                errs.append(f"{where}: '{nid}' ({target.get('type')}) can't have children")
                continue
            kids = target.setdefault("children", [])
            kids.insert(0, node) if kind == "prepend" else kids.append(node)
            changed.append(nid)
        elif kind == "remove":
            if parent is None:
                errs.append(f"{where}: can't remove the root; use replace")
                continue
            parent["children"].remove(target)
            changed.append(parent["id"])
    if errs:
        raise SpecError(errs)
    clean = validate_surface(s, preview_origins)
    return clean, sorted(set(changed))


# --- reading a surface back (for agents and voice edits) -------------------------------------------------

def walk(node, depth=0):
    if isinstance(node, dict):
        yield node, depth
        for c in node.get("children") or []:
            yield from walk(c, depth + 1)


def label_of(node: dict) -> str:
    for k in ("title", "label", "prompt", "text"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def outline(surface: dict, limit: int = 60) -> str:
    """A compact text outline of a surface: what an agent needs in order to patch it."""
    lines = [f"surface \"{surface.get('title', '')}\""]
    for node, depth in walk(surface.get("root")):
        if len(lines) > limit:
            lines.append("  …")
            break
        extra = []
        for k in ("size", "kind", "columns", "expanded", "selected"):
            if k in node:
                extra.append(f"{k}={node[k]}")
        lbl = label_of(node)
        lines.append("  " * (depth + 1) + f"- {node.get('type')} #{node.get('id')}" + (f" \"{lbl[:50]}\"" if lbl else "") +
                     (f" ({', '.join(extra)})" if extra else ""))
    return "\n".join(lines)


def binds(node) -> set[str]:
    """The data roots a surface binds to (to know which live sources it needs)."""
    out: set[str] = set()

    def visit(v):
        if isinstance(v, dict):
            if "$bind" in v and isinstance(v["$bind"], str):
                out.add(v["$bind"].split(".")[0].split("[")[0])
            if v.get("type") == "agent_status":
                out.add("agents" if v.get("source") == "agents" else "project")
            for x in v.values():
                visit(x)
        elif isinstance(v, list):
            for x in v:
                visit(x)
    visit(node)
    return out


# --- quick voice edits that need no AI turn ---------------------------------------------------------------

_SIZEABLE = {"card", "chart", "graph", "table", "code", "diff", "terminal", "metric", "preview", "image", "agent_status",
             "approvals", "list", "timeline", "section"}
_ORDER = ["s", "m", "l", "xl"]


def quick_edit(surface: dict, op: str, focused: str | None = None, target: str | None = None,
               kind: str | None = None) -> tuple[list[dict], str] | None:
    """Turns simple spoken edits ("make that chart bigger", "expand the Codex agent") into patch operations.
    Returns (ops, what was changed) or None if the request needs the agent."""
    root = surface.get("root")
    node = None
    if kind and not target:
        # "that chart": the focused chart, or the one around the focus, or the only chart on screen.
        n = find(root, focused)[0] if focused else None
        while n is not None and n.get("type") != kind:
            n = _parent_of(root, n.get("id"))
        if n is None:
            of_kind = [x for x, _ in walk(root) if x.get("type") == kind]
            n = of_kind[0] if len(of_kind) == 1 else None
        if n is None:
            return None
        focused = n["id"]
    if target:
        t = target.lower().strip()
        best = None
        for n, _ in walk(root):
            if n.get("type") in ("text", "badge"):
                continue
            lbl = label_of(n).lower()
            if lbl and (t in lbl or all(w in lbl for w in t.split())):
                if op in ("expand", "collapse") and not (n.get("type") == "card" and (n.get("expandable") or n.get("detail") or n.get("children"))):
                    continue
                if op in ("bigger", "smaller") and n.get("type") not in _SIZEABLE:
                    continue
                best = n
                break
        node = best
    if node is None and not target and focused:
        node, _ = find(root, focused)
        # "that" is the focused component or the nearest container of the right kind around it.
        while node is not None and ((op in ("bigger", "smaller") and node.get("type") not in _SIZEABLE)
                                    or (op in ("expand", "collapse") and node.get("type") != "card")):
            node = _parent_of(root, node.get("id"))
    if node is None:
        return None
    if op in ("bigger", "smaller") and node.get("type") not in _SIZEABLE:
        return None
    if op in ("bigger", "smaller"):
        cur = node.get("size", "m")
        i = _ORDER.index(cur) + (1 if op == "bigger" else -1)
        if i < 0 or i >= len(_ORDER):
            return [], f"That's already as {'big' if op == 'bigger' else 'small'} as it gets."
        return [{"op": "set", "id": node["id"], "props": {"size": _ORDER[i]}}], f"Made {label_of(node) or 'that'} {'bigger' if op == 'bigger' else 'smaller'}."
    if op in ("expand", "collapse"):
        return [{"op": "set", "id": node["id"], "props": {"expanded": op == "expand"}}], f"{'Expanded' if op == 'expand' else 'Collapsed'} {label_of(node) or 'that'}."
    return None


def _parent_of(root, nid):
    _, parent = find(root, nid)
    return parent
