"""Real uAgents wrappers in a local Bureau (no Agentverse, nothing published).

A scripted "patient" agent talks to the orchestrator over the actual chat
protocol; the orchestrator exchanges typed messages with the specialists, which
call a live Core over HTTP. Skipped when uagents is not installed.
"""

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("uagents")

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from uagents.dispatch import dispatcher  # noqa: E402
from uagents_core.contrib.protocols.chat import (  # noqa: E402
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity  # noqa: E402

from app.demo_support import (  # noqa: E402
    answer_open_questions,
    build_isolated_app,
    create_demo_visit,
    service_headers,
)
from claimshield_contracts.agent_messages import (  # noqa: E402
    VisitEvidenceRequest,
    VisitEvidenceResponse,
)
from claimshield_contracts.enums import ServiceName  # noqa: E402
from fetch.agents.local_direct import DEMO_CONVERSATION  # noqa: E402
from fetch.agents.orchestrator import HELLO_REPLY, build_orchestrator  # noqa: E402
from fetch.agents.run_all import build_bureau  # noqa: E402
from fetch.agents.settings import (  # noqa: E402
    OFFLINE_AGENTVERSE,
    MissingSeedError,
    require_seed,
)
from fetch.agents.visit_agent import build_visit_agent  # noqa: E402

from .conftest import SEEDS, agent_settings, free_port, serve  # noqa: E402

PROMPT_MARKERS = ("Reply", "reply **DONE**")


@pytest.fixture(autouse=True)
def no_agentverse_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """uAgents marks each agent active on Agentverse before running startup handlers,
    retrying 10x with backoff when offline. Skip that network call in tests."""

    async def skip(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("uagents.agent.update_agent_status", skip)


def _chat(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(UTC), msg_id=uuid4(), content=[TextContent(type="text", text=text)]
    )


@contextlib.contextmanager
def _bureau(build: Callable[[], list[Agent]], *, port: int | None = None) -> Iterator[None]:
    """Run a Bureau in its own thread + event loop (like ``bureau.run()``) for the duration
    of the block. The Bureau cancels every task on its loop at shutdown, so it must not
    share a loop with the test; agents are built inside that loop."""
    handles: dict[str, Any] = {}
    built: list[Agent] = []
    ready = threading.Event()

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        built.extend(build())
        bureau = Bureau(
            agents=built,
            port=port or free_port(),
            agentverse=OFFLINE_AGENTVERSE,
            shutdown_timeout=5,
            loop=loop,
        )
        handles["loop"], handles["task"] = loop, loop.create_task(bureau.run_async())
        ready.set()
        with contextlib.suppress(BaseException):
            loop.run_until_complete(handles["task"])

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(30), "bureau did not start"
    try:
        yield
    finally:
        handles["loop"].call_soon_threadsafe(handles["task"].cancel)
        thread.join(timeout=20)
        # Every Agent registers itself in uAgents' process-global dispatcher and never
        # unregisters; tests reuse seeds (same addresses), so stale agents would answer.
        for agent in built:
            dispatcher.unregister(agent.address, agent)


def _wait_for(condition: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return condition()


def _listening(port: int) -> bool:
    with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.2):
        return True
    return False


def _converse(
    build: Callable[[], tuple[list[Agent], str]],
    script: list[str],
    *,
    finished: Callable[[str], bool],
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """A patient agent sends the next scripted line after each orchestrator prompt."""
    state: dict[str, Any] = {"next": 1, "replies": [], "acks": 0, "done": False}

    def agents() -> list[Agent]:
        built, orchestrator_address = build()
        patient = Agent(
            name="test-patient",
            seed="claimshield-test-patient-seed-0001",
            agentverse=OFFLINE_AGENTVERSE,
        )
        chat = Protocol(spec=chat_protocol_spec)

        @chat.on_message(ChatMessage)
        async def on_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
            await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
            text = msg.text()
            state["replies"].append(text)
            if finished(text):
                state["done"] = True
            elif any(m in text for m in PROMPT_MARKERS) and state["next"] < len(script):
                await ctx.send(orchestrator_address, _chat(script[state["next"]]))
                state["next"] += 1

        @chat.on_message(ChatAcknowledgement)
        async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
            state["acks"] += 1

        patient.include(chat)

        @patient.on_event("startup")
        async def start(ctx: Context) -> None:
            await asyncio.sleep(0.5)
            await ctx.send(orchestrator_address, _chat(script[0]))

        return [*built, patient]

    with _bureau(agents):
        _wait_for(lambda: state["done"], timeout_s)
    return state


def test_missing_seed_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SEED", raising=False)
    with pytest.raises(MissingSeedError, match="ORCHESTRATOR_SEED is not set"):
        require_seed("ORCHESTRATOR_SEED")


def test_hello_world_mode(agent_env: None) -> None:
    def build() -> tuple[list[Agent], str]:
        orchestrator = build_orchestrator(
            agent_settings(hello_world=True),
            mailbox=False,
            publish=False,
            agentverse=OFFLINE_AGENTVERSE,
        )
        return [orchestrator], orchestrator.address

    state = _converse(build, ["hi"], finished=lambda t: t == HELLO_REPLY, timeout_s=20)
    assert state["done"], state["replies"]
    assert state["acks"] >= 1  # the orchestrator always acknowledges


def test_full_workflow_over_uagents(agent_env: None, live_core: dict[str, Any]) -> None:
    settings = agent_settings(core_base_url=live_core["base_url"])

    def build() -> tuple[list[Agent], str]:
        bureau = build_bureau(settings, online=False)
        agents = list(bureau._agents)  # noqa: SLF001 - reuse the configured agents
        orchestrator = next(a for a in agents if a.name == "amyna-orchestrator")
        return agents, orchestrator.address

    state = _converse(
        build, list(DEMO_CONVERSATION), finished=lambda t: "Resolved" in t, timeout_s=90
    )
    assert state["done"], state["replies"][-3:]
    case = httpx.get(
        f"{live_core['base_url']}/cases/{live_core['case_id']}", headers=live_core["relay"]
    ).json()
    assert case["state"] == "RESOLVED"
    assert {r["agent"] for r in case["agent_runs"]} >= {
        "visit_agent",
        "claim_agent",
        "dispute_agent",
    }


def test_core_drives_the_agents_in_agentverse_mode(agent_env: None, tmp_path: Path) -> None:
    """FETCH_MODE=agentverse: Core POSTs the orchestrator's REST /trigger, the orchestrator
    dispatches typed requests, and the specialists write results back via /internal/tools."""
    bureau_port = free_port()
    app = build_isolated_app(
        tmp_path / "agents.db",
        fetch_mode="agentverse",
        orchestrator_url=f"http://127.0.0.1:{bureau_port}",
    )
    relay = service_headers(app.state.container.settings, ServiceName.RELAY)
    with serve(app) as base_url:
        settings = agent_settings(core_base_url=base_url)

        def agents() -> list[Agent]:
            return list(build_bureau(settings, online=False)._agents)  # noqa: SLF001

        with _bureau(agents, port=bureau_port):
            assert _wait_for(lambda: _listening(bureau_port), 20), "orchestrator REST not up"
            http = httpx.Client(base_url=base_url, headers=relay, timeout=30)
            case_id = create_demo_visit(http, relay)["case_id"]

            def state() -> str:
                return http.get(f"/cases/{case_id}").json()["state"]

            assert _wait_for(lambda: state() == "EVIDENCE_EXTRACTED", 30), state()
            claim = http.post(
                "/claims",
                json={
                    "case_id": case_id,
                    "source": "finchnode",
                    "finchnode_claim_id": "CLM-2026-0915-001",
                },
            ).json()
            assert http.post(f"/claims/{claim['claim_id']}/analyze").json()["status"] == "pending"
            assert _wait_for(lambda: state() == "AWAITING_PATIENT_INPUT", 30), state()
            answer_open_questions(http, relay, case_id)
            assert http.post("/disputes", json={"case_id": case_id}).json()["status"] == "pending"
            assert _wait_for(lambda: state() == "AWAITING_APPROVAL", 30), state()
            case = http.get(f"/cases/{case_id}").json()
    assert len(case["findings"]) == 3 and len(case["dispute"]["items"]) == 3
    assert {r["agent"] for r in case["agent_runs"]} >= {
        "visit_agent",
        "claim_agent",
        "dispute_agent",
    }


def test_specialists_answer_only_the_orchestrator(
    agent_env: None, live_core: dict[str, Any]
) -> None:
    """Case data is PHI: a specialist ignores any sender but the orchestrator's address."""
    settings = agent_settings(core_base_url=live_core["base_url"])
    orchestrator_address = Identity.from_seed(SEEDS["ORCHESTRATOR_SEED"], 0).address
    answered: dict[str, list[str]] = {"orchestrator": [], "stranger": []}
    first_answer: list[float] = []

    def agents() -> list[Agent]:
        visit = build_visit_agent(
            settings,
            mailbox=False,
            agentverse=OFFLINE_AGENTVERSE,
            orchestrator_address=orchestrator_address,
        )
        callers = {
            "orchestrator": Agent(  # same seed, so same address as the real orchestrator
                name="orchestrator-stand-in",
                seed=SEEDS["ORCHESTRATOR_SEED"],
                agentverse=OFFLINE_AGENTVERSE,
            ),
            "stranger": Agent(
                name="stranger",
                seed="claimshield-test-stranger-seed-0001",
                agentverse=OFFLINE_AGENTVERSE,
            ),
        }
        for label, caller in callers.items():
            _ask_visit_agent(caller, label, visit.address, live_core["case_id"], answered)
        first_answer.append(0.0)
        return [visit, *callers.values()]

    def done() -> bool:
        if answered["orchestrator"] and first_answer[0] == 0.0:
            first_answer[0] = time.time()
        return bool(first_answer and first_answer[0]) and time.time() - first_answer[0] > 1.5

    with _bureau(agents):
        _wait_for(done, timeout_s=30)
    assert answered == {"orchestrator": ["req_orchestrator"], "stranger": []}


def _ask_visit_agent(
    caller: Agent, label: str, visit_address: str, case_id: str, answered: dict[str, list[str]]
) -> None:
    @caller.on_message(VisitEvidenceResponse)
    async def on_response(ctx: Context, sender: str, msg: VisitEvidenceResponse) -> None:
        answered[label].append(msg.request_id)

    @caller.on_event("startup")
    async def ask(ctx: Context) -> None:
        await asyncio.sleep(0.5)
        await ctx.send(
            visit_address, VisitEvidenceRequest(request_id=f"req_{label}", case_id=case_id)
        )


def test_print_addresses_never_prints_seeds(
    agent_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from fetch.agents.print_addresses import main

    main()
    out = capsys.readouterr().out
    assert "ORCHESTRATOR_ADDRESS=agent1" in out
    assert not any(seed in out for seed in SEEDS.values())
