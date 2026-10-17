from .base import AgentAdapter, TurnParser
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .shell import ShellAdapter

ADAPTERS = {a.id: a for a in (CodexAdapter, ClaudeAdapter, ShellAdapter)}


def make_adapter(agent_id: str, options: dict | None = None) -> AgentAdapter:
    return ADAPTERS[agent_id](options)


__all__ = ["AgentAdapter", "TurnParser", "make_adapter", "ADAPTERS"]
