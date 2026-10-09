"""Contract versions.

Bump rules (see docs/BRANCH_WORKFLOW.md):
- CONTRACTS_VERSION covers HTTP + domain models. Additive change = minor,
  breaking change = major.
- AGENT_PROTOCOL_VERSION covers uAgents messages. Any field change alters the
  schema digest, so every agent must be restarted on the same version.
"""

CONTRACTS_VERSION = "1.0.0"
AGENT_PROTOCOL_VERSION = "1.0.0"
