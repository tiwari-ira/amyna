"""Pure agent logic (no uagents imports). Entry points (contract, see CONTRACTS.md):

- visit_logic.handle_visit_evidence_request(tools, VisitEvidenceRequest, deps)
- claim_logic.handle_claim_analysis_request(tools, ClaimAnalysisRequest, deps)
- dispute_logic.handle_dispute_request(tools, DisputeRequest)
- orchestrate.ChatOrchestrator(tools).on_user_message / on_agent_response / on_timeouts

``tools`` is any ``CoreTools`` implementation (HTTPS CoreClient or Core's
InProcessTools), so the same code runs in uAgents, local_direct and Core.
"""
