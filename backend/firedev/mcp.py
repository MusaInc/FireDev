"""A small MCP (Model Context Protocol) server over FireDev's command API.

Served as JSON-RPC over HTTP POST at /mcp (bearer token of an "assistant" device), and bridged to stdio by
`firedev mcp` for desktop assistants. Approving is subject to the same role rules as every other client:
by default an assistant can ask for things and reject them, but only the TV remote can approve.
"""

from __future__ import annotations

import json

from .auth import Client
from .service import CommandError, FireDev

PROTOCOL_VERSION = "2025-06-18"

_project = {"project": {"type": "string", "description": "Project id; defaults to the project open on the TV"}}

TOOLS = [
    {"name": "list_projects", "description": "List configured projects with connection, agent, build and test status.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "open_project", "description": "Connect to a project over SSH, attach its tmux session and start its coding agent.",
     "inputSchema": {"type": "object", "properties": _project, "required": ["project"]}},
    {"name": "send_agent_task", "description": "Give the project's coding agent (Codex or Claude Code) a development task in plain language.",
     "inputSchema": {"type": "object", "properties": {**_project, "task": {"type": "string"}}, "required": ["task"]}},
    {"name": "run_action", "description": "Run one of the project's declared actions (tests, build, deploy_preview, logs). Level 2+ actions wait for approval on the TV.",
     "inputSchema": {"type": "object", "properties": {**_project, "action": {"type": "string"}}, "required": ["action"]}},
    {"name": "get_project_status", "description": "Current task, agent state, build, tests, changes and pending approvals.",
     "inputSchema": {"type": "object", "properties": _project}},
    {"name": "get_current_task", "description": "The task the agent is working on, or the last one.",
     "inputSchema": {"type": "object", "properties": _project}},
    {"name": "get_build_status", "description": "Latest build, test and deployment results.",
     "inputSchema": {"type": "object", "properties": _project}},
    {"name": "approve_action", "description": "Approve a pending action. Usually refused: approvals belong to the TV remote.",
     "inputSchema": {"type": "object", "properties": {"approval": {"type": "string"}}}},
    {"name": "reject_action", "description": "Reject a pending action.",
     "inputSchema": {"type": "object", "properties": {"approval": {"type": "string"}}}},
    {"name": "cancel_task", "description": "Stop the agent's current task.",
     "inputSchema": {"type": "object", "properties": _project}},
    {"name": "voice_command", "description": "Send an utterance exactly as if the user had said it to FireDev.",
     "inputSchema": {"type": "object", "properties": {**_project, "text": {"type": "string"}}, "required": ["text"]}},
]
_TOOL_NAMES = {t["name"] for t in TOOLS}


def _result(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


async def handle(service: FireDev, client: Client, msg: dict) -> dict | None:
    rid = msg.get("id")
    method = msg.get("method", "")
    if rid is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(rid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "firedev", "version": "0.1.0"},
                             "instructions": "FireDev controls AI coding agents on the user's machines. The user watches on a Fire TV and approves risky actions there."})
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in _TOOL_NAMES:
            return _error(rid, -32602, f"Unknown tool {name}")
        args = params.get("arguments") or {}
        if name == "voice_command":
            args = {**args, "source": "assistant"}
        try:
            out = await service.execute(name, args, client)
            return _result(rid, {"content": [{"type": "text", "text": json.dumps(out, default=str)}], "isError": False})
        except CommandError as e:
            return _result(rid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
    return _error(rid, -32601, f"Method not found: {method}")
