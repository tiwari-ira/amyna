"""Launch all four Amyna agents.

Two ways (document both, pick one):

1. Bureau (default) -- one process, specialists wired by address automatically,
   inter-agent messages dispatched locally. The orchestrator keeps its
   Agentverse mailbox so ASI:One can reach it; the Bureau serves its REST
   ``/trigger`` endpoint on port 8000.

       python -m fetch.agents.run_all

2. Separate processes -- one OS process per agent (ports 8000-8003). Every
   agent needs ``mailbox=True`` (default) and the orchestrator needs
   VISIT/CLAIM/DISPUTE_AGENT_ADDRESS in .env (``make agent-addresses``).

       python -m fetch.agents.run_all --processes

# VERIFY against current docs: Bureau + mailbox behavior, and whether separately
# running mailbox agents resolve each other through Agentverse without endpoints.
"""

import argparse
import subprocess
import sys

from uagents import Bureau
from uagents_core.identity import Identity

from claimshield_contracts.enums import AgentName

from .claim_agent import build_claim_agent
from .dispute_agent import build_dispute_agent
from .orchestrator import PORT, build_orchestrator
from .settings import OFFLINE_AGENTVERSE, AgentSettings, require_seed
from .visit_agent import build_visit_agent

MODULES = ("orchestrator", "visit_agent", "claim_agent", "dispute_agent")


def build_bureau(settings: AgentSettings, *, online: bool = True) -> Bureau:
    """All agents in one Bureau. ``online=False``: no mailbox, nothing published, no
    Agentverse/Almanac traffic at all (tests and fully offline demos)."""
    agentverse = None if online else OFFLINE_AGENTVERSE
    orchestrator_address = Identity.from_seed(require_seed("ORCHESTRATOR_SEED"), 0).address
    specialist = {
        "mailbox": False,
        "agentverse": agentverse,
        "orchestrator_address": orchestrator_address,
    }
    visit = build_visit_agent(settings, **specialist)
    claim = build_claim_agent(settings, **specialist)
    dispute = build_dispute_agent(settings, **specialist)
    orchestrator = build_orchestrator(
        settings,
        mailbox=online,
        publish=online,
        agentverse=agentverse,
        addresses={
            AgentName.VISIT_AGENT: visit.address,
            AgentName.CLAIM_AGENT: claim.address,
            AgentName.DISPUTE_AGENT: dispute.address,
        },
    )
    return Bureau(
        agents=[orchestrator, visit, claim, dispute],
        port=PORT,
        agentverse=agentverse,
        shutdown_timeout=60 if online else 5,
    )


def run_processes() -> int:
    procs = [subprocess.Popen([sys.executable, "-m", f"fetch.agents.{m}"]) for m in MODULES]
    try:
        return max(proc.wait() for proc in procs)
    except KeyboardInterrupt:
        for proc in procs:
            proc.terminate()
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Amyna Fetch agents")
    parser.add_argument("--processes", action="store_true", help="one OS process per agent")
    args = parser.parse_args()
    if args.processes:
        raise SystemExit(run_processes())
    build_bureau(AgentSettings.from_env()).run()


if __name__ == "__main__":
    main()
