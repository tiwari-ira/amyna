"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-03 21:57:43.100789
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=True),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("request_type", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_agent_runs_case_id"), ["case_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=True),
        sa.Column("resource_type", sa.String(length=40), nullable=True),
        sa.Column("resource_id", sa.String(length=40), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_events_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_events_case_id"), ["case_id"], unique=False)

    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("finchnode_patient_id", sa.String(length=80), nullable=False),
        sa.Column("patient_display_name", sa.String(length=200), nullable=True),
        sa.Column("member_ref", sa.String(length=80), nullable=True),
        sa.Column("payer_name", sa.String(length=200), nullable=True),
        sa.Column("payer_phone", sa.String(length=32), nullable=True),
        sa.Column("patient_state", sa.String(length=2), nullable=True),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_cases_finchnode_patient_id"), ["finchnode_patient_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_cases_state"), ["state"], unique=False)

    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("external_claim_id", sa.String(length=80), nullable=False),
        sa.Column("payer_name", sa.String(length=200), nullable=True),
        sa.Column("provider_name", sa.String(length=200), nullable=True),
        sa.Column("facility_name", sa.String(length=200), nullable=True),
        sa.Column("date_of_service", sa.Date(), nullable=False),
        sa.Column("total_billed_cents", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_claims_case_id"), ["case_id"], unique=True)
        batch_op.create_index(
            batch_op.f("ix_claims_external_claim_id"), ["external_claim_id"], unique=False
        )

    op.create_table(
        "consents",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("consent_type", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("consents", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_consents_case_id"), ["case_id"], unique=False)

    op.create_table(
        "human_decisions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("choice", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("authorization_text", sa.Text(), nullable=True),
        sa.Column("authorize_call", sa.Boolean(), nullable=False),
        sa.Column("intervention_id", sa.String(length=40), nullable=True),
        sa.Column("dispute_id", sa.String(length=40), nullable=True),
        sa.Column("finding_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("human_decisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_human_decisions_case_id"), ["case_id"], unique=False)

    op.create_table(
        "human_interventions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=40), nullable=True),
        sa.Column("finding_id", sa.String(length=40), nullable=True),
        sa.Column("decision_id", sa.String(length=40), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("human_interventions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_human_interventions_call_id"), ["call_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_human_interventions_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_human_interventions_status"), ["status"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("buttons", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("intervention_id", sa.String(length=40), nullable=True),
        sa.Column("dispute_id", sa.String(length=40), nullable=True),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("delivery_ref", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_case_id"), ["case_id"], unique=False)

    op.create_table(
        "claim_lines",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("claim_id", sa.String(length=40), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("code_system", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("code_display", sa.String(length=300), nullable=True),
        sa.Column("code_source_uri", sa.String(length=300), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("date_of_service", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("billed_cents", sa.Integer(), nullable=False),
        sa.Column("modifiers", sa.JSON(), nullable=False),
        sa.Column("revenue_code", sa.String(length=8), nullable=True),
        sa.Column("ndc", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("claim_lines", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_claim_lines_claim_id"), ["claim_id"], unique=False)

    op.create_table(
        "disputes",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("claim_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("total_disputed_cents", sa.Integer(), nullable=False),
        sa.Column("letter_text", sa.Text(), nullable=False),
        sa.Column("patient_authorization_id", sa.String(length=40), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("disputes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_disputes_case_id"), ["case_id"], unique=True)

    op.create_table(
        "visits",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("provider_name", sa.String(length=200), nullable=True),
        sa.Column("facility_name", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_ref", sa.String(length=500), nullable=True),
        sa.Column("recording_consent_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("transcript_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["recording_consent_id"],
            ["consents.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("visits", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_visits_case_id"), ["case_id"], unique=True)

    op.create_table(
        "call_sessions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("dispute_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_call_ref", sa.String(length=120), nullable=True),
        sa.Column("to_number_masked", sa.String(length=32), nullable=True),
        sa.Column("turns", sa.JSON(), nullable=False),
        sa.Column("insurer_response", sa.JSON(), nullable=True),
        sa.Column("open_intervention_id", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["dispute_id"],
            ["disputes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("call_sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_call_sessions_case_id"), ["case_id"], unique=False)

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("visit_id", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("concept", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("codes", sa.JSON(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("segment_ids", sa.JSON(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("negated", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extractor", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("evidence_items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_evidence_items_case_id"), ["case_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_evidence_items_visit_id"), ["visit_id"], unique=False)

    op.create_table(
        "findings",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("claim_id", sa.String(length=40), nullable=False),
        sa.Column("claim_line_id", sa.String(length=40), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("issue_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("human_confirmation_required", sa.Boolean(), nullable=False),
        sa.Column("confirmation", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("recommended_action", sa.String(length=32), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("policy_citations", sa.JSON(), nullable=False),
        sa.Column("patient_answer", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
        ),
        sa.ForeignKeyConstraint(
            ["claim_line_id"],
            ["claim_lines.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_findings_case_id"), ["case_id"], unique=False)

    op.create_table(
        "line_assessments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("claim_line_id", sa.String(length=40), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("concept", sa.String(length=80), nullable=True),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["claim_line_id"],
            ["claim_lines.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("line_assessments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_line_assessments_case_id"), ["case_id"], unique=False)

    op.create_table(
        "transcripts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("visit_id", sa.String(length=40), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("segments", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transcripts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_transcripts_visit_id"), ["visit_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("transcripts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transcripts_visit_id"))

    op.drop_table("transcripts")
    with op.batch_alter_table("line_assessments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_line_assessments_case_id"))

    op.drop_table("line_assessments")
    with op.batch_alter_table("findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_findings_case_id"))

    op.drop_table("findings")
    with op.batch_alter_table("evidence_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_evidence_items_visit_id"))
        batch_op.drop_index(batch_op.f("ix_evidence_items_case_id"))

    op.drop_table("evidence_items")
    with op.batch_alter_table("call_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_call_sessions_case_id"))

    op.drop_table("call_sessions")
    with op.batch_alter_table("visits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_visits_case_id"))

    op.drop_table("visits")
    with op.batch_alter_table("disputes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_disputes_case_id"))

    op.drop_table("disputes")
    with op.batch_alter_table("claim_lines", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_claim_lines_claim_id"))

    op.drop_table("claim_lines")
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_case_id"))

    op.drop_table("notifications")
    with op.batch_alter_table("human_interventions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_human_interventions_status"))
        batch_op.drop_index(batch_op.f("ix_human_interventions_case_id"))
        batch_op.drop_index(batch_op.f("ix_human_interventions_call_id"))

    op.drop_table("human_interventions")
    with op.batch_alter_table("human_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_human_decisions_case_id"))

    op.drop_table("human_decisions")
    with op.batch_alter_table("consents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_consents_case_id"))

    op.drop_table("consents")
    with op.batch_alter_table("claims", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_claims_external_claim_id"))
        batch_op.drop_index(batch_op.f("ix_claims_case_id"))

    op.drop_table("claims")
    op.drop_table("webhook_events")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_state"))
        batch_op.drop_index(batch_op.f("ix_cases_finchnode_patient_id"))

    op.drop_table("cases")
    with op.batch_alter_table("audit_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_events_case_id"))
        batch_op.drop_index(batch_op.f("ix_audit_events_action"))

    op.drop_table("audit_events")
    with op.batch_alter_table("agent_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_agent_runs_case_id"))

    op.drop_table("agent_runs")
