"""Orchestrator logic for the ASI:One chat (pure; no uagents imports).

Flow (decisions D5/D6): ids -> consent -> claim ingest -> VisitEvidenceRequest ->
ClaimAnalysisRequest -> findings summary -> one confirmation question at a time ->
"prepare a dispute?" -> DisputeRequest(prepare) -> APPROVE / REJECT ->
DisputeRequest(start_call) -> handoff (DONE) -> final report.

A case may have progressed through another interface (Relay) or an earlier chat;
``_resume`` always continues from Core's current state instead of assuming one.

Every inter-agent message is typed; chat text is rendered here from typed data.
The uAgent wrapper (orchestrator.py) and local_direct.py both drive this class.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from claimshield_contracts.agent_messages import (
    DISPUTE_ACTION_PREPARE,
    DISPUTE_ACTION_START_CALL,
    TRIGGER_CLAIM_ANALYSIS,
    TRIGGER_DISPUTE_PREPARE,
    TRIGGER_VISIT_EVIDENCE,
    ClaimAnalysisRequest,
    ClaimAnalysisResponse,
    DisputeRequest,
    DisputeResponse,
    OrchestratorTrigger,
    VisitEvidenceRequest,
    VisitEvidenceResponse,
)
from claimshield_contracts.enums import (
    AgentName,
    CaseState,
    Channel,
    ClaimSource,
    ConsentType,
    FindingStatus,
    HandoffChoice,
    HumanReason,
    PatientDecision,
)
from claimshield_contracts.ids import new_id
from claimshield_contracts.models import CaseDetail, Dispute, HumanIntervention
from claimshield_contracts.tool_api import (
    CoreToolError,
    CoreTools,
    FindOrCreateCaseRequest,
    GetCaseRequest,
    IngestClaimRequest,
    RecordConsentRequest,
    RecordPatientDecisionRequest,
    ResolveHumanInterventionRequest,
)

from ..intent import Intent, IntentKind, parse_intent
from ..session import ChatSession, PendingRequest, Stage

AgentMessage = VisitEvidenceRequest | ClaimAnalysisRequest | DisputeRequest
AgentResponse = VisitEvidenceResponse | ClaimAnalysisResponse | DisputeResponse

AGENT_LABELS = {
    AgentName.VISIT_AGENT.value: "Visit Agent",
    AgentName.CLAIM_AGENT.value: "Claim Agent",
    AgentName.DISPUTE_AGENT.value: "Dispute Agent",
}
DISPUTABLE = (FindingStatus.CONFIRMED, FindingStatus.NEEDS_CLARIFICATION)
CONSENT_TEXT = (
    "I allow Amyna to read my FinchNode health records (encounters, procedures, "
    "medications and claims) to review this bill."
)
WELCOME = (
    "Hi! I'm **Amyna**. I check medical bills against your health records and help fix "
    "billing mistakes.\n\nTell me what's going on in your own words -- for example "
    '*"this bill looks wrong"*. If you have them handy, I can also use your record '
    "code (it starts with `fn_pat_`) and the claim number from the bill (starts with "
    "`CLM-`) to look things up right away."
)
HELP_TEXT = (
    "Tell me about the bill in plain words, or send your record code (`fn_pat_...`) "
    "with the claim number (`CLM-...`) from the bill. Answer my questions with "
    "**YES / NO / NOT SURE**. Commands: STATUS, RESTART, HELP. Amyna never asks for "
    "your SSN, PINs or passwords."
)


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _patient_reply(text: str) -> str:
    """Quote the patient's own words as part of the stored consent/authorization."""
    return f'Patient replied in ASI:One: "{text.strip()[:200]}"'


def authorization_prompt(payer: str, total_cents: int) -> str:
    """Exact wording the patient approves; stored with the approval decision."""
    return (
        f"If you approve, you authorize Amyna to call {payer} on your behalf about "
        f"these {money(total_cents)} in charges. Amyna will never share your SSN, PINs "
        "or passwords; if the representative asks, the call pauses and you verify directly."
    )


@dataclass(frozen=True)
class Outbound:
    agent: AgentName
    message: AgentMessage


@dataclass
class Step:
    replies: list[str] = field(default_factory=list)
    outbound: list[Outbound] = field(default_factory=list)
    end_session: bool = False

    def say(self, text: str) -> "Step":
        self.replies.append(text)
        return self


def trigger_to_outbound(trigger: OrchestratorTrigger) -> Outbound:
    """Core REST trigger (FETCH_MODE=agentverse) -> typed specialist request."""
    request_id = new_id("request")
    if trigger.kind == TRIGGER_VISIT_EVIDENCE:
        return Outbound(
            AgentName.VISIT_AGENT,
            VisitEvidenceRequest(
                request_id=request_id, case_id=trigger.case_id, visit_id=trigger.visit_id
            ),
        )
    if trigger.kind == TRIGGER_CLAIM_ANALYSIS:
        return Outbound(
            AgentName.CLAIM_AGENT,
            ClaimAnalysisRequest(
                request_id=request_id, case_id=trigger.case_id, claim_id=trigger.claim_id
            ),
        )
    if trigger.kind == TRIGGER_DISPUTE_PREPARE:
        return Outbound(
            AgentName.DISPUTE_AGENT,
            DisputeRequest(
                request_id=request_id, case_id=trigger.case_id, action=DISPUTE_ACTION_PREPARE
            ),
        )
    raise ValueError(f"unknown trigger kind {trigger.kind!r}")


class ChatOrchestrator:
    def __init__(
        self, tools: CoreTools, *, timeout_s: float = 60.0, clock: Callable[[], float] = time.time
    ) -> None:
        self.tools = tools
        self.timeout_s = timeout_s
        self.clock = clock

    # ------------------------------------------------------------------ entry points

    def on_user_message(self, session: ChatSession, text: str) -> Step:
        intent = parse_intent(text)
        if intent.kind == IntentKind.RESTART:
            session.reset()
            return Step([WELCOME])
        if intent.kind == IntentKind.HELP:
            return Step([HELP_TEXT])
        try:
            if intent.kind == IntentKind.STATUS:
                return Step([self._status(session)])
            return self._dispatch(session, intent, text)
        except CoreToolError as exc:
            return self._failed(session, exc)

    def _dispatch(self, session: ChatSession, intent: Intent, text: str) -> Step:
        handlers = {
            Stage.NEW: self._collect_ids,
            Stage.AWAITING_IDS: self._collect_ids,
            Stage.AWAITING_CONSENT: self._consent,
            Stage.CONFIRMING: self._confirm,
            Stage.AWAITING_DISPUTE_OK: self._dispute_ok,
            Stage.AWAITING_APPROVAL: self._approval,
            Stage.CALLING: self._calling,
            Stage.AWAITING_HANDOFF: self._handoff,
            Stage.DONE: self._after_done,
            Stage.ERROR: self._after_error,
        }
        return handlers.get(session.stage, self._busy)(session, intent, text)

    def on_agent_response(self, session: ChatSession, response: AgentResponse) -> Step:
        pending = next((p for p in session.pending if p.request_id == response.request_id), None)
        if pending is None or response.case_id != session.case_id:
            return Step()  # unknown, late, or for another case: ignore
        session.pending.remove(pending)
        if not response.ok:
            session.stage = Stage.ERROR
            label = AGENT_LABELS.get(pending.agent, pending.agent)
            return Step([f"Sorry, the {label} could not finish: {response.error}. Reply RESTART."])
        try:
            if isinstance(response, VisitEvidenceResponse):
                return self._visit_done(session, response)
            if isinstance(response, ClaimAnalysisResponse):
                return self._present_findings(session, self._case(session), Step())
            if response.action == DISPUTE_ACTION_PREPARE:
                return self._dispute_ready(session, response)
            return self._call_update(session, response)
        except CoreToolError as exc:
            return self._failed(session, exc)

    def on_timeouts(self, session: ChatSession) -> Step:
        now = self.clock()
        expired = [p for p in session.pending if now - p.sent_at > self.timeout_s]
        if not expired:
            return Step()
        session.pending = [p for p in session.pending if p not in expired]
        session.stage = Stage.ERROR
        label = AGENT_LABELS.get(expired[0].agent, expired[0].agent)
        return Step(
            [f"Sorry, the {label} didn't answer in time. Reply STATUS, or RESTART to try again."]
        )

    # ------------------------------------------------------------------ helpers

    def _send(
        self, session: ChatSession, step: Step, agent: AgentName, message: AgentMessage
    ) -> None:
        session.pending.append(
            PendingRequest(
                request_id=message.request_id,
                agent=agent.value,
                kind=type(message).__name__,
                sent_at=self.clock(),
            )
        )
        step.outbound.append(Outbound(agent, message))

    def _case(self, session: ChatSession) -> CaseDetail:
        return self.tools.get_case(GetCaseRequest(case_id=session.case_id or ""))

    def _failed(self, session: ChatSession, exc: CoreToolError) -> Step:
        session.stage = Stage.ERROR
        return Step(
            [f"Sorry, Amyna Core said: {exc.message} ({exc.code}). Reply STATUS or RESTART."]
        )

    @staticmethod
    def _payer(case: CaseDetail) -> str:
        claim_payer = case.claim.payer_name if case.claim else None
        return case.patient.payer_name or claim_payer or "your insurer"

    @staticmethod
    def _open(case: CaseDetail, *, call: bool) -> list[HumanIntervention]:
        return [
            i
            for i in case.open_interventions()
            if (i.call_id is not None) == call
            and (call or i.reason == HumanReason.PATIENT_CONFIRMATION)
        ]

    # ------------------------------------------------------------------ stages: setup

    def _collect_ids(self, session: ChatSession, intent: Intent, text: str) -> Step:
        session.finchnode_patient_id = intent.patient_id or session.finchnode_patient_id
        session.external_claim_id = intent.claim_id or session.external_claim_id
        if not session.finchnode_patient_id:
            session.stage = Stage.AWAITING_IDS
            return Step([WELCOME])
        if not session.external_claim_id:
            session.stage = Stage.AWAITING_IDS
            return Step(
                [
                    "Thanks! Which claim should I review? "
                    "Send the claim number (e.g. `CLM-2026-0915-001`)."
                ]
            )
        found = self.tools.find_or_create_case(
            FindOrCreateCaseRequest(
                finchnode_patient_id=session.finchnode_patient_id,
                channel=Channel.ASI1,
                external_ref=session.external_claim_id,
            )
        )
        session.case_id = found.case.case_id
        case = self._case(session)
        if case.claim and case.claim.external_claim_id != session.external_claim_id:
            session.stage = Stage.DONE  # never review (or dispute) a different claim
            return Step(
                [
                    f"Your open Amyna case covers claim {case.claim.external_claim_id}, "
                    f"not {session.external_claim_id}. Reply RESTART and send that claim number."
                ]
            )
        if case.has_consent(ConsentType.HEALTH_DATA_ACCESS):
            return self._start_review(session, case, Step())
        session.stage = Stage.AWAITING_CONSENT
        return Step(
            [
                f"To review claim **{session.external_claim_id}** I need your permission to read "
                f"your FinchNode health records for this case:\n\n> {CONSENT_TEXT}\n\n"
                "Reply **YES** to consent or **NO** to stop."
            ]
        )

    def _consent(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if intent.answer == "YES":
            self.tools.record_consent(
                RecordConsentRequest(
                    case_id=session.case_id or "",
                    consent_type=ConsentType.HEALTH_DATA_ACCESS,
                    consent_text=f"{CONSENT_TEXT} {_patient_reply(text)}",
                    channel=Channel.ASI1,
                )
            )
            return self._start_review(
                session, self._case(session), Step(["Thank you, consent recorded."])
            )
        if intent.answer == "NO":
            session.stage = Stage.DONE
            return Step(
                ["Understood. I won't access your records. Reply RESTART any time."],
                end_session=True,
            )
        return Step(["Please reply **YES** to consent or **NO** to stop."])

    def _start_review(self, session: ChatSession, case: CaseDetail, step: Step) -> Step:
        if case.findings:  # analyzed already (another interface or an earlier chat)
            return self._present_findings(session, case, step)
        claim = case.claim or self.tools.ingest_claim(
            IngestClaimRequest(
                case_id=case.case_id,
                source=ClaimSource.FINCHNODE,
                finchnode_claim_id=session.external_claim_id,
            )
        )
        session.claim_id = claim.claim_id
        total = money(claim.total_billed_cents)
        summary = f"claim **{claim.external_claim_id}** ({len(claim.lines)} lines, {total})"
        if case.visit is not None:
            session.stage = Stage.GATHERING_EVIDENCE
            self._send(
                session,
                step,
                AgentName.VISIT_AGENT,
                VisitEvidenceRequest(
                    request_id=new_id("request"), case_id=case.case_id, visit_id=case.visit.visit_id
                ),
            )
            return step.say(f"Reviewing your visit recording and {summary} now...")
        session.stage = Stage.ANALYZING
        self._send(
            session,
            step,
            AgentName.CLAIM_AGENT,
            ClaimAnalysisRequest(
                request_id=new_id("request"), case_id=case.case_id, claim_id=claim.claim_id
            ),
        )
        return step.say(
            f"No visit recording on file; comparing {summary} with your health records..."
        )

    def _visit_done(self, session: ChatSession, response: VisitEvidenceResponse) -> Step:
        step = Step()
        documented = [e.description for e in response.evidence if not e.negated]
        if documented:
            step.say("From your visit recording: " + "; ".join(documented) + ".")
        session.stage = Stage.ANALYZING
        self._send(
            session,
            step,
            AgentName.CLAIM_AGENT,
            ClaimAnalysisRequest(
                request_id=new_id("request"),
                case_id=session.case_id or "",
                claim_id=session.claim_id,
            ),
        )
        return step

    # ------------------------------------------------------------------ stages: findings

    def _present_findings(self, session: ChatSession, case: CaseDetail, step: Step) -> Step:
        session.claim_id = case.claim.claim_id if case.claim else session.claim_id
        if not case.findings:
            session.stage = Stage.DONE
            step.end_session = True
            return step.say("Good news: I found no billing discrepancies on this claim.")
        claim = case.claim.external_claim_id if case.claim else "this claim"
        total = sum(f.amount_cents for f in case.findings)
        lines = [
            f"I found **{len(case.findings)} potential issue(s)** on {claim} "
            f"totaling **{money(total)}**:"
        ]
        lines += [
            f"{i}. {f.title} ({money(f.amount_cents)})" for i, f in enumerate(case.findings, 1)
        ]
        step.say("\n".join(lines))
        return self._resume(session, case, step)

    def _resume(self, session: ChatSession, case: CaseDetail, step: Step) -> Step:
        """Continue from Core's current state (never assume where the case is)."""
        questions = self._open(case, call=False)
        if questions:
            session.stage = Stage.CONFIRMING
            session.open_question_id = questions[0].intervention_id
            return step.say(f"**{questions[0].prompt}**\n\nReply YES, NO, or NOT SURE.")
        if case.state == CaseState.FINDINGS_CONFIRMED:
            items = [f for f in case.findings if f.status in DISPUTABLE]
            session.stage = Stage.AWAITING_DISPUTE_OK
            listing = "\n".join(f"- {f.title} ({money(f.amount_cents)})" for f in items)
            total = money(sum(f.amount_cents for f in items))
            return step.say(
                f"Items to dispute ({total}):\n{listing}\n\n"
                "Shall I prepare a dispute package? Reply YES or NO."
            )
        if case.state in (CaseState.DISPUTE_PREPARED, CaseState.AWAITING_APPROVAL) and case.dispute:
            session.dispute_id = case.dispute.dispute_id
            session.stage = Stage.AWAITING_APPROVAL
            return step.say(self._package_text(case.dispute, self._payer(case)))
        if case.state == CaseState.APPROVED and case.dispute:
            session.dispute_id = case.dispute.dispute_id
            session.stage = Stage.CALLING
            self._send(
                session,
                step,
                AgentName.DISPUTE_AGENT,
                DisputeRequest(
                    request_id=new_id("request"),
                    case_id=case.case_id,
                    action=DISPUTE_ACTION_START_CALL,
                    dispute_id=case.dispute.dispute_id,
                ),
            )
            return step.say(f"Your dispute is approved. Calling {self._payer(case)} now...")
        report = self._report(session, case)
        step.replies.extend(report.replies)
        step.end_session = report.end_session
        return step

    def _confirm(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if intent.kind != IntentKind.ANSWER:
            return Step(["Please answer YES, NO, or NOT SURE."])
        self.tools.resolve_human_intervention(
            ResolveHumanInterventionRequest(
                case_id=session.case_id or "",
                intervention_id=session.open_question_id or "",
                choice=intent.answer or "",
                channel=Channel.ASI1,
            )
        )
        session.open_question_id = None
        return self._resume(session, self._case(session), Step(["Got it."]))

    # ------------------------------------------------------------------ stages: dispute

    def _dispute_ok(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if intent.answer == "YES":
            session.stage = Stage.PREPARING_DISPUTE
            step = Step(["Preparing your dispute package..."])
            self._send(
                session,
                step,
                AgentName.DISPUTE_AGENT,
                DisputeRequest(
                    request_id=new_id("request"),
                    case_id=session.case_id or "",
                    action=DISPUTE_ACTION_PREPARE,
                ),
            )
            return step
        if intent.answer == "NO":
            session.stage = Stage.DONE
            return Step(
                ["OK, no dispute will be prepared. Reply RESTART any time."], end_session=True
            )
        return Step(["Reply YES to prepare the dispute package, or NO."])

    def _dispute_ready(self, session: ChatSession, response: DisputeResponse) -> Step:
        case = self._case(session)
        if case.dispute is None:
            return Step([self._status(session)])
        session.dispute_id = case.dispute.dispute_id
        session.stage = Stage.AWAITING_APPROVAL
        return Step([self._package_text(case.dispute, self._payer(case))])

    @staticmethod
    def _package_text(dispute: Dispute, payer: str) -> str:
        items = "\n".join(
            f"{i}. Line {item.line_number}: {item.summary} ({money(item.amount_cents)})"
            for i, item in enumerate(dispute.items, 1)
        )
        sources = sorted({c.title for item in dispute.items for c in item.policy_citations})
        references = f"\n\nPolicy references: {'; '.join(sources)}." if sources else ""
        return (
            f"**Your dispute package is ready**: {len(dispute.items)} item(s), "
            f"{money(dispute.total_disputed_cents)}.\n{items}{references}\n\n"
            f"{authorization_prompt(payer, dispute.total_disputed_cents)}\n\n"
            "Reply **APPROVE** or **REJECT**."
        )

    def _approval(self, session: ChatSession, intent: Intent, text: str) -> Step:
        case = self._case(session)
        total = case.dispute.total_disputed_cents if case.dispute else 0
        if intent.kind == IntentKind.APPROVE:
            payer = self._payer(case)
            self.tools.record_patient_decision(
                RecordPatientDecisionRequest(
                    case_id=case.case_id,
                    dispute_id=session.dispute_id or "",
                    decision=PatientDecision.APPROVE,
                    authorization_text=(
                        f"{authorization_prompt(payer, total)} {_patient_reply(text)}"
                    ),
                    authorize_call=True,
                    channel=Channel.ASI1,
                )
            )
            session.stage = Stage.CALLING
            step = Step([f"Approved, thank you. Calling {payer} now..."])
            self._send(
                session,
                step,
                AgentName.DISPUTE_AGENT,
                DisputeRequest(
                    request_id=new_id("request"),
                    case_id=case.case_id,
                    action=DISPUTE_ACTION_START_CALL,
                    dispute_id=session.dispute_id,
                ),
            )
            return step
        if intent.kind == IntentKind.REJECT:
            self.tools.record_patient_decision(
                RecordPatientDecisionRequest(
                    case_id=case.case_id,
                    dispute_id=session.dispute_id or "",
                    decision=PatientDecision.REJECT,
                    authorization_text="Patient rejected the dispute in the ASI:One chat.",
                    channel=Channel.ASI1,
                    reason="rejected in chat",
                )
            )
            session.stage = Stage.DONE
            return Step(["Understood. The dispute was not sent."], end_session=True)
        return Step(
            [
                "To authorize the dispute and the call, reply exactly **APPROVE**. "
                "Reply **REJECT** to stop."
            ]
        )

    # ------------------------------------------------------------------ stages: call

    def _calling(self, session: ChatSession, intent: Intent, text: str) -> Step:
        """Live calls run in the background: any message re-checks Core."""
        return self._report(session)

    def _call_update(self, session: ChatSession, response: DisputeResponse) -> Step:
        session.call_id = response.call_id
        if response.open_intervention_id:
            return self._ask_handoff(
                session, response.open_intervention_id, response.open_intervention_prompt
            )
        return self._report(session)

    def _ask_handoff(self, session: ChatSession, intervention_id: str, prompt: str | None) -> Step:
        session.stage = Stage.AWAITING_HANDOFF
        session.handoff_id = intervention_id
        return Step(
            [
                "I'm on the line with your insurer. "
                f"{prompt or 'The representative needs you.'}\n\n"
                "Amyna never shares SSNs, PINs or passwords, so **the call is paused**. "
                "Please verify directly with the representative (we also sent you a notification), "
                "then reply **DONE**, or CANCEL to end the call."
            ]
        )

    def _handoff(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if intent.kind not in (IntentKind.DONE, IntentKind.CANCEL):
            return Step(
                ["Reply exactly DONE once you've verified with the representative, or CANCEL."]
            )
        choice = HandoffChoice.DONE if intent.kind == IntentKind.DONE else HandoffChoice.CANCEL
        self.tools.resolve_human_intervention(
            ResolveHumanInterventionRequest(
                case_id=session.case_id or "",
                intervention_id=session.handoff_id or "",
                choice=choice.value,
                notes="Patient handled verification directly (ASI:One).",
                channel=Channel.ASI1,
            )
        )
        session.handoff_id = None
        return self._report(session)

    def _report(self, session: ChatSession, case: CaseDetail | None = None) -> Step:
        """Describe the call/outcome from Core's current state."""
        case = case or self._case(session)
        calls = self._open(case, call=True)
        if case.state == CaseState.HUMAN_REQUIRED and calls:
            return self._ask_handoff(session, calls[0].intervention_id, calls[0].prompt)
        if case.state in (CaseState.CALL_IN_PROGRESS, CaseState.APPROVED):
            session.stage = Stage.CALLING
            return Step(["The call is in progress. Send any message for an update."])
        if case.state not in (CaseState.RESOLVED, CaseState.REJECTED, CaseState.FAILED):
            return Step([self._status(session)])
        session.stage = Stage.DONE
        call = case.calls[-1] if case.calls else None
        if case.state == CaseState.REJECTED:
            return Step(["This dispute was rejected, so nothing was sent."], end_session=True)
        if case.state == CaseState.FAILED:
            reason = call.end_reason if call else "see STATUS"
            return Step([f"The case ended without a resolution ({reason})."], end_session=True)
        if call is None or call.insurer_response is None:
            outcome = (
                "Your dispute letter is approved; no call was authorized."
                if case.dispute
                else "Based on your answers there is nothing to dispute on this bill."
            )
            return Step([outcome], end_session=True)
        response = call.insurer_response
        lines = {line.line_id: line for line in (case.claim.lines if case.claim else [])}

        def describe(ids: list[str]) -> str:
            return " and ".join(
                f"{money(lines[i].billed_cents)} ({lines[i].description})"
                for i in ids
                if i in lines
            )

        parts = [f"**Resolved with {self._payer(case)}.**"]
        if response.removed_line_ids:
            parts.append(f"They will remove {describe(response.removed_line_ids)}.")
        if response.itemization_requested_line_ids:
            parts.append(
                "They will request an itemization for "
                f"{describe(response.itemization_requested_line_ids)}."
            )
        if response.reference_number:
            parts.append(f"Reference number: **{response.reference_number}**.")
        return Step([" ".join(parts)], end_session=True)

    # ------------------------------------------------------------------ misc

    def _status(self, session: ChatSession) -> str:
        if not session.case_id:
            return (
                "No bill under review yet. Send me your record code (`fn_pat_...`) and "
                "the claim number (`CLM-...`) from the bill, or just tell me what happened."
            )
        case = self._case(session)
        flagged = sum(f.amount_cents for f in case.findings)
        return (
            f"Case `{case.case_id}` is **{case.state.value}** "
            f"({len(case.findings)} finding(s), {money(flagged)} flagged)."
        )

    def _busy(self, session: ChatSession, intent: Intent, text: str) -> Step:
        doing = session.stage.value.replace("_", " ")
        return Step(
            [f"Still working on it ({doing}). I'll message you here. Reply STATUS any time."]
        )

    def _after_done(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if intent.kind in (IntentKind.REVIEW_BILL, IntentKind.GREETING):
            session.reset()
            return self._collect_ids(session, intent, text)
        return Step([f"{self._status(session)} Reply RESTART to review another bill."])

    def _after_error(self, session: ChatSession, intent: Intent, text: str) -> Step:
        if session.case_id and not session.pending:
            return self._resume(session, self._case(session), Step())
        return Step([f"{self._status(session)} Reply RESTART to start over."])
