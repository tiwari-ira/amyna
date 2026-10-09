"""Amyna shared contracts.

Modules:
    enums           wire-level enumerations
    models          Pydantic v2 domain models (Core responses, intelligence outputs)
    api             public HTTP request/response bodies
    tool_api        /internal/tools/* schemas + the CoreTools protocol
    agent_messages  uAgents messages (pydantic.v1 based, see ARCHITECTURE.md D1)
    ids             prefixed id helper
    demo            loader for the synthetic demo scenario
    version         CONTRACTS_VERSION / AGENT_PROTOCOL_VERSION
"""

from .version import AGENT_PROTOCOL_VERSION, CONTRACTS_VERSION

__all__ = ["AGENT_PROTOCOL_VERSION", "CONTRACTS_VERSION"]
