# Optional: FinchNode via MCP

> Optional. The scaffold uses the REST client in `client.py`. Only consider MCP if
> FinchNode publishes an MCP server **and** it adds something the REST API lacks.

**VERIFY against current docs** whether FinchNode offers an MCP server, which
transport it uses (stdio / SSE / streamable HTTP), and how authorization works.

If you adopt it, keep the architecture rules intact:

1. **Core stays the only FinchNode caller.** Wrap the MCP client in a new
   `finchnode/client.py` implementation of the `FinchNodeClient` protocol
   (`get_patient`, `search`). Do not give agents direct MCP access; they keep
   using Core's consent-gated tools (`get_patient_*`).
2. **Normalize the same way.** MCP tool results still go through
   `normalize.py`, so source `system/code/display` values are preserved.
3. **Same mode flag.** Add e.g. `FINCHNODE_MODE=mcp` in `core/app/config.py`
   and `build_client()`, keep `mock` the default, fail fast without credentials,
   and add the new VERIFY items to `docs/VERIFY_LIST.md`.
4. **Least privilege.** Expose only the read tools the Claim Agent needs
   (claims, encounters, procedures, medications) — no write tools.
