import pytest

from firedev import mcp
from firedev import config as cfg
from firedev.auth import Client, DeviceStore
from firedev.service import FireDev

ASSISTANT = Client("as1", "Alexa", "assistant")


@pytest.fixture
def fd(tmp_path):
    raw = {"server": {"data_dir": str(tmp_path / "data")},
           "machines": {"m": {"host": "example.invalid", "user": "u", "auth": "agent"}},
           "projects": [{"id": "app", "name": "My Android App", "machine": "m", "path": "~/app",
                         "actions": {"deploy_preview": {"run": "./deploy.sh", "kind": "deploy", "level": 2}}}]}
    return FireDev(cfg.parse(raw), DeviceStore(tmp_path / "home"))


async def test_mcp_handshake_and_tools(fd):
    r = await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["result"]["serverInfo"]["name"] == "firedev"
    assert await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    r = await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in r["result"]["tools"]}
    assert {"open_project", "send_agent_task", "get_project_status", "get_current_task", "get_build_status",
            "approve_action", "reject_action", "cancel_task"} <= names


async def test_mcp_call_and_errors(fd):
    r = await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}})
    assert not r["result"]["isError"] and "My Android App" in r["result"]["content"][0]["text"]
    r = await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "approve_action", "arguments": {"approval": "x"}}})
    assert r["result"]["isError"]
    r = await mcp.handle(fd, ASSISTANT, {"jsonrpc": "2.0", "id": 5, "method": "nope"})
    assert r["error"]["code"] == -32601


async def test_assistant_cannot_approve_by_default(fd):
    import asyncio
    from firedev.service import Approval, CommandError
    a = Approval(id="ap1", project="app", task=None, command="./deploy.sh", title="Deploy", reason="r", risk="x", level=2, source="voice", agent="Codex")
    a.future = asyncio.get_running_loop().create_future()
    fd.approvals["ap1"] = a
    with pytest.raises(CommandError):
        fd.decide("ap1", True, ASSISTANT)
    fd.config.settings.remote_approval = "level2"
    fd.decide("ap1", True, ASSISTANT)
    assert a.future.result()[0] is True


async def test_tv_must_hold_for_level3(fd):
    import asyncio
    from firedev.service import Approval, CommandError
    a = Approval(id="ap3", project="app", task=None, command="rm -rf x", title="Wipe", reason="r", risk="x", level=3, source="voice", agent="Codex")
    a.future = asyncio.get_running_loop().create_future()
    fd.approvals["ap3"] = a
    tv = Client("tv", "TV", "tv")
    with pytest.raises(CommandError):
        fd.decide("ap3", True, tv)
    fd.decide("ap3", True, tv, held=True)
    assert a.future.result()[0] is True

